//! Conservative, complementary cleanup for one two-child stereo separation task.

use std::collections::VecDeque;
use std::fmt;

use rustfft::num_complex::Complex64;

use crate::kernels::fft::RealFft;
use crate::kernels::stft::hann_periodic;

pub const STEM_CLEANUP_FFT_SIZE: usize = 1024;
pub const STEM_CLEANUP_HOP: usize = STEM_CLEANUP_FFT_SIZE / 4;
pub const STEM_CLEANUP_LATENCY: usize = STEM_CLEANUP_FFT_SIZE;

const HISTORY_FRAMES: usize = 4;
const EPS: f64 = 1e-20;

/// One stereo block. Python duplicates mono before this boundary.
#[derive(Clone, Copy)]
pub struct StereoBlock<'a> {
    pub left: &'a [f64],
    pub right: &'a [f64],
}

/// Owned stereo samples returned by [`StemCleanup`].
#[derive(Clone, Debug, PartialEq)]
pub struct StereoBuffer {
    pub left: Vec<f64>,
    pub right: Vec<f64>,
}

/// Fixed product-policy voicing passed by the Python orchestration layer.
#[derive(Clone, Copy, Debug)]
pub struct StemCleanupPolicy {
    pub relative_energy_floor: f64,
    pub relative_leakage_floor: f64,
    pub coherence_floor: f64,
    pub dominance_ratio: f64,
    pub transfer_cap: f64,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum StemCleanupError {
    UnsupportedSampleRate,
    LengthMismatch,
    NonFinite,
    InvalidPolicy,
    AlreadyFlushed,
}

impl fmt::Display for StemCleanupError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::UnsupportedSampleRate => "sample rate must be between 8000 and 192000 Hz",
            Self::LengthMismatch => {
                "parent and child stereo blocks must have equal channel lengths"
            }
            Self::NonFinite => "stem cleanup input must be finite",
            Self::InvalidPolicy => "stem cleanup policy is invalid",
            Self::AlreadyFlushed => "stem cleanup cannot process after flush",
        })
    }
}

impl std::error::Error for StemCleanupError {}

#[derive(Clone, Copy, Default)]
struct BandStats {
    a: f64,
    b: f64,
    cross: Complex64,
}

impl BandStats {
    fn add(self, other: Self) -> Self {
        Self {
            a: self.a + other.a,
            b: self.b + other.b,
            cross: self.cross + other.cross,
        }
    }

    fn subtract(self, other: Self) -> Self {
        Self {
            a: self.a - other.a,
            b: self.b - other.b,
            cross: self.cross - other.cross,
        }
    }

    fn scale(self, amount: f64) -> Self {
        Self {
            a: self.a * amount,
            b: self.b * amount,
            cross: self.cross * amount,
        }
    }

    fn finite(self) -> bool {
        self.a.is_finite()
            && self.b.is_finite()
            && self.cross.re.is_finite()
            && self.cross.im.is_finite()
    }
}

#[derive(Clone, Copy, Default)]
struct Transfer {
    direction: i8,
    gain: f64,
}

/// Stateful fixed-voicing cleanup for an ordered two-child stereo split.
pub struct StemCleanup {
    policy: StemCleanupPolicy,
    fft: RealFft,
    window: Vec<f64>,
    cola: Vec<f64>,
    input: [Vec<f64>; 6],
    write: usize,
    seen: usize,
    next_frame: i64,
    audio_samples: usize,
    generated: usize,
    ola: [Vec<f64>; 6],
    ring: usize,
    frame: Vec<f64>,
    spectrum: [Vec<Complex64>; 6],
    bands: Vec<(usize, usize)>,
    bin_bands: Vec<usize>,
    history: Vec<BandStats>,
    sums: Vec<BandStats>,
    history_cursor: usize,
    history_count: usize,
    transfer: Vec<Transfer>,
    output: [VecDeque<f64>; 4],
    delay_remaining: usize,
    flushed: bool,
}

