//! Velvet-noise decorrelator pair for surround and height sends.
//!
//! Reference design: Alary, Politis & Välimäki, "Velvet-noise decorrelator"
//! (DAFx 2018) — a sparse FIR of ~1 tap/ms with ±1 signs on an exponentially
//! decaying envelope, which decorrelates without the periodic comb a single
//! delayed copy produces.
//!
//! The pair is built as **complements**: `2 * taps` velvet cells are laid over
//! the filter length and handed out alternately, so the two sides never share
//! a tap position. Three properties follow by construction rather than by
//! tuning:
//!
//! - `<h_L, h_R> = 0` exactly, so the pair is decorrelated at zero lag;
//! - `h_L + h_R` is itself a velvet sequence of twice the density, so a
//!   BS.775 fold-down of a pair fed from one source has no cancellation
//!   structure at all — its energy is the power sum (+3.01 dB), where the
//!   single-delay blend it replaces loses 1.5 dB to comb cancellation;
//! - each side carries unit energy, where that blend costs 2.97 dB broadband.
//!
//! Per-bin magnitude flatness is *not* a property of any sparse FIR: with M
//! taps the response has ~M²/2 non-zero autocorrelation lags against 2M free
//! parameters, so the Rayleigh floor of ~5.5 dB per-bin sigma cannot be
//! optimized away, at any length or tap count (measured: it does not move).
//! What the design controls is where the dips land — ~60 aperiodic ones here
//! against the ~490 evenly spaced −20 dB notches of the blend. See
//! `docs/plans/mixing/phase2_report.md` for the measured comparison.

use crate::kernels::rng::{next_sign, next_unit};

/// Default filter span. Long enough to decorrelate down to ~200 Hz, short
/// enough to read as one event rather than an echo.
pub const VELVET_LENGTH_MS: f64 = 30.0;

/// Default taps per side — ~1 tap/ms, the density the reference design uses.
pub const VELVET_TAPS_PER_SIDE: usize = 30;

/// Envelope ratio between successive taps, ~−27 dB by the end of the span.
pub const VELVET_DECAY: f64 = 0.9;

/// Default tap-set seed, used by the surround sends. Positions and signs are a
/// random draw, and the draw matters: over 400k seeds the worst third-octave
/// deviation of the pair ranges 2.5-16 dB, so this one is the best of that
/// search rather than an arbitrary constant.
pub const VELVET_SEED: u64 = 260_797;

/// Tap-set seed for the height sends. A second draw is what keeps a stem's
/// surround and height sends decorrelated from each other, not only within
/// each pair. Picked from the same 400k-seed search, scored on flatness but
/// chosen for the cross term: its taps never land on a surround tap, so the
/// two zone classes are exactly orthogonal (2.68 dB worst third-octave
/// against the flattest candidate's 2.53 dB at a 0.12 cross product).
pub const VELVET_SEED_HEIGHT: u64 = 18_861;

/// Default wet fraction. Fully wet, by measurement: a dry component only
/// re-correlates the pair (its correlation is exactly `1 - wet`) and, at every
/// fraction measured, made the third-octave deviation worse rather than
/// better.
pub const VELVET_WET: f64 = 1.0;

/// One side's sparse FIR: `(delay, gain)` pairs, unit energy.
#[derive(Clone, Debug, PartialEq)]
pub struct VelvetFir {
    taps: Vec<(usize, f64)>,
    span: usize,
}

impl VelvetFir {
    /// Taps as `(delay in samples, signed gain)`, ascending by delay.
    pub fn taps(&self) -> &[(usize, f64)] {
        &self.taps
    }

    /// Delay of the last tap.
    pub fn span(&self) -> usize {
        self.span
    }

    /// Whole-buffer convolution, truncated to the input length the way the
    /// other sends are.
    pub fn process(&self, signal: &[f64]) -> Vec<f64> {
        let mut out = vec![0.0; signal.len()];
        for &(delay, gain) in &self.taps {
            if delay >= signal.len() {
                break;
            }
            for (o, x) in out[delay..].iter_mut().zip(signal.iter()) {
                *o += gain * x;
            }
        }
        out
    }
}

/// How many samples one pass over the ring handles. The ring holds the filter
/// span plus this much, so a long block is filtered in several passes rather
/// than from a ring large enough to lose cache locality.
const CHUNK: usize = 256;

