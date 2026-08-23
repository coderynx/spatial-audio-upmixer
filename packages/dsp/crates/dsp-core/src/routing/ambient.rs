//! Per-stem primary/ambient split, and the tilt that decides where the
//! ambient half goes.
//!
//! Reference design: Avendaño & Jot, "Frequency Domain Techniques for Stereo
//! to Multichannel Upmix" (AES 22nd) — a short-time inter-channel coherence
//! index mapped through a smooth non-linear function into a per-bin gain. A
//! separated stem is not a dry signal: it carries the room, plate and delay
//! its source was mixed with, and that part of it belongs around the listener
//! rather than in front.
//!
//! Two details from the paper are load-bearing:
//!
//! - the mask floor must stay above zero, or the output gets the
//!   musical-noise artifacts of spectral subtraction;
//! - coherence alone is not enough. A hard-panned primary reads as incoherent
//!   for the same reason ambience does, so the mask is multiplied by an
//!   equal-energy factor that only opens where both channels carry
//!   comparable energy.
//!
//! The split reads *ahead* of the block it is asked for rather than delaying
//! its output: a stem is resident in memory in both the preview and the
//! export, so the frames covering a block can be computed when the block is
//! asked for. Frames are scheduled at absolute sample positions, which is
//! what keeps the output independent of the block size it was rendered in.

use rustfft::num_complex::Complex64;

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::fft::RealFft;
use crate::kernels::stft::hann_periodic;

/// Analysis length. 21 ms at 48 kHz: long enough to resolve a reverb tail
/// from the note that caused it, short enough to keep the look-ahead the
/// engine has to hold in front of the playhead small.
pub const AMBIENT_FFT_SIZE: usize = 1024;

/// Overlap factor. Four gives the constant-overlap-add sum a square-root Hann
/// pair needs for the mask to be re-weighted every hop without modulation.
pub const AMBIENT_OVERLAP: usize = 4;

/// Mask floor `µ0`. Deliberately not zero — see the module note.
pub const AMBIENCE_FLOOR: f64 = 0.1;

/// Ambience-index threshold `Φ0`, the coherence the mask crosses at.
pub const AMBIENCE_THRESHOLD: f64 = 0.5;

/// Mask slope `σ`.
pub const AMBIENCE_SLOPE: f64 = 4.0;

/// One-pole smoothing of the cross- and auto-spectra, per frame — about
/// 53 ms of history at the default size. Slower than this passes more of a
/// diffuse field (an independent pair reads 0.76 of its power through at this
/// setting, 0.88 at half of it) but holds the ambient gain open across a note
/// boundary; faster loses diffuse level to the variance of its own estimate.
pub const COHERENCE_SMOOTHING: f64 = 0.1;

/// Where the ambient half splits between the rear and height sends. Elevation
/// perception keys on the 6-9 kHz spectral cues (Blauert's directional
/// bands), so heights take the bright half; a crossover this low still leaves
/// the rears the body of a reverb tail, which is where its energy is.
pub const AMBIENT_TILT_HZ: f64 = 2000.0;

const EPS: f64 = 1e-20;

/// Resolution of the tabulated mask. The curve is a fixed function of the
/// ambience index and is evaluated once per bin per frame, where a `tanh`
/// call each time is the single most expensive thing the stage does; at this
/// many points, linear interpolation is within 1e-6 of the closed form.
const MASK_TABLE_LEN: usize = 1024;