impl StemCleanup {
    pub fn new(sample_rate: u32, policy: StemCleanupPolicy) -> Result<Self, StemCleanupError> {
        if !(8_000..=192_000).contains(&sample_rate) {
            return Err(StemCleanupError::UnsupportedSampleRate);
        }
        if !valid_policy(policy) {
            return Err(StemCleanupError::InvalidPolicy);
        }
        let n = STEM_CLEANUP_FFT_SIZE;
        let hop = STEM_CLEANUP_HOP;
        let window: Vec<f64> = hann_periodic(n).into_iter().map(f64::sqrt).collect();
        let mut cola = vec![0.0; hop];
        for (index, value) in window.iter().enumerate() {
            cola[index % hop] += value * value;
        }
        let bins = n / 2 + 1;
        let (bands, bin_bands) = erb_bands(bins, sample_rate, n);
        let band_count = bands.len();
        let ring = (n + hop).next_power_of_two();
        let queue_capacity = n + hop;
        Ok(Self {
            policy,
            fft: RealFft::new(n),
            window,
            cola,
            input: std::array::from_fn(|_| vec![0.0; n]),
            write: 0,
            seen: 0,
            next_frame: -(n as i64 - hop as i64),
            audio_samples: 0,
            generated: 0,
            ola: std::array::from_fn(|_| vec![0.0; ring]),
            ring,
            frame: vec![0.0; n],
            spectrum: std::array::from_fn(|_| vec![Complex64::new(0.0, 0.0); bins]),
            bands,
            bin_bands,
            history: vec![BandStats::default(); HISTORY_FRAMES * 2 * band_count],
            sums: vec![BandStats::default(); 2 * band_count],
            history_cursor: 0,
            history_count: 0,
            transfer: vec![Transfer::default(); 2 * band_count],
            output: std::array::from_fn(|_| VecDeque::with_capacity(queue_capacity)),
            delay_remaining: STEM_CLEANUP_LATENCY,
            flushed: false,
        })
    }

    pub fn latency_samples(&self) -> usize {
        STEM_CLEANUP_LATENCY
    }

    /// Fixed internal working samples; it does not grow with programme length.
    pub fn scratch_samples(&self) -> usize {
        self.input.iter().map(Vec::capacity).sum::<usize>()
            + self.ola.iter().map(Vec::capacity).sum::<usize>()
            + self.frame.capacity()
            + self.spectrum.iter().map(Vec::capacity).sum::<usize>()
            + self.history.capacity()
            + self.sums.capacity()
            + self.transfer.capacity()
            + self.output.iter().map(VecDeque::capacity).sum::<usize>()
    }

    /// Process arbitrary, equally sized stereo chunks. Output has the same
    /// shape and carries a fixed [`STEM_CLEANUP_LATENCY`] sample delay.
    pub fn process(
        &mut self,
        parent: StereoBlock<'_>,
        child_a: StereoBlock<'_>,
        child_b: StereoBlock<'_>,
    ) -> Result<(StereoBuffer, StereoBuffer), StemCleanupError> {
        if self.flushed {
            return Err(StemCleanupError::AlreadyFlushed);
        }
        let len = validate(parent, child_a, child_b)?;
        let mut out_a = StereoBuffer {
            left: Vec::with_capacity(len),
            right: Vec::with_capacity(len),
        };
        let mut out_b = StereoBuffer {
            left: Vec::with_capacity(len),
            right: Vec::with_capacity(len),
        };
        for index in 0..len {
            self.ingest([
                parent.left[index],
                parent.right[index],
                child_a.left[index],
                child_a.right[index],
                child_b.left[index],
                child_b.right[index],
            ]);
            self.audio_samples += 1;
            self.emit(&mut out_a, &mut out_b);
        }
        Ok((out_a, out_b))
    }

    /// Emit the remaining delayed audio. It always returns
    /// [`STEM_CLEANUP_LATENCY`] samples per channel, including any initial
    /// delay not consumed by a short input.
    pub fn flush(&mut self) -> Result<(StereoBuffer, StereoBuffer), StemCleanupError> {
        if self.flushed {
            return Err(StemCleanupError::AlreadyFlushed);
        }
        while self.generated < self.audio_samples {
            self.ingest([0.0; 6]);
        }
        let len = self.delay_remaining + self.output[0].len();
        let mut out_a = StereoBuffer {
            left: Vec::with_capacity(len),
            right: Vec::with_capacity(len),
        };
        let mut out_b = StereoBuffer {
            left: Vec::with_capacity(len),
            right: Vec::with_capacity(len),
        };
        while self.delay_remaining > 0 {
            self.delay_remaining -= 1;
            out_a.left.push(0.0);
            out_a.right.push(0.0);
            out_b.left.push(0.0);
            out_b.right.push(0.0);
        }
        while let (Some(al), Some(ar), Some(bl), Some(br)) = (
            self.output[0].pop_front(),
            self.output[1].pop_front(),
            self.output[2].pop_front(),
            self.output[3].pop_front(),
        ) {
            out_a.left.push(al);
            out_a.right.push(ar);
            out_b.left.push(bl);
            out_b.right.push(br);
        }
        self.flushed = true;
        Ok((out_a, out_b))
    }

    fn ingest(&mut self, samples: [f64; 6]) {
        for (stream, sample) in samples.into_iter().enumerate() {
            self.input[stream][self.write] = sample;
        }
        self.write = (self.write + 1) % STEM_CLEANUP_FFT_SIZE;
        self.seen += 1;
        while self.next_frame + STEM_CLEANUP_FFT_SIZE as i64 <= self.seen as i64 {
            let available = self.audio_samples.saturating_sub(self.generated);
            let output_len = if self.next_frame < 0 {
                0
            } else {
                available.min(STEM_CLEANUP_HOP)
            };
            self.process_frame(self.next_frame, output_len);
            self.next_frame += STEM_CLEANUP_HOP as i64;
        }
    }

