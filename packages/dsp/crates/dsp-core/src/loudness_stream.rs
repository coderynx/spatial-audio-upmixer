//! Streaming forms of the BS.1770 meters in [`crate::loudness`].
//!
//! The offline functions need the whole programme in memory at once, which on
//! the browser's audio thread means one render that blocks for a minute. These
//! carry state instead, so a measurement can be advanced a slice at a time.
//!
//! They are held to *equality*, not agreement: same filter recurrence, same
//! block grid, same `pairwise_sum` over each gating block, same gate
//! arithmetic. `measuring_in_slices_equals_the_offline_meters` pins that.

use crate::kernels::biquad::SosFilter;
use crate::kernels::sum::pairwise_sum;
use crate::loudness::{
    k_weighting_sos, ABS_GATE, BLOCK_S, HOP_S, LKFS_OFFSET, REL_GATE_OFFSET, TRUE_PEAK_FIR_4X,
    TRUE_PEAK_OVERSAMPLE,
};

/// One channel's K-weighted gating blocks, produced as samples arrive.
struct ChannelGate {
    filter: SosFilter,
    weight: f64,
    /// K-weighted squares not yet consumed by a block, oldest first.
    squares: Vec<f64>,
    /// Samples still needed before the next block closes.
    until_block: usize,
    /// Blocks this channel has closed, which is its index into `power_blocks`.
    closed: usize,
    block_len: usize,
    hop_len: usize,
}

impl ChannelGate {
    fn new(weight: f64, sos: &[[f64; 6]], block_len: usize, hop_len: usize) -> Self {
        Self {
            filter: SosFilter::from_flat(sos),
            weight,
            squares: Vec::new(),
            until_block: block_len,
            closed: 0,
            block_len,
            hop_len,
        }
    }

    /// Feed samples, appending each closed block's weighted mean square.
    fn push(&mut self, samples: &[f64], blocks: &mut Vec<f64>) {
        let scale = self.weight / self.block_len as f64;
        for &x in samples {
            let filtered = self.filter.tick(x);
            self.squares.push(filtered * filtered);
            self.until_block -= 1;
            if self.until_block > 0 {
                continue;
            }
            // The block ends at the newest sample and spans `block_len`; the
            // offline pass sums exactly this window with `pairwise_sum`.
            let start = self.squares.len() - self.block_len;
            blocks.push(pairwise_sum(&self.squares[start..]) * scale);
            self.until_block = self.hop_len;
            // Only the overlap of the next block needs keeping.
            let keep = self.block_len - self.hop_len;
            if self.squares.len() > keep {
                self.squares.drain(..self.squares.len() - keep);
            }
        }
    }
}

/// Integrated loudness over a programme delivered in slices.
pub struct IntegratedLoudnessMeter {
    channels: Vec<ChannelGate>,
    /// Summed weighted mean square per gating block, across channels.
    power_blocks: Vec<f64>,
    scratch: Vec<f64>,
    enough_for_a_block: bool,
}

impl IntegratedLoudnessMeter {
    /// `weights` are the BS.1770 channel weights, in channel order; a zero
    /// weight drops the channel exactly as the offline pass skips it.
    pub fn new(weights: &[f64], sample_rate: u32) -> Self {
        let block_len = (BLOCK_S * sample_rate as f64) as usize;
        let hop_len = (HOP_S * sample_rate as f64) as usize;
        let sos = k_weighting_sos(sample_rate);
        let channels = weights
            .iter()
            .map(|w| ChannelGate::new(*w, &sos, block_len.max(1), hop_len.max(1)))
            .collect();
        Self {
            channels,
            power_blocks: Vec::new(),
            scratch: Vec::new(),
            enough_for_a_block: block_len > 0 && hop_len > 0,
        }
    }

    /// Feed one slice: `channel_slices[i]` belongs to weight `i`.
    pub fn push(&mut self, channel_slices: &[&[f64]]) {
        if !self.enough_for_a_block {
            return;
        }
        for (index, gate) in self.channels.iter_mut().enumerate() {
            if gate.weight == 0.0 {
                continue;
            }
            let Some(slice) = channel_slices.get(index) else { continue };
            self.scratch.clear();
            gate.push(slice, &mut self.scratch);
            for (offset, value) in self.scratch.iter().enumerate() {
                // Blocks are indexed globally, so channels accumulate into the
                // same block in channel order however the slices fall.
                match self.power_blocks.get_mut(gate.closed + offset) {
                    Some(slot) => *slot += value,
                    None => self.power_blocks.push(*value),
                }
            }
            gate.closed += self.scratch.len();
        }
    }

    /// Apply the absolute and relative gates. Idempotent.
    pub fn finish(&self) -> f64 {
        if self.power_blocks.is_empty() {
            return ABS_GATE;
        }
        let block_lkfs: Vec<f64> = self
            .power_blocks
            .iter()
            .map(|p| LKFS_OFFSET + 10.0 * p.max(1e-30).log10())
            .collect();
        let above_abs: Vec<usize> =
            (0..block_lkfs.len()).filter(|&i| block_lkfs[i] >= ABS_GATE).collect();
        if above_abs.is_empty() {
            return ABS_GATE;
        }
        let mean_of = |idx: &[usize]| {
            let vals: Vec<f64> = idx.iter().map(|&i| self.power_blocks[i]).collect();
            pairwise_sum(&vals) / vals.len() as f64
        };
        let ungated = LKFS_OFFSET + 10.0 * mean_of(&above_abs).max(1e-30).log10();
        let above_rel: Vec<usize> = above_abs
            .iter()
            .copied()
            .filter(|&i| block_lkfs[i] >= ungated + REL_GATE_OFFSET)
            .collect();
        let gated = if above_rel.is_empty() { &above_abs } else { &above_rel };
        LKFS_OFFSET + 10.0 * mean_of(gated).max(1e-30).log10()
    }
}

