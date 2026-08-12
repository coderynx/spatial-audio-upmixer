//! Collapse of the mastered bed to whatever the listener is on: the discrete
//! bed itself, a binaural render, a crosstalk-cancelled speaker pair, or the
//! BS.775 stereo downmix.
//!
//! Every FIR here streams, so the collapse is the same convolution the export
//! performs rather than an approximation of it.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_bandpass_sos, butter_sos, BandType};
use crate::spatial::ambisonics::{encode_gains, N_ACN_CHANNELS};
use crate::spatial::downmix::soft_limit;
use crate::spatial::voicing::VoicingParams;

use super::conv::StreamingConvolver;
use super::params::{EngineParams, OutputMode};

/// The voicing chain with carried filter state.
struct StreamingVoicing {
    params: VoicingParams,
    crossfeed: [SosFilter; 2],
    bass: [SosFilter; 2],
    air: [SosFilter; 2],
    presence: [SosFilter; 2],
}

impl StreamingVoicing {
    fn new(sample_rate: u32, p: VoicingParams) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let crossfeed_sos = butter_sos(1, p.crossfeed_cutoff_hz.max(1.0) / nyq, BandType::Low);
        let bass_sos = butter_sos(2, p.bass_shelf_hz.max(1.0) / nyq, BandType::Low);
        let air_sos = butter_sos(2, p.air_shelf_hz.max(1.0) / nyq, BandType::High);
        let bandwidth = (p.presence_hz / p.presence_q.max(1e-6)).max(1.0);
        let low = (p.presence_hz - bandwidth / 2.0).max(1.0) / nyq;
        let high = (p.presence_hz + bandwidth / 2.0).min(nyq - 1.0) / nyq;
        let presence_sos = if low < high && high < 1.0 {
            butter_bandpass_sos(2, low, high)
        } else {
            bass_sos.clone()
        };
        let pair = |sos: &Vec<[f64; 6]>| [SosFilter::from_flat(sos), SosFilter::from_flat(sos)];
        Self {
            params: p,
            crossfeed: pair(&crossfeed_sos),
            bass: pair(&bass_sos),
            air: pair(&air_sos),
            presence: pair(&presence_sos),
        }
    }

    #[inline]
    fn tick(&mut self, left: f64, right: f64) -> (f64, f64) {
        let p = self.params;
        let (mut l, mut r) = (left, right);

        if p.crossfeed_amount > 0.0 {
            let bleed_l = self.crossfeed[0].tick(l);
            let bleed_r = self.crossfeed[1].tick(r);
            let a = p.crossfeed_amount;
            let next_l = l * (1.0 - a) + bleed_r * a;
            let next_r = r * (1.0 - a) + bleed_l * a;
            l = next_l;
            r = next_r;
        }
        if p.bass_shelf_gain_db != 0.0 {
            let gain = 10.0_f64.powf(p.bass_shelf_gain_db / 20.0) - 1.0;
            l += self.bass[0].tick(l) * gain;
            r += self.bass[1].tick(r) * gain;
        }
        if p.air_shelf_gain_db != 0.0 {
            let gain = 10.0_f64.powf(p.air_shelf_gain_db / 20.0) - 1.0;
            l += self.air[0].tick(l) * gain;
            r += self.air[1].tick(r) * gain;
        }
        if p.presence_gain_db != 0.0 {
            let gain = 10.0_f64.powf(p.presence_gain_db / 20.0) - 1.0;
            l += self.presence[0].tick(l) * gain;
            r += self.presence[1].tick(r) * gain;
        }
        if p.stereo_widen != 0.0 {
            let mid = (l + r) * 0.5;
            let side = (l - r) * 0.5 * (1.0 + p.stereo_widen);
            l = mid + side;
            r = mid - side;
        }
        (l, r)
    }
}

/// Collapses the bed for one output mode.
pub struct OutputStage {
    mode: OutputMode,
    n_channels: usize,
    lfe_index: Option<usize>,
    encoders: Vec<Option<[f64; N_ACN_CHANNELS]>>,
    decode: Vec<[StreamingConvolver; 2]>,
    xtc: Option<[[StreamingConvolver; 2]; 2]>,
    voicing: Option<StreamingVoicing>,
    downmix: Vec<Option<(f64, f64)>>,
    soft_limit_threshold: f64,
}

