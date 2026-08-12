//! Streaming stem-to-speaker-bed routing.
//!
//! Mirrors `separation/stem_router.py::StemRouter.route` block by block: each
//! shaped send carries the filter state and delay line its offline
//! counterpart would have accumulated, so the two agree sample for sample.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_sos, BandType};

use super::conv::StreamingConvolver;
use super::params::{SendParams, SendShape};

/// Fixed-delay ring buffer for the Haas/diffusion sends.
pub struct DelayLine {
    buffer: Vec<f64>,
    write: usize,
}

impl DelayLine {
    pub fn new(delay_samples: usize) -> Self {
        Self { buffer: vec![0.0; delay_samples.max(1)], write: 0 }
    }

    pub fn reset(&mut self) {
        self.buffer.fill(0.0);
        self.write = 0;
    }

    #[inline]
    pub fn tick(&mut self, x: f64) -> f64 {
        let out = self.buffer[self.write];
        self.buffer[self.write] = x;
        self.write = (self.write + 1) % self.buffer.len();
        out
    }
}

/// One shaped send: a filter chain, a delay line, and the diffusion blend.
struct Send {
    filters: Vec<SosFilter>,
    delay: DelayLine,
    blend: f64,
    /// Elevation-EQ gains, when this send is a height send.
    elevation: Option<(f64, f64)>,
}

impl Send {
    fn reset(&mut self) {
        for f in &mut self.filters {
            f.reset();
        }
        self.delay.reset();
    }

    #[inline]
    fn tick(&mut self, x: f64) -> f64 {
        let shaped = match self.elevation {
            None => self.filters[0].tick(x),
            Some((low_gain, high_gain)) => {
                let low = self.filters[0].tick(x);
                let bass_shaped = x - low * (1.0 - low_gain);
                let high = self.filters[1].tick(bass_shaped);
                bass_shaped + high * (high_gain - 1.0)
            }
        };
        let delayed = self.delay.tick(shaped);
        shaped * (1.0 - self.blend) + delayed * self.blend
    }
}

fn delay_samples(sample_rate: u32, delay_ms: f64) -> usize {
    (sample_rate as f64 * delay_ms / 1000.0) as usize
}

/// Per-stem shaping state: the four sends plus the optional stem EQ.
pub struct StemRouteState {
    pub eq: Option<(StreamingConvolver, StreamingConvolver)>,
    surround: [Send; 2],
    height: [Send; 2],
}

impl StemRouteState {
    pub fn new(sample_rate: u32, p: &SendParams, eq_fir: &[f64]) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let surround_hp = butter_sos(2, p.surround_bass_cutoff_hz / nyq, BandType::High);
        let height_lp = butter_sos(1, p.height_low_rolloff_hz / nyq, BandType::Low);
        let height_hp = butter_sos(2, p.height_crossover_hz / nyq, BandType::High);

        let surround_send = |delay_ms: f64| Send {
            filters: vec![SosFilter::from_flat(&surround_hp)],
            delay: DelayLine::new(delay_samples(sample_rate, delay_ms)),
            blend: p.diffuse_blend,
            elevation: None,
        };
        let height_send = |delay_ms: f64| Send {
            filters: vec![
                SosFilter::from_flat(&height_lp),
                SosFilter::from_flat(&height_hp),
            ],
            delay: DelayLine::new(delay_samples(sample_rate, delay_ms)),
            blend: p.diffuse_blend,
            elevation: Some((p.height_low_rolloff_gain, p.height_high_shelf_gain)),
        };

        Self {
            eq: (!eq_fir.is_empty()).then(|| {
                (
                    StreamingConvolver::new(eq_fir.to_vec()),
                    StreamingConvolver::new(eq_fir.to_vec()),
                )
            }),
            surround: [surround_send(p.surround_haas_ms.0), surround_send(p.surround_haas_ms.1)],
            height: [height_send(p.height_haas_ms.0), height_send(p.height_haas_ms.1)],
        }
    }

    pub fn reset(&mut self) {
        if let Some((l, r)) = &mut self.eq {
            l.reset();
            r.reset();
        }
        for s in self.surround.iter_mut().chain(self.height.iter_mut()) {
            s.reset();
        }
    }

    /// Shape one stereo sample into the seven signals a speaker can draw on.
    #[inline]
    pub fn tick(&mut self, left: f64, right: f64) -> [f64; 7] {
        let mono = (left + right) * 0.5;
        [
            left,
            right,
            mono,
            self.surround[0].tick(left),
            self.surround[1].tick(right),
            self.height[0].tick(left),
            self.height[1].tick(right),
        ]
    }
}