/// True peak of one channel, sample by sample.
///
/// Reproduces `upfirdn_up(FIR, x, 4)` followed by a max: each 4x output is
/// accumulated over the contributing input samples in increasing input order,
/// which is the order the offline pass accumulates them in.
pub struct TruePeakMeter {
    /// Most recent inputs, newest first; one per filter phase reach.
    history: Vec<f64>,
    peak: f64,
}

impl TruePeakMeter {
    pub fn new() -> Self {
        let reach = TRUE_PEAK_FIR_4X.len().div_ceil(TRUE_PEAK_OVERSAMPLE);
        Self { history: vec![0.0; reach], peak: 0.0 }
    }

    pub fn push(&mut self, samples: &[f64]) {
        for &x in samples {
            self.history.rotate_right(1);
            self.history[0] = x;
            // Outputs 4k..4k+3 close once input k has arrived.
            for phase in 0..TRUE_PEAK_OVERSAMPLE {
                let mut acc = 0.0;
                // `back` counts inputs backwards, so taps are visited in
                // increasing input index — matching the offline loop.
                for back in (0..self.history.len()).rev() {
                    let tap = phase + back * TRUE_PEAK_OVERSAMPLE;
                    if tap >= TRUE_PEAK_FIR_4X.len() {
                        continue;
                    }
                    acc += self.history[back] * TRUE_PEAK_FIR_4X[tap];
                }
                self.peak = self.peak.max(acc.abs());
            }
        }
    }

    /// Flush the filter's tail. Zeros contribute nothing, so this is the same
    /// set of outputs the offline convolution's overhang produces.
    pub fn finish(&mut self) -> f64 {
        let reach = self.history.len();
        self.push(&vec![0.0; reach]);
        self.peak
    }
}

impl Default for TruePeakMeter {
    fn default() -> Self {
        Self::new()
    }
}

/// dBTP across channels, as `measure_true_peak` reports it.
pub fn true_peak_dbtp(peaks: &[f64]) -> f64 {
    20.0 * peaks.iter().fold(1e-30_f64, |m, p| m.max(*p)).log10()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::loudness::{measure_integrated_loudness, measure_true_peak};

    fn programme(n: usize, seed: f64) -> Vec<f64> {
        (0..n)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                let env = if i % 97_000 < 20_000 { 0.02 } else { 1.0 };
                env * (0.4 * (2.0 * std::f64::consts::PI * (110.0 + seed) * t).sin()
                    + 0.2 * (2.0 * std::f64::consts::PI * 947.0 * t + seed).sin()
                    + 0.05 * (2.0 * std::f64::consts::PI * 6100.0 * t).sin())
            })
            .collect()
    }

    /// The whole point of the streaming meters: identical numbers, delivered
    /// in slices small enough for one audio callback.
    #[test]
    fn measuring_in_slices_equals_the_offline_meters() {
        let sr = 48_000;
        let left = programme(240_000, 0.0);
        let right = programme(240_000, 1.7);
        let weights = [1.0, 1.0];

        let want_lkfs = measure_integrated_loudness(&[(1.0, &left), (1.0, &right)], sr);
        let want_dbtp = measure_true_peak(&[&left, &right]);

        for slice in [128usize, 512, 4096, 7000] {
            let mut loudness = IntegratedLoudnessMeter::new(&weights, sr);
            let mut peaks = [TruePeakMeter::new(), TruePeakMeter::new()];
            let mut at = 0;
            while at < left.len() {
                let stop = (at + slice).min(left.len());
                loudness.push(&[&left[at..stop], &right[at..stop]]);
                peaks[0].push(&left[at..stop]);
                peaks[1].push(&right[at..stop]);
                at = stop;
            }
            let got_lkfs = loudness.finish();
            let got_dbtp = true_peak_dbtp(&[peaks[0].finish(), peaks[1].finish()]);

            assert_eq!(got_lkfs, want_lkfs, "slice {slice}: LKFS");
            assert_eq!(got_dbtp, want_dbtp, "slice {slice}: dBTP");
        }
    }

    #[test]
    fn a_zero_weight_channel_is_dropped_like_the_offline_pass() {
        let sr = 48_000;
        let bed = programme(96_000, 0.0);
        let lfe = programme(96_000, 5.0);
        let want = measure_integrated_loudness(&[(1.0, &bed), (0.0, &lfe)], sr);

        let mut meter = IntegratedLoudnessMeter::new(&[1.0, 0.0], sr);
        let mut at = 0;
        while at < bed.len() {
            let stop = (at + 1024).min(bed.len());
            meter.push(&[&bed[at..stop], &lfe[at..stop]]);
            at = stop;
        }
        assert_eq!(meter.finish(), want);
    }

    #[test]
    fn silence_reports_the_absolute_gate() {
        let silence = vec![0.0; 48_000];
        let mut meter = IntegratedLoudnessMeter::new(&[1.0], 48_000);
        meter.push(&[&silence]);
        assert_eq!(meter.finish(), ABS_GATE);
    }
}
