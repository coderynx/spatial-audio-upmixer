//! Compact, stateful per-stem EQ shared by export and preview.

use serde::Deserialize;

use crate::kernels::biquad::{high_shelf_sos, low_shelf_sos, peaking_sos, SosFilter};
use crate::kernels::butter::{butter_sos, BandType};

const MIX_RAMP_MS: f64 = 5.0;

#[derive(Clone, Copy, Debug, PartialEq, Deserialize)]
pub struct FilterParams {
    #[serde(default)]
    pub enabled: bool,
    pub freq_hz: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Deserialize)]
pub struct BandParams {
    #[serde(default)]
    pub enabled: bool,
    pub freq_hz: f64,
    pub gain_db: f64,
    pub q: f64,
}

#[derive(Clone, Debug, PartialEq, Deserialize)]
pub struct StemEqParams {
    #[serde(default)]
    pub bypass: bool,
    #[serde(default)]
    pub highpass: Option<FilterParams>,
    #[serde(default)]
    pub low_shelf: Option<BandParams>,
    #[serde(default)]
    pub bell_1: Option<BandParams>,
    #[serde(default)]
    pub bell_2: Option<BandParams>,
    #[serde(default)]
    pub high_shelf: Option<BandParams>,
    #[serde(default)]
    pub lowpass: Option<FilterParams>,
    #[serde(default = "full_mix")]
    pub mix: f64,
}

fn full_mix() -> f64 {
    1.0
}

fn normalized(freq_hz: f64, sample_rate: u32) -> f64 {
    (freq_hz / (sample_rate as f64 * 0.5)).clamp(1e-5, 0.999)
}

fn gain(value: f64) -> f64 {
    10.0_f64.powf(value / 20.0)
}

impl StemEqParams {
    pub fn is_active(&self) -> bool {
        !self.bypass
            && self.mix > 0.0
            && [
                self.highpass.map(|p| p.enabled),
                self.low_shelf.map(|p| p.enabled && p.gain_db != 0.0),
                self.bell_1.map(|p| p.enabled && p.gain_db != 0.0),
                self.bell_2.map(|p| p.enabled && p.gain_db != 0.0),
                self.high_shelf.map(|p| p.enabled && p.gain_db != 0.0),
                self.lowpass.map(|p| p.enabled),
            ]
            .into_iter()
            .flatten()
            .any(|enabled| enabled)
    }
}

struct Channel {
    highpass: SosFilter,
    low_shelf: SosFilter,
    bell_1: SosFilter,
    bell_2: SosFilter,
    high_shelf: SosFilter,
    lowpass: SosFilter,
}

impl Channel {
    fn new(sample_rate: u32, p: &StemEqParams) -> Self {
        Self {
            highpass: SosFilter::from_flat(&highpass_sos(sample_rate, p.highpass)),
            low_shelf: SosFilter::from_flat(&low_shelf_sos_for(sample_rate, p.low_shelf)),
            bell_1: SosFilter::from_flat(&bell_sos(sample_rate, p.bell_1)),
            bell_2: SosFilter::from_flat(&bell_sos(sample_rate, p.bell_2)),
            high_shelf: SosFilter::from_flat(&high_shelf_sos_for(sample_rate, p.high_shelf)),
            lowpass: SosFilter::from_flat(&lowpass_sos(sample_rate, p.lowpass)),
        }
    }

    fn retune(&mut self, sample_rate: u32, p: &StemEqParams) {
        self.highpass
            .retune_flat(&highpass_sos(sample_rate, p.highpass));
        self.low_shelf
            .retune_flat(&low_shelf_sos_for(sample_rate, p.low_shelf));
        self.bell_1.retune_flat(&bell_sos(sample_rate, p.bell_1));
        self.bell_2.retune_flat(&bell_sos(sample_rate, p.bell_2));
        self.high_shelf
            .retune_flat(&high_shelf_sos_for(sample_rate, p.high_shelf));
        self.lowpass
            .retune_flat(&lowpass_sos(sample_rate, p.lowpass));
    }