/// Index into [`StemRouteState::tick`]'s output for a given shape.
pub fn shape_index(shape: SendShape) -> usize {
    match shape {
        SendShape::Left => 0,
        SendShape::Right => 1,
        SendShape::Mono => 2,
        SendShape::SurroundLeft => 3,
        SendShape::SurroundRight => 4,
        SendShape::HeightLeft => 5,
        SendShape::HeightRight => 6,
    }
}

/// The LFE bus: stems sum in dry, then the whole bus is filtered once.
pub struct LfeBus {
    filter: SosFilter,
    gain: f64,
}

impl LfeBus {
    pub fn new(sample_rate: u32, p: &SendParams) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        Self {
            filter: SosFilter::from_flat(&butter_sos(
                p.lfe_filter_order,
                p.lfe_cutoff_hz / nyq,
                BandType::Low,
            )),
            gain: p.lfe_gain,
        }
    }

    pub fn reset(&mut self) {
        self.filter.reset();
    }

    #[inline]
    pub fn tick(&mut self, summed: f64) -> f64 {
        self.filter.tick(summed) * self.gain
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn send_params() -> SendParams {
        SendParams {
            surround_bass_cutoff_hz: 250.0,
            surround_haas_ms: (31.0, 37.0),
            height_haas_ms: (23.0, 29.0),
            diffuse_blend: 0.55,
            height_low_rolloff_hz: 150.0,
            height_low_rolloff_gain: 0.15,
            height_crossover_hz: 3000.0,
            height_high_shelf_gain: 1.5,
            lfe_cutoff_hz: 120.0,
            lfe_filter_order: 4,
            lfe_gain: 0.31622776601683794,
        }
    }

    #[test]
    fn delay_line_holds_a_sample_for_its_whole_length() {
        let mut d = DelayLine::new(3);
        assert_eq!(d.tick(1.0), 0.0);
        assert_eq!(d.tick(0.0), 0.0);
        assert_eq!(d.tick(0.0), 0.0);
        assert_eq!(d.tick(0.0), 1.0);
    }

    #[test]
    fn surround_send_matches_the_offline_highpass_and_blend() {
        use crate::kernels::biquad::sosfilt;
        use crate::routing::sends::diffuse_send;

        let sr = 48_000;
        let signal: Vec<f64> = (0..9600).map(|i| (i as f64 * 0.04).sin()).collect();
        let p = send_params();

        let mut state = StemRouteState::new(sr, &p, &[]);
        let got: Vec<f64> = signal.iter().map(|v| state.tick(*v, 0.0)[3]).collect();

        let hp = butter_sos(2, p.surround_bass_cutoff_hz / (sr as f64 / 2.0), BandType::High);
        let want = diffuse_send(&sosfilt(&hp, &signal), sr, p.surround_haas_ms.0, p.diffuse_blend);

        for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
            assert!((a - b).abs() < 1e-12, "sample {i}: {a} vs {b}");
        }
    }

    #[test]
    fn height_send_matches_the_offline_elevation_eq() {
        use crate::routing::sends::{diffuse_send, elevation_eq};

        let sr = 48_000;
        let signal: Vec<f64> = (0..9600).map(|i| (i as f64 * 0.07).sin()).collect();
        let p = send_params();

        let mut state = StemRouteState::new(sr, &p, &[]);
        let got: Vec<f64> = signal.iter().map(|v| state.tick(*v, 0.0)[5]).collect();

        let shaped = elevation_eq(
            &signal, sr, p.height_low_rolloff_hz, p.height_low_rolloff_gain,
            p.height_crossover_hz, p.height_high_shelf_gain,
        );
        let want = diffuse_send(&shaped, sr, p.height_haas_ms.0, p.diffuse_blend);

        for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
            assert!((a - b).abs() < 1e-12, "sample {i}: {a} vs {b}");
        }
    }

    #[test]
    fn lfe_bus_matches_the_offline_lowpass_and_gain() {
        use crate::kernels::biquad::sosfilt;

        let sr = 48_000;
        let signal: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.02).sin()).collect();
        let p = send_params();

        let mut bus = LfeBus::new(sr, &p);
        let got: Vec<f64> = signal.iter().map(|v| bus.tick(*v)).collect();

        let lp = butter_sos(p.lfe_filter_order, p.lfe_cutoff_hz / (sr as f64 / 2.0), BandType::Low);
        let want: Vec<f64> = sosfilt(&lp, &signal).iter().map(|v| v * p.lfe_gain).collect();

        for (a, b) in got.iter().zip(want.iter()) {
            assert!((a - b).abs() < 1e-12);
        }
    }
}
