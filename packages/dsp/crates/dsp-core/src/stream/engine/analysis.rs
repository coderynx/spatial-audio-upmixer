//! Loudness/true-peak measurement and the meter + spectrum readouts.

use super::{PreviewEngine, METER_WINDOW_FRAMES};
use crate::spatial::downmix::{FoldTo51, FOLD_51_WEIGHTS};
use crate::stream::meters::Meters;
use crate::stream::params::OutputMode;

impl PreviewEngine {
    /// The 5.1 re-render integrated loudness is measured on, for a native
    /// output wider than 5.1 — `None` when the delivered output is already
    /// the programme (see `docs/standards/loudness_dsp_bs1770.md`
    /// §"Measurement programme").
    pub fn measurement_fold(&self) -> Option<FoldTo51> {
        if self.params.output_mode != OutputMode::Native {
            return None;
        }
        let names: Vec<&str> = self.params.speakers.iter().map(|s| s.name.as_str()).collect();
        FoldTo51::new(&names)
    }

    /// Measure the whole collapsed programme: integrated loudness in LKFS and
    /// true peak in dBTP.
    ///
    /// This replaces the browser's old excerpt-sampled estimate (ledger D4)
    /// with the real BS.1770 measurement over the actual render, so the
    /// correction gain the preview applies is the one a bounce would need.
    /// It renders the programme once into memory — two channels for every
    /// collapse mode — and rewinds afterwards, so the transport is untouched.
    ///
    /// Loudness comes off the measurement programme (the 5.1 re-render for a
    /// native bed wider than 5.1, whose weights the fold fixes and `weights`
    /// no longer describes); true peak stays on the delivered channels, which
    /// are what the ceiling applies to.
    pub fn measure(&mut self, weights: &[f64]) -> (f64, f64) {
        let out_channels = self.output_channels();
        self.rewind();

        let block = 8192;
        let mut collected = vec![Vec::new(); out_channels];
        let mut scratch = vec![0.0; out_channels.max(self.params.speakers.len()) * block];
        loop {
            let written = self.render(&mut scratch, block);
            if written == 0 {
                break;
            }
            for (channel, sink) in collected.iter_mut().enumerate() {
                sink.extend_from_slice(&scratch[channel * block..channel * block + written]);
            }
        }
        self.rewind();

        let refs: Vec<&[f64]> = collected.iter().map(|c| c.as_slice()).collect();
        let mut folded = Vec::new();
        let programme: Vec<(f64, &[f64])> = match self.measurement_fold() {
            Some(fold) => {
                let frames = refs.first().map(|c| c.len()).unwrap_or(0);
                fold.apply(&refs, frames, &mut folded);
                FOLD_51_WEIGHTS.iter().copied().zip(folded.iter().map(|c| c.as_slice())).collect()
            }
            None => refs
                .iter()
                .enumerate()
                .map(|(i, samples)| (weights.get(i).copied().unwrap_or(1.0), *samples))
                .collect(),
        };
        (
            crate::loudness::measure_integrated_loudness(&programme, self.sample_rate),
            crate::loudness::measure_true_peak(&refs),
        )
    }

    /// Levels from the most recent render.
    pub fn meters(&self) -> &Meters {
        &self.meters
    }

    /// Per-stem `(level, spectral centroid, duck gain)` for the
    /// haze/elevation displays — all roughly 0..1, `centroid` sqrt-scaled
    /// toward the low end like a listener's own frequency perception and
    /// `duck` the mean transient-duck gain, 1.0 for no reduction. Computed
    /// here rather than kept current in `meters`/`render()`: the FFT this
    /// needs is too heavy to run every 128-frame quantum, but is cheap enough
    /// to run at the worklet's ~30Hz report cadence, called on demand from the
    /// same trailing `METER_WINDOW_FRAMES` window the meters use.
    pub fn stem_spectrum(&self) -> Vec<(f64, f64, f64)> {
        self.stems
            .iter()
            .enumerate()
            .map(|(i, stem)| {
                let sp = self.params.stems.get(i);
                if !sp.map(|p| p.enabled).unwrap_or(true) {
                    return (0.0, 0.0, 1.0);
                }
                let gain = sp
                    .map(|p| 10.0_f64.powf(p.rebalance_db / 20.0))
                    .unwrap_or(1.0);
                let to = self.emitted.min(stem.len());
                let win_start = to.saturating_sub(METER_WINDOW_FRAMES);
                if to <= win_start {
                    return (0.0, 0.0, 1.0);
                }
                let duck = self.duck_gain(i, win_start, to);

                let level = self
                    .meters
                    .stems
                    .get(i)
                    .map(|pair| ((pair[0].rms + pair[1].rms) * 0.5 * 2.5).min(1.0))
                    .unwrap_or(0.0);

                let mut windowed = vec![0.0; self.spectrum_window.len()];
                for (j, w) in self.spectrum_window.iter().enumerate().take(to - win_start) {
                    let sample = (stem.left[win_start + j] as f64 + stem.right[win_start + j] as f64) * 0.5;
                    windowed[j] = sample * gain * w;
                }
                let bins = self.spectrum_fft.rfft(&windowed);
                let mut weighted = 0.0;
                let mut total = 0.0;
                for (bin, c) in bins.iter().enumerate() {
                    let amplitude = c.norm();
                    weighted += amplitude * bin as f64;
                    total += amplitude;
                }
                let centroid = if total > 0.0 && bins.len() > 1 {
                    ((weighted / total) / (bins.len() - 1) as f64).sqrt()
                } else {
                    0.0
                };
                (level, centroid, duck)
            })
            .collect()
    }

    /// Mean duck gain applied to stem `i` over `[from, to)` absolute frames.
    /// The mean, not the window's deepest dip: at a 60 ms release against a
    /// ~43 ms window a peak reading would sit pinned at the dip for as long
    /// as it takes the display to notice the next one.
    fn duck_gain(&self, i: usize, from: usize, to: usize) -> f64 {
        let Some(trace) = self.duck.channels.get(i) else { return 1.0 };
        let lo = from.saturating_sub(self.duck.base).min(trace.len());
        let hi = to.saturating_sub(self.duck.base).min(trace.len());
        if hi <= lo {
            return 1.0;
        }
        trace[lo..hi].iter().sum::<f64>() / (hi - lo) as f64
    }
}