    fn tick(&mut self, input: f64, p: &StemEqParams) -> f64 {
        let mut value = input;
        if p.highpass.is_some_and(|band| band.enabled) {
            value = self.highpass.tick(value);
        }
        if p.low_shelf
            .is_some_and(|band| band.enabled && band.gain_db != 0.0)
        {
            value = self.low_shelf.tick(value);
        }
        if p.bell_1
            .is_some_and(|band| band.enabled && band.gain_db != 0.0)
        {
            value = self.bell_1.tick(value);
        }
        if p.bell_2
            .is_some_and(|band| band.enabled && band.gain_db != 0.0)
        {
            value = self.bell_2.tick(value);
        }
        if p.high_shelf
            .is_some_and(|band| band.enabled && band.gain_db != 0.0)
        {
            value = self.high_shelf.tick(value);
        }
        if p.lowpass.is_some_and(|band| band.enabled) {
            value = self.lowpass.tick(value);
        }
        value
    }
}

fn highpass_sos(sample_rate: u32, p: Option<FilterParams>) -> Vec<[f64; 6]> {
    butter_sos(
        2,
        normalized(p.map_or(20.0, |v| v.freq_hz), sample_rate),
        BandType::High,
    )
}
fn lowpass_sos(sample_rate: u32, p: Option<FilterParams>) -> Vec<[f64; 6]> {
    butter_sos(
        2,
        normalized(p.map_or(20000.0, |v| v.freq_hz), sample_rate),
        BandType::Low,
    )
}
fn low_shelf_sos_for(sample_rate: u32, p: Option<BandParams>) -> [[f64; 6]; 1] {
    let p = p.unwrap_or(BandParams {
        enabled: false,
        freq_hz: 100.0,
        gain_db: 0.0,
        q: 0.707,
    });
    [low_shelf_sos(
        normalized(p.freq_hz, sample_rate),
        p.q,
        gain(p.gain_db),
    )]
}
fn high_shelf_sos_for(sample_rate: u32, p: Option<BandParams>) -> [[f64; 6]; 1] {
    let p = p.unwrap_or(BandParams {
        enabled: false,
        freq_hz: 8000.0,
        gain_db: 0.0,
        q: 0.707,
    });
    [high_shelf_sos(
        normalized(p.freq_hz, sample_rate),
        p.q,
        gain(p.gain_db),
    )]
}
fn bell_sos(sample_rate: u32, p: Option<BandParams>) -> [[f64; 6]; 1] {
    let p = p.unwrap_or(BandParams {
        enabled: false,
        freq_hz: 1000.0,
        gain_db: 0.0,
        q: 1.0,
    });
    [peaking_sos(
        normalized(p.freq_hz, sample_rate),
        p.q,
        gain(p.gain_db),
    )]
}

pub struct StemEq {
    sample_rate: u32,
    params: StemEqParams,
    left: Channel,
    right: Channel,
    mix: f64,
    mix_alpha: f64,
}

impl StemEq {
    pub fn new(sample_rate: u32, params: StemEqParams) -> Self {
        let mix_alpha = 1.0 - (-1.0 / (MIX_RAMP_MS * sample_rate as f64 / 1000.0)).exp();
        let mix = if params.is_active() { params.mix } else { 0.0 };
        Self {
            sample_rate,
            left: Channel::new(sample_rate, &params),
            right: Channel::new(sample_rate, &params),
            params,
            mix,
            mix_alpha,
        }
    }

    pub fn retune(&mut self, sample_rate: u32, params: StemEqParams) {
        self.left.retune(sample_rate, &params);
        self.right.retune(sample_rate, &params);
        self.params = params;
        self.sample_rate = sample_rate;
        self.mix_alpha = 1.0 - (-1.0 / (MIX_RAMP_MS * sample_rate as f64 / 1000.0)).exp();
    }

    pub fn process_stereo(&mut self, left: &mut [f64], right: &mut [f64]) {
        let target = if self.params.is_active() {
            self.params.mix
        } else {
            0.0
        };
        if target == 0.0 && self.mix.abs() < 1e-12 {
            return;
        }
        for (l, r) in left.iter_mut().zip(right.iter_mut()) {
            self.mix += self.mix_alpha * (target - self.mix);
            let dry_l = *l;
            let dry_r = *r;
            *l = dry_l + (self.left.tick(dry_l, &self.params) - dry_l) * self.mix;
            *r = dry_r + (self.right.tick(dry_r, &self.params) - dry_r) * self.mix;
        }
    }