    fn emit(&mut self, out_a: &mut StereoBuffer, out_b: &mut StereoBuffer) {
        if self.delay_remaining > 0 {
            self.delay_remaining -= 1;
            out_a.left.push(0.0);
            out_a.right.push(0.0);
            out_b.left.push(0.0);
            out_b.right.push(0.0);
            return;
        }
        out_a.left.push(self.output[0].pop_front().unwrap_or(0.0));
        out_a.right.push(self.output[1].pop_front().unwrap_or(0.0));
        out_b.left.push(self.output[2].pop_front().unwrap_or(0.0));
        out_b.right.push(self.output[3].pop_front().unwrap_or(0.0));
    }

    fn process_frame(&mut self, frame_start: i64, output_len: usize) {
        for stream in 0..6 {
            for index in 0..STEM_CLEANUP_FFT_SIZE {
                let sample = frame_start + index as i64;
                self.frame[index] = if sample < 0 {
                    0.0
                } else {
                    self.input[stream][sample as usize % STEM_CLEANUP_FFT_SIZE]
                } * self.window[index];
            }
            self.fft
                .rfft_into(&mut self.frame, &mut self.spectrum[stream]);
        }
        self.update_transfer();
        self.apply_transfer();
        for stream in 0..6 {
            self.fft
                .irfft_into(&mut self.spectrum[stream], &mut self.frame);
            for index in 0..STEM_CLEANUP_FFT_SIZE {
                let sample = frame_start + index as i64;
                if sample < 0 {
                    continue;
                }
                let slot = sample as usize & (self.ring - 1);
                self.ola[stream][slot] += self.frame[index] * self.window[index];
            }
        }
        for index in 0..output_len {
            let sample = frame_start as usize + index;
            let slot = sample & (self.ring - 1);
            let cola = self.cola[index % STEM_CLEANUP_HOP];
            let parent = self.ola[0][slot] / cola;
            let a_left = self.ola[2][slot] / cola;
            let a_right = self.ola[3][slot] / cola;
            let b_left = self.ola[4][slot] / cola;
            let b_right = self.ola[5][slot] / cola;
            let parent_right = self.ola[1][slot] / cola;
            self.output[0].push_back(a_left + (parent - a_left - b_left) * 0.5);
            self.output[1].push_back(a_right + (parent_right - a_right - b_right) * 0.5);
            self.output[2].push_back(b_left + (parent - a_left - b_left) * 0.5);
            self.output[3].push_back(b_right + (parent_right - a_right - b_right) * 0.5);
            for stream in 0..6 {
                self.ola[stream][slot] = 0.0;
            }
        }
        self.generated += output_len;
    }

    fn update_transfer(&mut self) {
        let mut total = 0.0;
        for side in 0..2 {
            for band in 0..self.bands.len() {
                let stats = band_stats(
                    &self.spectrum[2 + side],
                    &self.spectrum[4 + side],
                    self.bands[band],
                );
                self.replace_stats(side, band, stats);
                total += stats.a + stats.b;
            }
        }
        let floor = (total * self.policy.relative_energy_floor).max(EPS);
        let divisor = (self.history_count + 1).min(HISTORY_FRAMES) as f64;
        for side in 0..2 {
            for band in 0..self.bands.len() {
                let index = side * self.bands.len() + band;
                let stats = self.sums[index].scale(1.0 / divisor);
                let target = transfer_target(stats, floor, self.policy);
                let prior = self.transfer[index];
                self.transfer[index] = if target.direction == prior.direction {
                    Transfer {
                        direction: target.direction,
                        gain: prior.gain * 0.75 + target.gain * 0.25,
                    }
                } else {
                    Transfer {
                        direction: target.direction,
                        gain: target.gain * 0.25,
                    }
                };
            }
        }
        self.history_cursor = (self.history_cursor + 1) % HISTORY_FRAMES;
        self.history_count = (self.history_count + 1).min(HISTORY_FRAMES);
    }

    fn replace_stats(&mut self, side: usize, band: usize, value: BandStats) {
        let width = self.bands.len();
        let sum_index = side * width + band;
        let history_index = (self.history_cursor * 2 + side) * width + band;
        let old = self.history[history_index];
        self.history[history_index] = value;
        self.sums[sum_index] = self.sums[sum_index].add(value).subtract(old);
    }

