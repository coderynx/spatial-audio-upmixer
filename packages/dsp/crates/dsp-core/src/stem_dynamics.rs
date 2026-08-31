//! Constrained linked downward compression for separated stems.

use serde::Deserialize;

use crate::mastering::compressor::CompParams;
use crate::stream::state::{OnePole, StreamingCompressor};

const KNEE_DB: f64 = 9.0;
const MAX_GAIN_REDUCTION_DB: f64 = 6.0;
const SMOOTH_MS: f64 = 5.0;

#[derive(Clone, Copy, Debug, PartialEq, Deserialize)]
pub struct StemDynamicsParams {
    #[serde(default)]
    pub enabled: bool,
    pub threshold_db: f64,
    pub ratio: f64,
    pub attack_ms: f64,
    pub release_ms: f64,
    #[serde(default = "full_mix")]
    pub mix: f64,
}

fn full_mix() -> f64 {
    1.0
}

impl StemDynamicsParams {
    pub fn is_active(self) -> bool {
        self.enabled && self.ratio > 1.0 && self.mix > 0.0
    }

    fn compressor(self) -> CompParams {
        CompParams {
            threshold_db: self.threshold_db,
            ratio: self.ratio,
            attack_ms: self.attack_ms,
            release_ms: self.release_ms,
            knee_db: KNEE_DB,
            makeup_db: 0.0,
            sidechain_hpf_hz: None,
        }
    }
}

pub struct StemDynamics {
    sample_rate: u32,
    params: StemDynamicsParams,
    compressor: StreamingCompressor,
    mix: OnePole,
    gain: OnePole,
    gain_reduction_db: f64,
}

impl StemDynamics {
    pub fn new(sample_rate: u32, params: StemDynamicsParams) -> Self {
        let mix = if params.is_active() { params.mix } else { 0.0 };
        Self {
            sample_rate,
            params,
            compressor: StreamingCompressor::new(params.compressor(), sample_rate, 0),
            mix: OnePole::new_at(SMOOTH_MS, sample_rate as f64, mix),
            gain: OnePole::new_at(SMOOTH_MS, sample_rate as f64, 1.0),
            gain_reduction_db: 0.0,
        }
    }

    pub fn retune(&mut self, sample_rate: u32, params: StemDynamicsParams) {
        self.compressor.retune(params.compressor(), sample_rate);
        self.mix.retune(SMOOTH_MS, sample_rate as f64);
        self.gain.retune(SMOOTH_MS, sample_rate as f64);
        self.sample_rate = sample_rate;
        self.params = params;
    }

    pub fn process(&mut self, channels: &mut [Vec<f64>]) {
        let target_mix = if self.params.is_active() {
            self.params.mix
        } else {
            0.0
        };
        if channels.is_empty() || (target_mix == 0.0 && self.mix.is_settled(0.0)) {
            self.gain_reduction_db = 0.0;
            return;
        }
        let frames = channels.iter().map(Vec::len).min().unwrap_or(0);
        for frame in 0..frames {
            let mut sum_sq = 0.0;
            for channel in channels.iter_mut() {
                if !channel[frame].is_finite() {
                    channel[frame] = 0.0;
                }
                sum_sq += channel[frame] * channel[frame];
            }
            let rms = (sum_sq / channels.len() as f64 + 1e-20).sqrt();
            let (gain, _) = self.compressor.tick(rms);
            let capped = gain.max(10.0_f64.powf(-MAX_GAIN_REDUCTION_DB / 20.0));
            let wet_gain = self.gain.tick(capped);
            let mix = self.mix.tick(target_mix);
            self.gain_reduction_db = -20.0 * wet_gain.max(1e-20).log10();
            for channel in channels.iter_mut() {
                let dry = channel[frame];
                channel[frame] = dry + (dry * wet_gain - dry) * mix;
            }
        }
    }

    pub fn gain_reduction_db(&self) -> f64 {
        self.gain_reduction_db
    }

    pub fn reset(&mut self) {
        self.compressor.reset();
        self.mix.set(if self.params.is_active() {
            self.params.mix
        } else {
            0.0
        });
        self.gain.set(1.0);
        self.gain_reduction_db = 0.0;
    }
}

pub fn process(sample_rate: u32, params: StemDynamicsParams, channels: &mut [Vec<f64>]) {
    StemDynamics::new(sample_rate, params).process(channels);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params() -> StemDynamicsParams {
        StemDynamicsParams {
            enabled: true,
            threshold_db: -18.0,
            ratio: 3.0,
            attack_ms: 5.0,
            release_ms: 80.0,
            mix: 1.0,
        }
    }

    #[test]
    fn cap_and_link_hold_for_hot_stereo() {
        let left = vec![1.0; 48_000];
        let right = vec![0.25; 48_000];
        let balance = left[0] / right[0];
        let mut processor = StemDynamics::new(48_000, params());
        processor.process(&mut [left, right]);
        assert!(processor.gain_reduction_db() <= MAX_GAIN_REDUCTION_DB + 1e-12);
        assert!((processor.gain_reduction_db() - MAX_GAIN_REDUCTION_DB).abs() < 1e-3);
        let channels = &mut [vec![1.0; 48_000], vec![0.25; 48_000]];
        processor.reset();
        processor.process(channels);
        assert!((channels[0][47_999] / channels[1][47_999] - balance).abs() < 1e-12);
    }

    #[test]
    fn blocks_and_live_retune_keep_the_envelope_continuous() {
        let left: Vec<f64> = (0..4096).map(|i| (i as f64 * 0.03).sin() * 0.8).collect();
        let right: Vec<f64> = left.iter().map(|v| v * 0.5).collect();
        let mut whole = [left.clone(), right.clone()];
        StemDynamics::new(48_000, params()).process(&mut whole);

        let mut stream = StemDynamics::new(48_000, params());
        let mut blocked = [Vec::new(), Vec::new()];
        for start in (0..left.len()).step_by(127) {
            let end = (start + 127).min(left.len());
            let mut block = [left[start..end].to_vec(), right[start..end].to_vec()];
            stream.process(&mut block);
            blocked[0].extend_from_slice(&block[0]);
            blocked[1].extend_from_slice(&block[1]);
        }
        for channel in 0..2 {
            for (a, b) in whole[channel].iter().zip(&blocked[channel]) {
                assert!((a - b).abs() < 1e-12);
            }
        }

        let mut live = StemDynamics::new(48_000, params());
        let mut first = [left[..2048].to_vec(), right[..2048].to_vec()];
        live.process(&mut first);
        let mut retuned = params();
        retuned.threshold_db = -30.0;
        live.retune(48_000, retuned);
        let mut second = [left[2048..].to_vec(), right[2048..].to_vec()];
        live.process(&mut second);
        assert!(second.iter().flatten().all(|sample| sample.is_finite()));
        assert!((second[0][0] - first[0][2047]).abs() < 0.1);
    }
}