/// The streaming counterpart: one ring buffer, read once per tap per block.
///
/// Carries state across blocks and agrees with [`VelvetFir::process`] sample
/// for sample from a cold start. Taps are applied a whole block at a time:
/// per sample, the masked random read into the ring costs more than the
/// multiply it feeds, which the preview's quantum budget notices.
pub struct VelvetLine {
    taps: Vec<(usize, f64)>,
    ring: Vec<f64>,
    write: usize,
}

impl VelvetLine {
    pub fn new(fir: &VelvetFir) -> Self {
        // Power-of-two ring: the read index masks instead of dividing.
        Self {
            taps: fir.taps.clone(),
            ring: vec![0.0; (fir.span + CHUNK + 1).next_power_of_two()],
            write: 0,
        }
    }

    pub fn reset(&mut self) {
        self.ring.fill(0.0);
        self.write = 0;
    }

    /// Filter `signal` in place. Every read comes from the ring, which already
    /// holds the block, so overwriting the caller's buffer is safe.
    pub fn process(&mut self, signal: &mut [f64]) {
        for chunk in signal.chunks_mut(CHUNK) {
            self.process_chunk(chunk);
        }
    }

    fn process_chunk(&mut self, signal: &mut [f64]) {
        let capacity = self.ring.len();
        let mask = capacity - 1;
        let n = signal.len();
        for (i, x) in signal.iter().enumerate() {
            self.ring[(self.write + i) & mask] = *x;
        }
        signal.fill(0.0);
        for &(delay, gain) in &self.taps {
            let start = (self.write + capacity - delay) & mask;
            let head = (capacity - start).min(n);
            for (o, r) in signal[..head].iter_mut().zip(&self.ring[start..start + head]) {
                *o += gain * r;
            }
            for (o, r) in signal[head..].iter_mut().zip(&self.ring[..n - head]) {
                *o += gain * r;
            }
        }
        self.write = (self.write + n) & mask;
    }
}

/// The decorrelator pair for one channel pair.
///
/// `wet` blends in an undelayed copy at constant power, so either side keeps
/// unit energy at any setting; `wet = 0` leaves a plain impulse.
pub fn velvet_pair(
    sample_rate: u32,
    length_ms: f64,
    taps: usize,
    seed: u64,
    wet: f64,
) -> (VelvetFir, VelvetFir) {
    let n = (sample_rate as f64 * length_ms / 1000.0).round() as usize;
    let wet = wet.clamp(0.0, 1.0);
    if n < 2 || taps == 0 || wet == 0.0 {
        let unit = VelvetFir { taps: vec![(0, 1.0)], span: 0 };
        return (unit.clone(), unit);
    }

    let cells = 2 * taps;
    let width = (n - 1) as f64 / cells as f64;
    let mut state = seed;
    let mut sides: [Vec<(usize, f64)>; 2] = [Vec::with_capacity(taps), Vec::with_capacity(taps)];
    let mut envelope = 1.0;

    for cell in 0..cells {
        // One tap per cell, uniformly inside it: adjacent cells cannot collide
        // while `width >= 2`, which is what keeps the two sides disjoint.
        let jitter = next_unit(&mut state) * (width - 1.0).max(0.0);
        let position = (1 + (cell as f64 * width + jitter) as usize).min(n - 1);
        let gain = next_sign(&mut state) * envelope;
        sides[cell % 2].push((position, gain));
        if cell % 2 == 1 {
            envelope *= VELVET_DECAY;
        }
    }

    let mut built = sides.into_iter().map(|mut taps| {
        taps.sort_by_key(|(position, _)| *position);
        let norm = taps.iter().map(|(_, g)| g * g).sum::<f64>().sqrt();
        let scale = wet.sqrt() / norm;
        let mut taps: Vec<(usize, f64)> = taps.iter().map(|(p, g)| (*p, g * scale)).collect();
        if wet < 1.0 {
            taps.insert(0, (0, (1.0 - wet).sqrt()));
        }
        let span = taps.last().map(|(p, _)| *p).unwrap_or(0);
        VelvetFir { taps, span }
    });
    let left = built.next().expect("left side");
    let right = built.next().expect("right side");
    (left, right)
}

/// A pair at the default length, density and wet fraction.
pub fn velvet_pair_seeded(sample_rate: u32, seed: u64) -> (VelvetFir, VelvetFir) {
    velvet_pair(
        sample_rate,
        VELVET_LENGTH_MS,
        VELVET_TAPS_PER_SIDE,
        seed,
        VELVET_WET,
    )
}

/// The surround-send pair, as both bindings and the streaming engine take it.
pub fn velvet_pair_default(sample_rate: u32) -> (VelvetFir, VelvetFir) {
    velvet_pair_seeded(sample_rate, VELVET_SEED)
}
