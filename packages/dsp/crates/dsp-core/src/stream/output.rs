//! Collapse of the mastered bed to whatever the listener is on: the discrete
//! bed itself, a binaural render, a crosstalk-cancelled speaker pair, or the
//! BS.775 stereo downmix.
//!
//! Every FIR here streams, so the collapse is the same convolution the export
//! performs rather than an approximation of it.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_bandpass_sos, butter_sos, BandType};
use crate::spatial::ambisonics::{encode_gains, N_ACN_CHANNELS};
use crate::spatial::downmix::{soft_limit, stereo_pair, DownmixRole};
use crate::spatial::voicing::VoicingParams;

use super::conv::StreamingConvolver;
use super::params::{EngineParams, OutputMode, SpeakerParams};

/// The voicing chain with carried filter state.
pub struct StreamingVoicing {
    crossfeed_amount: f64,
    bass_gain: f64,
    air_gain: f64,
    presence_gain: f64,
    stereo_widen: f64,
    crossfeed: [SosFilter; 2],
    bass: [SosFilter; 2],
    air: [SosFilter; 2],
    presence: [SosFilter; 2],
}

impl StreamingVoicing {
    pub fn new(sample_rate: u32, p: VoicingParams) -> Self {
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
            crossfeed_amount: p.crossfeed_amount,
            bass_gain: 10.0_f64.powf(p.bass_shelf_gain_db / 20.0) - 1.0,
            air_gain: 10.0_f64.powf(p.air_shelf_gain_db / 20.0) - 1.0,
            presence_gain: 10.0_f64.powf(p.presence_gain_db / 20.0) - 1.0,
            stereo_widen: p.stereo_widen,
            crossfeed: pair(&crossfeed_sos),
            bass: pair(&bass_sos),
            air: pair(&air_sos),
            presence: pair(&presence_sos),
        }
    }

    fn reset(&mut self) {
        for filter in self
            .crossfeed
            .iter_mut()
            .chain(self.bass.iter_mut())
            .chain(self.air.iter_mut())
            .chain(self.presence.iter_mut())
        {
            filter.reset();
        }
    }

    #[inline]
    pub fn tick(&mut self, left: f64, right: f64) -> (f64, f64) {
        let (mut l, mut r) = (left, right);

        if self.crossfeed_amount > 0.0 {
            let bleed_l = self.crossfeed[0].tick(l);
            let bleed_r = self.crossfeed[1].tick(r);
            let a = self.crossfeed_amount;
            let next_l = l * (1.0 - a) + bleed_r * a;
            let next_r = r * (1.0 - a) + bleed_l * a;
            l = next_l;
            r = next_r;
        }
        if self.bass_gain != 0.0 {
            l += self.bass[0].tick(l) * self.bass_gain;
            r += self.bass[1].tick(r) * self.bass_gain;
        }
        if self.air_gain != 0.0 {
            l += self.air[0].tick(l) * self.air_gain;
            r += self.air[1].tick(r) * self.air_gain;
        }
        if self.presence_gain != 0.0 {
            l += self.presence[0].tick(l) * self.presence_gain;
            r += self.presence[1].tick(r) * self.presence_gain;
        }
        if self.stereo_widen != 0.0 {
            let mid = (l + r) * 0.5;
            let side = (l - r) * 0.5 * (1.0 + self.stereo_widen);
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
    hoa: Vec<Vec<f64>>,
    stereo: [Vec<f64>; 2],
    work: [Vec<f64>; 4],
}

fn build_downmix(
    speakers: &[SpeakerParams],
    surround_coeff: f64,
    height_coeff: f64,
) -> Vec<Option<(f64, f64)>> {
    speakers
        .iter()
        .map(|s| {
            DownmixRole::from_name(&s.name)
                .map(|role| stereo_pair(role, surround_coeff, height_coeff))
        })
        .collect()
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
    pub fn new(
        sample_rate: u32,
        params: &EngineParams,
        decode_taps: &[f64],
        xtc_taps: &[f64],
    ) -> Self {
        let n_channels = params.speakers.len();
        let encoders = params
            .speakers
            .iter()
            .enumerate()
            .map(|(i, s)| {
                (params.lfe_index != Some(i)).then(|| encode_gains(s.azimuth_rad, s.elevation_rad))
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
            voicing: params
                .voicing
                .map(|v| StreamingVoicing::new(sample_rate, v)),
            downmix: build_downmix(
                &params.speakers,
                params.surround_downmix_coeff,
                params.height_downmix_coeff,
            ),
            soft_limit_threshold: params.soft_limit_threshold,
            hoa: vec![Vec::new(); N_ACN_CHANNELS],
            stereo: [Vec::new(), Vec::new()],
            work: std::array::from_fn(|_| Vec::new()),
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
                (params.lfe_index != Some(i)).then(|| encode_gains(s.azimuth_rad, s.elevation_rad))
            })
            .collect();
        self.downmix = build_downmix(
            &params.speakers,
            params.surround_downmix_coeff,
            params.height_downmix_coeff,
        );
        self.soft_limit_threshold = params.soft_limit_threshold;
        self.voicing = params
            .voicing
            .map(|v| StreamingVoicing::new(sample_rate, v));
    }

    pub fn reset(&mut self) {
        for filters in &mut self.decode {
            for filter in filters {
                filter.reset();
            }
        }
        if let Some(matrix) = &mut self.xtc {
            for row in matrix {
                for filter in row {
                    filter.reset();
                }
            }
        }
        if let Some(voicing) = &mut self.voicing {
            voicing.reset();
        }
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
                self.downmix_stereo(bed, frames);
                emit_stereo(
                    &self.stereo[0],
                    &self.stereo[1],
                    gain,
                    self.soft_limit_threshold,
                    out,
                );
            }
            OutputMode::Binaural => {
                self.render_binaural(bed, frames);
                emit_stereo(
                    &self.stereo[0],
                    &self.stereo[1],
                    gain,
                    self.soft_limit_threshold,
                    out,
                );
            }
            OutputMode::Transaural => {
                self.render_binaural(bed, frames);
                if let Some(matrix) = &mut self.xtc {
                    let [a, b, c, d] = &mut self.work;
                    matrix[0][0].process_into(&self.stereo[0], a);
                    matrix[0][1].process_into(&self.stereo[1], b);
                    matrix[1][0].process_into(&self.stereo[0], c);
                    matrix[1][1].process_into(&self.stereo[1], d);
                    for i in 0..frames {
                        self.stereo[0][i] = a[i] + b[i];
                        self.stereo[1][i] = c[i] + d[i];
                    }
                }
                emit_stereo(
                    &self.stereo[0],
                    &self.stereo[1],
                    gain,
                    self.soft_limit_threshold,
                    out,
                );
            }
        }
    }

    fn downmix_stereo(&mut self, bed: &[Vec<f64>], frames: usize) {
        self.stereo[0].resize(frames, 0.0);
        self.stereo[1].resize(frames, 0.0);
        self.stereo[0].fill(0.0);
        self.stereo[1].fill(0.0);
        for (channel, gains) in self.downmix.iter().enumerate() {
            let Some((gl, gr)) = gains else { continue };
            for i in 0..frames {
                self.stereo[0][i] += bed[channel][i] * gl;
                self.stereo[1][i] += bed[channel][i] * gr;
            }
        }
    }

    /// Encode every positional speaker to ambisonics, decode to the ears, add
    /// LFE *before* voicing (ledger D11), then voice.
    fn render_binaural(&mut self, bed: &[Vec<f64>], frames: usize) {
        for channel in &mut self.hoa {
            channel.resize(frames, 0.0);
            channel.fill(0.0);
        }
        for (channel, gains) in self.encoders.iter().enumerate() {
            let Some(gains) = gains else { continue };
            let source = &bed[channel];
            for (acn, gain) in gains.iter().enumerate() {
                if *gain == 0.0 {
                    continue;
                }
                let target = &mut self.hoa[acn];
                for i in 0..frames {
                    target[i] += source[i] * gain;
                }
            }
        }

        self.stereo[0].resize(frames, 0.0);
        self.stereo[1].resize(frames, 0.0);
        self.stereo[0].fill(0.0);
        self.stereo[1].fill(0.0);
        for (acn, filters) in self.decode.iter_mut().enumerate() {
            let (left, right) = self.work.split_at_mut(1);
            StreamingConvolver::process_pair_into(
                filters,
                &self.hoa[acn],
                &mut left[0],
                &mut right[0],
            );
            for i in 0..frames {
                self.stereo[0][i] += left[0][i];
                self.stereo[1][i] += right[0][i];
            }
        }

        if let Some(lfe) = self.lfe_index {
            for i in 0..frames {
                self.stereo[0][i] += bed[lfe][i];
                self.stereo[1][i] += bed[lfe][i];
            }
        }

        if let Some(voicing) = &mut self.voicing {
            for i in 0..frames {
                let (l, r) = voicing.tick(self.stereo[0][i], self.stereo[1][i]);
                self.stereo[0][i] = l;
                self.stereo[1][i] = r;
            }
        }
    }
}

/// The collapse correction is applied before the soft limiter, matching
/// `render_binaural_delivery`: normalize first, then let the limiter act only
/// as a true-peak safety net.
fn emit_stereo(
    left: &[f64],
    right: &[f64],
    gain: f64,
    soft_limit_threshold: f64,
    out: &mut [Vec<f64>],
) {
    out[0].clear();
    out[0].extend(left.iter().map(|sample| sample * gain));
    out[1].clear();
    out[1].extend(right.iter().map(|sample| sample * gain));
    if soft_limit_threshold > 0.0 {
        soft_limit(&mut out[0], soft_limit_threshold);
        soft_limit(&mut out[1], soft_limit_threshold);
    }
    for extra in out.iter_mut().skip(2) {
        extra.clear();
    }
}