fn split_taps(flat: &[f64], groups: usize) -> Vec<[Vec<f64>; 2]> {
    if flat.is_empty() || groups == 0 {
        return Vec::new();
    }
    let n_taps = flat.len() / (groups * 2);
    (0..groups)
        .map(|g| {
            let base = g * 2 * n_taps;
            [
                flat[base..base + n_taps].to_vec(),
                flat[base + n_taps..base + 2 * n_taps].to_vec(),
            ]
        })
        .collect()
}

impl OutputStage {
    /// `decode_taps`/`xtc_taps` are passed separately from `params` rather
    /// than read off it: the caller may be carrying them as a persistent
    /// override set once over its own binary channel (see
    /// `PreviewEngine::set_decode_taps`) instead of on every parameter
    /// update, since the bank is large and changes far less often than the
    /// rest of the mix.
    pub fn new(sample_rate: u32, params: &EngineParams, decode_taps: &[f64], xtc_taps: &[f64]) -> Self {
        let n_channels = params.speakers.len();
        let encoders = params
            .speakers
            .iter()
            .enumerate()
            .map(|(i, s)| {
                (params.lfe_index != Some(i))
                    .then(|| encode_gains(s.azimuth_rad, s.elevation_rad))
            })
            .collect();

        let decode = split_taps(decode_taps, N_ACN_CHANNELS)
            .into_iter()
            .map(|[l, r]| [StreamingConvolver::new(l), StreamingConvolver::new(r)])
            .collect();
        let xtc = {
            let sets = split_taps(xtc_taps, 2);
            (sets.len() == 2).then(|| {
                let mut it = sets.into_iter();
                let [ll, lr] = it.next().expect("left speaker row");
                let [rl, rr] = it.next().expect("right speaker row");
                [
                    [StreamingConvolver::new(ll), StreamingConvolver::new(lr)],
                    [StreamingConvolver::new(rl), StreamingConvolver::new(rr)],
                ]
            })
        };

        Self {
            mode: params.output_mode,
            n_channels,
            lfe_index: params.lfe_index,
            encoders,
            decode,
            xtc,
            voicing: params.voicing.map(|v| StreamingVoicing::new(sample_rate, v)),
            downmix: params.speakers.iter().map(|s| s.downmix).collect(),
            soft_limit_threshold: params.soft_limit_threshold,
        }
    }

    /// Adopt new mode/voicing/downmix/encoders in place, leaving the decode
    /// and XTC convolvers untouched — those travel their own channel (see
    /// [`super::engine::PreviewEngine::set_decode_taps`]) and are large
    /// enough that rebuilding them on every mix edit is what this method
    /// exists to avoid. Only call when `params.speakers.len()` and
    /// `params.lfe_index` match what this stage was built with; a channel
    /// count change needs a full [`Self::new`].
    pub fn retune(&mut self, sample_rate: u32, params: &EngineParams) {
        self.mode = params.output_mode;
        self.lfe_index = params.lfe_index;
        self.encoders = params
            .speakers
            .iter()
            .enumerate()
            .map(|(i, s)| {
                (params.lfe_index != Some(i))
                    .then(|| encode_gains(s.azimuth_rad, s.elevation_rad))
            })
            .collect();
        self.downmix = params.speakers.iter().map(|s| s.downmix).collect();
        self.soft_limit_threshold = params.soft_limit_threshold;
        self.voicing = params.voicing.map(|v| StreamingVoicing::new(sample_rate, v));
    }

    /// How many channels this stage writes.
    pub fn output_channels(&self) -> usize {
        match self.mode {
            OutputMode::Native => self.n_channels,
            _ => 2,
        }
    }

    /// Collapse `bed` (channel-major, `frames` long) into `out`.
    pub fn process(&mut self, bed: &[Vec<f64>], frames: usize, gain: f64, out: &mut [Vec<f64>]) {
        match self.mode {
            OutputMode::Native => {
                // The look-ahead limiter is the native path's own safety net,
                // so no collapse correction applies here.
                for (channel, dst) in out.iter_mut().enumerate().take(self.n_channels) {
                    dst.clear();
                    dst.extend(bed[channel][..frames].iter().copied());
                }
            }
            OutputMode::Stereo => {
                let (left, right) = self.downmix_stereo(bed, frames);
                self.emit_stereo(left, right, gain, out);
            }
            OutputMode::Binaural => {
                let (left, right) = self.render_binaural(bed, frames);
                self.emit_stereo(left, right, gain, out);
            }
            OutputMode::Transaural => {
                let (ear_l, ear_r) = self.render_binaural(bed, frames);
                let (left, right) = match &mut self.xtc {
                    None => (ear_l, ear_r),
                    Some(matrix) => {
                        let a = matrix[0][0].process(&ear_l);
                        let b = matrix[0][1].process(&ear_r);
                        let c = matrix[1][0].process(&ear_l);
                        let d = matrix[1][1].process(&ear_r);
                        (
                            a.iter().zip(b.iter()).map(|(x, y)| x + y).collect(),
                            c.iter().zip(d.iter()).map(|(x, y)| x + y).collect(),
                        )
                    }
                };
                self.emit_stereo(left, right, gain, out);
            }
        }
    }