/// One block of ambient signal, already split into its two destinations.
pub struct AmbientBlock<'a> {
    pub rear: [&'a [f64]; 2],
    pub height: [&'a [f64]; 2],
}

pub struct AmbientSplit {
    fft: RealFft,
    n: usize,
    hop: usize,
    window: Vec<f64>,
    /// Overlap-add normalization per hop phase.
    cola: Vec<f64>,
    phi_lr: Vec<Complex64>,
    phi_ll: Vec<f64>,
    phi_rr: Vec<f64>,
    masked: [Vec<Complex64>; 2],
    primed: bool,
    next_frame: usize,
    ola: [Vec<f64>; 2],
    ring: usize,
    lp: [SosFilter; 2],
    hp: [SosFilter; 2],
    rear: [Vec<f64>; 2],
    height: [Vec<f64>; 2],
    cursor: Option<usize>,
    mask_table: Vec<f64>,
    frame: Vec<f64>,
    scratch: Vec<f64>,
    spectrum: [Vec<Complex64>; 2],
}

impl AmbientSplit {
    pub fn new(sample_rate: u32) -> Self {
        let n = AMBIENT_FFT_SIZE;
        let hop = n / AMBIENT_OVERLAP;
        let window: Vec<f64> = hann_periodic(n).into_iter().map(f64::sqrt).collect();
        let mut cola = vec![0.0; hop];
        for (i, w) in window.iter().enumerate() {
            cola[i % hop] += w * w;
        }
        // Both are powers of two, so the drain loop indexes with a mask
        // rather than the modulo it would otherwise run four times a sample.
        let ring = (n + 4 * hop).next_power_of_two();
        let bins = n / 2 + 1;
        let nyq = sample_rate as f64 / 2.0;
        let wn = (AMBIENT_TILT_HZ / nyq).clamp(1e-6, 0.999_999);
        let lp_sos = butter_sos(1, wn, BandType::Low);
        let hp_sos = butter_sos(1, wn, BandType::High);
        Self {
            fft: RealFft::new(n),
            n,
            hop,
            window,
            cola,
            phi_lr: vec![Complex64::new(0.0, 0.0); bins],
            phi_ll: vec![0.0; bins],
            phi_rr: vec![0.0; bins],
            masked: [
                vec![Complex64::new(0.0, 0.0); bins],
                vec![Complex64::new(0.0, 0.0); bins],
            ],
            primed: false,
            next_frame: 0,
            ola: [vec![0.0; ring], vec![0.0; ring]],
            ring,
            lp: [SosFilter::from_flat(&lp_sos), SosFilter::from_flat(&lp_sos)],
            hp: [SosFilter::from_flat(&hp_sos), SosFilter::from_flat(&hp_sos)],
            rear: [Vec::new(), Vec::new()],
            height: [Vec::new(), Vec::new()],
            cursor: None,
            mask_table: (0..=MASK_TABLE_LEN)
                .map(|i| mask(i as f64 / MASK_TABLE_LEN as f64))
                .collect(),
            frame: vec![0.0; n],
            scratch: vec![0.0; n],
            spectrum: [
                vec![Complex64::new(0.0, 0.0); bins],
                vec![Complex64::new(0.0, 0.0); bins],
            ],
        }
    }

    /// Samples the split reads past the end of the block it is asked for.
    pub fn look_ahead(&self) -> usize {
        self.n
    }

    pub fn reset(&mut self) {
        self.phi_lr.iter_mut().for_each(|v| *v = Complex64::new(0.0, 0.0));
        self.phi_ll.iter_mut().for_each(|v| *v = 0.0);
        self.phi_rr.iter_mut().for_each(|v| *v = 0.0);
        self.primed = false;
        self.next_frame = 0;
        self.cursor = None;
        for channel in self.ola.iter_mut() {
            channel.iter_mut().for_each(|v| *v = 0.0);
        }
        for filter in self.lp.iter_mut().chain(self.hp.iter_mut()) {
            filter.reset();
        }
    }

    /// Ambient rear and height pairs for `[start, start + len)` of a stem.
    ///
    /// `left`/`right` are the whole resident stem, not the block: the mask for
    /// a block is computed from frames that reach past its end.
    pub fn advance(
        &mut self,
        left: &[f32],
        right: &[f32],
        start: usize,
        len: usize,
    ) -> AmbientBlock<'_> {
        if self.cursor != Some(start) {
            self.reset();
            self.cursor = Some(start);
            self.next_frame = start / self.hop;
        }

        for channel in 0..2 {
            self.rear[channel].resize(len, 0.0);
            self.height[channel].resize(len, 0.0);
        }

        // Frames are produced hop by hop as the block is drained rather than
        // all at once: the overlap-add ring holds one frame plus slack, so a
        // whole-signal call would otherwise wrap onto itself.
        let mut done = 0;
        while done < len {
            let position = start + done;
            let frame = position / self.hop;
            while self.next_frame <= frame {
                self.process_frame(left, right, self.next_frame);
                self.next_frame += 1;
            }
            let until = ((frame + 1) * self.hop).min(start + len);
            for offset in position..until {
                let slot = offset & (self.ring - 1);
                let cola = self.cola[offset & (self.hop - 1)];
                let value = self.ola[0][slot] / cola;
                self.ola[0][slot] = 0.0;
                let index = offset - start;
                self.rear[0][index] = self.lp[0].tick(value);
                self.height[0][index] = self.hp[0].tick(value);

                let value = self.ola[1][slot] / cola;
                self.ola[1][slot] = 0.0;
                self.rear[1][index] = self.lp[1].tick(value);
                self.height[1][index] = self.hp[1].tick(value);
            }
            done += until - position;
        }
        self.cursor = Some(start + len);

        AmbientBlock {
            rear: [&self.rear[0], &self.rear[1]],
            height: [&self.height[0], &self.height[1]],
        }
    }

    fn process_frame(&mut self, left: &[f32], right: &[f32], index: usize) {
        let base = index * self.hop;
        self.windowed(left, base, 0);
        self.windowed(right, base, 1);

        let alpha = if self.primed { COHERENCE_SMOOTHING } else { 1.0 };
        self.primed = true;

        for bin in 0..self.phi_lr.len() {
            let l = self.spectrum[0][bin];
            let r = self.spectrum[1][bin];
            self.phi_lr[bin] = self.phi_lr[bin] * (1.0 - alpha) + l * r.conj() * alpha;
            self.phi_ll[bin] = self.phi_ll[bin] * (1.0 - alpha) + l.norm_sqr() * alpha;
            self.phi_rr[bin] = self.phi_rr[bin] * (1.0 - alpha) + r.norm_sqr() * alpha;

            let auto = self.phi_ll[bin] * self.phi_rr[bin];
            let coherence = (self.phi_lr[bin].norm() / (auto.sqrt() + EPS)).min(1.0);
            let balance = 2.0 * auto.sqrt() / (self.phi_ll[bin] + self.phi_rr[bin] + EPS);
            let gain = balance * self.tabulated_mask(1.0 - coherence);

            self.masked[0][bin] = l * gain;
            self.masked[1][bin] = r * gain;
        }
        self.overlap_add(base);
    }

    /// [`mask`] read off the table, linearly interpolated.
    #[inline]
    fn tabulated_mask(&self, ambience_index: f64) -> f64 {
        let position = ambience_index.clamp(0.0, 1.0) * MASK_TABLE_LEN as f64;
        let index = position as usize;
        let fraction = position - index as f64;
        let low = self.mask_table[index];
        let high = self.mask_table[(index + 1).min(MASK_TABLE_LEN)];
        low + (high - low) * fraction
    }

    /// Windowed transform of one channel at `base`, zero-padded past the end,
    /// left in `spectrum[side]`.
    fn windowed(&mut self, signal: &[f32], base: usize, side: usize) {
        for i in 0..self.n {
            let sample = signal.get(base + i).copied().unwrap_or(0.0) as f64;
            self.frame[i] = sample * self.window[i];
        }
        self.fft.rfft_into(&mut self.frame, &mut self.spectrum[side]);
    }

    fn overlap_add(&mut self, base: usize) {
        for channel in 0..2 {
            self.fft
                .irfft_into(&mut self.masked[channel], &mut self.scratch);
            for i in 0..self.n {
                let slot = (base + i) & (self.ring - 1);
                self.ola[channel][slot] += self.scratch[i] * self.window[i];
            }
        }
    }
}

/// Avendaño & Jot's mapping of the ambience index `Φ = 1 − coherence` onto a
/// gain, floored at `AMBIENCE_FLOOR`.
fn mask(ambience_index: f64) -> f64 {
    let shifted = AMBIENCE_SLOPE * std::f64::consts::PI * (ambience_index - AMBIENCE_THRESHOLD);
    ((1.0 - AMBIENCE_FLOOR) / 2.0) * shifted.tanh() + (1.0 + AMBIENCE_FLOOR) / 2.0
}