    pub fn process_mono(&mut self, signal: &mut [f64]) {
        let target = if self.params.is_active() {
            self.params.mix
        } else {
            0.0
        };
        if target == 0.0 && self.mix.abs() < 1e-12 {
            return;
        }
        for value in signal {
            self.mix += self.mix_alpha * (target - self.mix);
            let dry = *value;
            *value = dry + (self.left.tick(dry, &self.params) - dry) * self.mix;
        }
    }

    pub fn reset(&mut self) {
        self.left = Channel::new(self.sample_rate, &self.params);
        self.right = Channel::new(self.sample_rate, &self.params);
    }
}

pub fn process_stereo(sample_rate: u32, params: StemEqParams, left: &mut [f64], right: &mut [f64]) {
    StemEq::new(sample_rate, params).process_stereo(left, right);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn settings() -> StemEqParams {
        StemEqParams {
            bypass: false,
            highpass: None,
            low_shelf: None,
            bell_1: None,
            bell_2: None,
            high_shelf: None,
            lowpass: None,
            mix: 1.0,
        }
    }

    fn level(signal: &[f64]) -> f64 {
        (signal.iter().map(|value| value * value).sum::<f64>() / signal.len() as f64).sqrt()
    }

    #[test]
    fn bypass_and_zero_mix_are_exact_noops() {
        let signal = vec![0.2, -0.4, 0.1];
        let mut bypass = settings();
        bypass.bypass = true;
        let mut zero_mix = settings();
        zero_mix.mix = 0.0;
        for params in [bypass, zero_mix] {
            let mut out = signal.clone();
            StemEq::new(48_000, params).process_mono(&mut out);
            assert_eq!(out, signal);
        }
    }

    #[test]
    fn filters_shape_tones_and_keep_stereo_matched() {
        let sr = 48_000;
        let tone = |hz: f64| {
            (0..sr)
                .map(|i| (2.0 * std::f64::consts::PI * hz * i as f64 / sr as f64).sin())
                .collect::<Vec<_>>()
        };
        let mut hpf = settings();
        hpf.highpass = Some(FilterParams {
            enabled: true,
            freq_hz: 500.0,
        });
        let mut low = tone(80.0);
        StemEq::new(sr, hpf).process_mono(&mut low);
        assert!(level(&low[sr as usize / 2..]) < 0.1);
        let mut bell = settings();
        bell.bell_1 = Some(BandParams {
            enabled: true,
            freq_hz: 1000.0,
            gain_db: 6.0,
            q: 1.0,
        });
        let dry = tone(1000.0);
        let mut left = dry.clone();
        let mut right = dry.clone();
        StemEq::new(sr, bell).process_stereo(&mut left, &mut right);
        assert!(level(&left[sr as usize / 2..]) / level(&dry[sr as usize / 2..]) > 1.8);
        assert_eq!(left, right);
        let mut lpf = settings();
        lpf.lowpass = Some(FilterParams {
            enabled: true,
            freq_hz: 1000.0,
        });
        let mut high = tone(8000.0);
        StemEq::new(sr, lpf).process_mono(&mut high);
        assert!(level(&high[sr as usize / 2..]) < 0.05);
    }

    #[test]
    fn blocks_and_live_retune_are_continuous() {
        let sr = 48_000;
        let input = (0..4096)
            .map(|i| (2.0 * std::f64::consts::PI * 440.0 * i as f64 / sr as f64).sin())
            .collect::<Vec<_>>();
        let mut params = settings();
        params.low_shelf = Some(BandParams {
            enabled: true,
            freq_hz: 150.0,
            gain_db: 3.0,
            q: 0.707,
        });
        let mut whole = input.clone();
        StemEq::new(sr, params.clone()).process_mono(&mut whole);
        let mut streamed = input;
        let mut state = StemEq::new(sr, params.clone());
        state.process_mono(&mut streamed[..1000]);
        state.process_mono(&mut streamed[1000..]);
        assert_eq!(whole, streamed);
        params.bell_1 = Some(BandParams {
            enabled: true,
            freq_hz: 2000.0,
            gain_db: 6.0,
            q: 8.0,
        });
        let before = streamed[999];
        state.retune(sr, params);
        state.process_mono(&mut streamed[1000..]);
        assert!((streamed[1000] - before).abs() < 0.3);
    }
}