    /// The collapse correction is applied before the soft limiter, matching
    /// `render_binaural_delivery`: normalize first, then let the limiter act
    /// only as a true-peak safety net.
    fn emit_stereo(&self, mut left: Vec<f64>, mut right: Vec<f64>, gain: f64, out: &mut [Vec<f64>]) {
        if gain != 1.0 {
            for v in left.iter_mut().chain(right.iter_mut()) {
                *v *= gain;
            }
        }
        if self.soft_limit_threshold > 0.0 {
            soft_limit(&mut left, self.soft_limit_threshold);
            soft_limit(&mut right, self.soft_limit_threshold);
        }
        out[0].clear();
        out[0].extend(left);
        out[1].clear();
        out[1].extend(right);
        for extra in out.iter_mut().skip(2) {
            extra.clear();
        }
    }

    fn downmix_stereo(&self, bed: &[Vec<f64>], frames: usize) -> (Vec<f64>, Vec<f64>) {
        let mut left = vec![0.0; frames];
        let mut right = vec![0.0; frames];
        for (channel, gains) in self.downmix.iter().enumerate() {
            let Some((gl, gr)) = gains else { continue };
            for i in 0..frames {
                left[i] += bed[channel][i] * gl;
                right[i] += bed[channel][i] * gr;
            }
        }
        (left, right)
    }

    /// Encode every positional speaker to ambisonics, decode to the ears, add
    /// LFE *before* voicing (ledger D11), then voice.
    fn render_binaural(&mut self, bed: &[Vec<f64>], frames: usize) -> (Vec<f64>, Vec<f64>) {
        let mut hoa = vec![vec![0.0; frames]; N_ACN_CHANNELS];
        for (channel, gains) in self.encoders.iter().enumerate() {
            let Some(gains) = gains else { continue };
            let source = &bed[channel];
            for (acn, gain) in gains.iter().enumerate() {
                if *gain == 0.0 {
                    continue;
                }
                let target = &mut hoa[acn];
                for i in 0..frames {
                    target[i] += source[i] * gain;
                }
            }
        }

        let mut left = vec![0.0; frames];
        let mut right = vec![0.0; frames];
        for (acn, filters) in self.decode.iter_mut().enumerate() {
            let l = filters[0].process(&hoa[acn]);
            let r = filters[1].process(&hoa[acn]);
            for i in 0..frames {
                left[i] += l[i];
                right[i] += r[i];
            }
        }

        if let Some(lfe) = self.lfe_index {
            for i in 0..frames {
                left[i] += bed[lfe][i];
                right[i] += bed[lfe][i];
            }
        }

        if let Some(voicing) = &mut self.voicing {
            for i in 0..frames {
                let (l, r) = voicing.tick(left[i], right[i]);
                left[i] = l;
                right[i] = r;
            }
        }
        (left, right)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::spatial::voicing::apply_voicing;

    #[test]
    fn streaming_voicing_matches_the_offline_chain() {
        let sr = 48_000;
        let p = VoicingParams {
            crossfeed_amount: 0.25,
            crossfeed_cutoff_hz: 700.0,
            bass_shelf_hz: 120.0,
            bass_shelf_gain_db: 2.0,
            air_shelf_hz: 9000.0,
            air_shelf_gain_db: 1.5,
            presence_hz: 3000.0,
            presence_gain_db: 1.0,
            presence_q: 1.2,
            stereo_widen: 0.3,
        };
        let left: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.05).sin() * 0.4).collect();
        let right: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.07).cos() * 0.4).collect();

        let (want_l, want_r) = apply_voicing(&left, &right, sr, &p);
        let mut streaming = StreamingVoicing::new(sr, p);
        for (i, (l, r)) in left.iter().zip(right.iter()).enumerate() {
            let (got_l, got_r) = streaming.tick(*l, *r);
            assert!((got_l - want_l[i]).abs() < 1e-12, "left sample {i}");
            assert!((got_r - want_r[i]).abs() < 1e-12, "right sample {i}");
        }
    }
}