    fn apply_transfer(&mut self) {
        for side in 0..2 {
            for bin in 0..self.spectrum[2 + side].len() {
                let transfer = self.transfer[side * self.bands.len() + self.bin_bands[bin]];
                if transfer.gain <= 0.0 {
                    continue;
                }
                let a = self.spectrum[2 + side][bin];
                let b = self.spectrum[4 + side][bin];
                let stats = self.sums[side * self.bands.len() + self.bin_bands[bin]]
                    .scale(1.0 / self.history_count.max(1) as f64);
                if !stats.finite() {
                    continue;
                }
                match transfer.direction {
                    1 => {
                        let component = a * (stats.cross.conj() / (stats.a + EPS)) * transfer.gain;
                        self.spectrum[2 + side][bin] += component;
                        self.spectrum[4 + side][bin] -= component;
                    }
                    -1 => {
                        let component = b * (stats.cross / (stats.b + EPS)) * transfer.gain;
                        self.spectrum[2 + side][bin] -= component;
                        self.spectrum[4 + side][bin] += component;
                    }
                    _ => {}
                }
            }
        }
    }
}

fn validate(
    parent: StereoBlock<'_>,
    child_a: StereoBlock<'_>,
    child_b: StereoBlock<'_>,
) -> Result<usize, StemCleanupError> {
    let len = parent.left.len();
    let signals = [
        parent.left,
        parent.right,
        child_a.left,
        child_a.right,
        child_b.left,
        child_b.right,
    ];
    if signals.iter().any(|signal| signal.len() != len) {
        return Err(StemCleanupError::LengthMismatch);
    }
    if signals
        .iter()
        .flat_map(|signal| signal.iter())
        .any(|sample| !sample.is_finite())
    {
        return Err(StemCleanupError::NonFinite);
    }
    Ok(len)
}

fn band_stats(a: &[Complex64], b: &[Complex64], band: (usize, usize)) -> BandStats {
    let mut out = BandStats::default();
    for index in band.0..band.1 {
        out.a += a[index].norm_sqr();
        out.b += b[index].norm_sqr();
        out.cross += a[index] * b[index].conj();
    }
    out
}

fn transfer_target(stats: BandStats, floor: f64, policy: StemCleanupPolicy) -> Transfer {
    if !stats.finite() || stats.a <= floor || stats.b <= floor {
        return Transfer::default();
    }
    if stats.a.min(stats.b) / stats.a.max(stats.b) < policy.relative_leakage_floor {
        return Transfer::default();
    }
    let coherence = stats.cross.norm_sqr() / (stats.a * stats.b).max(EPS);
    if !coherence.is_finite() || coherence < policy.coherence_floor {
        return Transfer::default();
    }
    let direction = if stats.a >= stats.b * policy.dominance_ratio {
        1
    } else if stats.b >= stats.a * policy.dominance_ratio {
        -1
    } else {
        0
    };
    Transfer {
        direction,
        gain: if direction == 0 {
            0.0
        } else {
            (policy.transfer_cap * coherence.sqrt()).min(policy.transfer_cap)
        },
    }
}

fn valid_policy(policy: StemCleanupPolicy) -> bool {
    policy.relative_energy_floor.is_finite()
        && (0.0..=1.0).contains(&policy.relative_energy_floor)
        && policy.relative_leakage_floor.is_finite()
        && (0.0..=1.0).contains(&policy.relative_leakage_floor)
        && policy.coherence_floor.is_finite()
        && (0.0..=1.0).contains(&policy.coherence_floor)
        && policy.dominance_ratio.is_finite()
        && policy.dominance_ratio >= 1.0
        && policy.transfer_cap.is_finite()
        && (0.0..=1.0).contains(&policy.transfer_cap)
}

fn erb_bands(bins: usize, sample_rate: u32, n: usize) -> (Vec<(usize, usize)>, Vec<usize>) {
    let bin_hz = sample_rate as f64 / n as f64;
    let mut bands = Vec::new();
    let mut start = 0;
    while start < bins {
        let end_hz = erb_hz(erb_rate(start as f64 * bin_hz) + 1.0);
        let end = ((end_hz / bin_hz).ceil() as usize).max(start + 3).min(bins);
        bands.push((start, end));
        start = end;
    }
    let mut bin_bands = vec![0; bins];
    for (band, (start, end)) in bands.iter().copied().enumerate() {
        for slot in &mut bin_bands[start..end] {
            *slot = band;
        }
    }
    (bands, bin_bands)
}

fn erb_rate(hz: f64) -> f64 {
    21.4 * (1.0 + 0.00437 * hz).log10()
}

fn erb_hz(rate: f64) -> f64 {
    (10.0_f64.powf(rate / 21.4) - 1.0) / 0.00437
}

#[cfg(test)]
#[path = "stem_cleanup_tests.rs"]
mod stem_cleanup_tests;
