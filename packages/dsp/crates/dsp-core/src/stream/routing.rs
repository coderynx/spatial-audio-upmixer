//! Streaming stem-to-speaker-bed routing.
//!
//! Mirrors `separation/stem_router.py::StemRouter.route` block by block: each
//! shaped send carries the filter state and decorrelator history its offline
//! counterpart would have accumulated, so the two agree sample for sample.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_sos, linkwitz_riley_lowpass_sos, BandType};
use crate::routing::decorrelate::{
    velvet_pair_seeded, VelvetFir, VelvetLine, VELVET_SEED, VELVET_SEED_HEIGHT,
};
use crate::routing::sends::directional_band_sos;
use crate::routing::transient::MultibandDucker;

use super::conv::StreamingConvolver;
use super::params::{SendParams, SendShape};

/// One shaped send: a filter chain and one side of a decorrelator pair.
struct Send {
    filters: Vec<SosFilter>,
    velvet: VelvetLine,
    /// Elevation-EQ gains, when this send is a height send: low rolloff,
    /// high shelf, directional band.
    elevation: Option<(f64, f64, f64)>,
}

impl Send {
    fn reset(&mut self) {
        for f in &mut self.filters {
            f.reset();
        }
        self.velvet.reset();
    }

    /// Re-derive a surround send's highpass in place.
    fn retune_surround(&mut self, sample_rate: u32, p: &SendParams) {
        let nyq = sample_rate as f64 / 2.0;
        let hp = butter_sos(2, p.surround_bass_cutoff_hz / nyq, BandType::High);
        self.filters[0].retune_flat(&hp);
    }

    /// Re-derive a height send's low-rolloff/crossover/band in place.
    fn retune_height(&mut self, sample_rate: u32, p: &SendParams) {
        let nyq = sample_rate as f64 / 2.0;
        let lp = butter_sos(1, p.height_low_rolloff_hz / nyq, BandType::Low);
        let hp = butter_sos(2, p.height_crossover_hz / nyq, BandType::High);
        let band = directional_band_sos(
            p.height_directional_band_hz,
            sample_rate,
            p.height_directional_band_gain,
        );
        self.filters[0].retune_flat(&lp);
        self.filters[1].retune_flat(&hp);
        self.filters[2].retune_flat(&[band]);
        self.elevation = Some((
            p.height_low_rolloff_gain,
            p.height_high_shelf_gain,
            p.height_directional_band_gain,
        ));
    }

    #[inline]
    fn shape(&mut self, x: f64) -> f64 {
        match self.elevation {
            None => self.filters[0].tick(x),
            Some((low_gain, high_gain, band_gain)) => {
                let low = self.filters[0].tick(x);
                let bass_shaped = x - low * (1.0 - low_gain);
                let high = self.filters[1].tick(bass_shaped);
                let shelved = bass_shaped + high * (high_gain - 1.0);
                if band_gain == 1.0 {
                    shelved
                } else {
                    self.filters[2].tick(shelved)
                }
            }
        }
    }

    fn process(&mut self, input: &[f64], out: &mut Vec<f64>) {
        out.clear();
        out.reserve(input.len());
        for x in input {
            let shaped = self.shape(*x);
            out.push(shaped);
        }
        self.velvet.process(out);
    }
}

/// Per-stem shaping state: the four sends plus the optional stem EQ.
pub struct StemRouteState {
    pub eq: Option<(StreamingConvolver, StreamingConvolver)>,
    surround: [Send; 2],
    height: [Send; 2],
    /// One ducker feeds both send pairs: its state depends only on the stem's
    /// input, so a single trajectory is what the offline path's two separate
    /// calls each reproduce.
    ducker: MultibandDucker,
    ducked: [Vec<f64>; 2],
    /// Last block's shaped sends: surround L/R then height L/R.
    shaped: [Vec<f64>; 4],
}

impl StemRouteState {
    pub fn new(sample_rate: u32, p: &SendParams, eq_fir: &[f64]) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let surround_hp = butter_sos(2, p.surround_bass_cutoff_hz / nyq, BandType::High);
        let height_lp = butter_sos(1, p.height_low_rolloff_hz / nyq, BandType::Low);
        let height_hp = butter_sos(2, p.height_crossover_hz / nyq, BandType::High);
        let height_band = directional_band_sos(
            p.height_directional_band_hz,
            sample_rate,
            p.height_directional_band_gain,
        );

        let (surround_l, surround_r) = velvet_pair_seeded(sample_rate, VELVET_SEED);
        let (height_l, height_r) = velvet_pair_seeded(sample_rate, VELVET_SEED_HEIGHT);

        let surround_send = |fir: &VelvetFir| Send {
            filters: vec![SosFilter::from_flat(&surround_hp)],
            velvet: VelvetLine::new(fir),
            elevation: None,
        };
        let height_send = |fir: &VelvetFir| Send {
            filters: vec![
                SosFilter::from_flat(&height_lp),
                SosFilter::from_flat(&height_hp),
                SosFilter::from_flat(&[height_band]),
            ],
            velvet: VelvetLine::new(fir),
            elevation: Some((
                p.height_low_rolloff_gain,
                p.height_high_shelf_gain,
                p.height_directional_band_gain,
            )),
        };

        Self {
            eq: (!eq_fir.is_empty()).then(|| {
                (
                    StreamingConvolver::new(eq_fir.to_vec()),
                    StreamingConvolver::new(eq_fir.to_vec()),
                )
            }),
            surround: [surround_send(&surround_l), surround_send(&surround_r)],
            height: [height_send(&height_l), height_send(&height_r)],
            ducker: MultibandDucker::new(sample_rate, p.stem_transient_duck),
            ducked: Default::default(),
            shaped: Default::default(),
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
        self.ducker.reset();
    }

    /// Adopt new send shaping and/or a new stem EQ in place, keeping every
    /// filter's carried state and the EQ convolvers' history — a live mix
    /// edit re-derives coefficients rather than restarting this stem's
    /// routing cold. `sends_changed`/`eq_changed` let the caller skip the
    /// (still cheap, but non-zero) work when that half didn't move.
    pub fn retune(
        &mut self,
        sample_rate: u32,
        sends: &SendParams,
        eq_fir: &[f64],
        sends_changed: bool,
        eq_changed: bool,
    ) {
        if sends_changed {
            for s in self.surround.iter_mut() {
                s.retune_surround(sample_rate, sends);
            }
            for s in self.height.iter_mut() {
                s.retune_height(sample_rate, sends);
            }
            self.ducker.retune(sample_rate, sends.stem_transient_duck);
        }
        if eq_changed {
            if eq_fir.is_empty() {
                self.eq = None;
            } else if let Some((l, r)) = &mut self.eq {
                l.retune_kernel(eq_fir.to_vec());
                r.retune_kernel(eq_fir.to_vec());
            } else {
                self.eq = Some((
                    StreamingConvolver::new(eq_fir.to_vec()),
                    StreamingConvolver::new(eq_fir.to_vec()),
                ));
            }
        }
    }

    /// Shape a whole block into the four decorrelated sends, readable through
    /// [`Self::send`].
    ///
    /// A send no speaker draws from is skipped and reads back as the dry
    /// signal, which is what `StemRouter.route`'s `needs_surround` /
    /// `needs_height` guards produce offline. Its filters then start cold if
    /// a later mix edit routes the stem there — the same cold start the
    /// offline path takes on every render.
    pub fn process(&mut self, left: &[f64], right: &[f64], surround: bool, height: bool) {
        let dry = |source: &[f64], out: &mut Vec<f64>| {
            out.clear();
            out.extend_from_slice(source);
        };
        let (left, right) = if self.ducker.depth() > 0.0 {
            self.ducked[0].clear();
            self.ducked[1].clear();
            self.ducked[0].reserve(left.len());
            self.ducked[1].reserve(right.len());
            for (l, r) in left.iter().zip(right.iter()) {
                let (dl, dr) = self.ducker.tick(*l, *r);
                self.ducked[0].push(dl);
                self.ducked[1].push(dr);
            }
            (&self.ducked[0][..], &self.ducked[1][..])
        } else {
            (left, right)
        };
        for (i, source) in [left, right].into_iter().enumerate() {
            if surround {
                self.surround[i].process(source, &mut self.shaped[i]);
            } else {
                dry(source, &mut self.shaped[i]);
            }
            if height {
                self.height[i].process(source, &mut self.shaped[2 + i]);
            } else {
                dry(source, &mut self.shaped[2 + i]);
            }
        }
    }

    /// One shaped send of the block [`Self::process`] just filtered.
    #[inline]
    pub fn send(&self, index: usize) -> &[f64] {
        &self.shaped[index]
    }
}

/// Index into the seven signals a speaker can draw on: the three dry shapes,
/// then [`StemRouteState::send`]'s four, offset by three.
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
            filter: SosFilter::from_flat(&linkwitz_riley_lowpass_sos(
                p.lfe_filter_order,
                p.lfe_cutoff_hz / nyq,
            )),
            gain: p.lfe_gain,
        }
    }

    pub fn reset(&mut self) {
        self.filter.reset();
    }

    /// Re-derive the lowpass and gain in place, keeping the filter state.
    pub fn retune(&mut self, sample_rate: u32, p: &SendParams) {
        let nyq = sample_rate as f64 / 2.0;
        let sos = linkwitz_riley_lowpass_sos(p.lfe_filter_order, p.lfe_cutoff_hz / nyq);
        self.filter.retune_flat(&sos);
        self.gain = p.lfe_gain;
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
            height_low_rolloff_hz: 150.0,
            height_low_rolloff_gain: 0.15,
            height_crossover_hz: 3000.0,
            height_high_shelf_gain: 1.5,
            height_directional_band_hz: 8000.0,
            height_directional_band_gain: 1.0,
            stem_transient_duck: 0.0,
            lfe_cutoff_hz: 120.0,
            lfe_filter_order: 4,
            lfe_gain: 0.31622776601683794,
        }
    }

    /// Run a signal through both channels of a route in uneven blocks and
    /// return the four shaped sends, concatenated.
    fn blocked(state: &mut StemRouteState, signal: &[f64]) -> [Vec<f64>; 4] {
        let mut out: [Vec<f64>; 4] = Default::default();
        let mut rest = signal;
        for size in [333usize, 999, 128].iter().cycle() {
            if rest.is_empty() {
                break;
            }
            let n = (*size).min(rest.len());
            state.process(&rest[..n], &rest[..n], true, true);
            for (index, side) in out.iter_mut().enumerate() {
                side.extend_from_slice(state.send(index));
            }
            rest = &rest[n..];
        }
        out
    }

    #[test]
    fn surround_sends_match_the_offline_highpass_and_velvet_pair() {
        use crate::kernels::biquad::sosfilt;

        let sr = 48_000;
        let signal: Vec<f64> = (0..9600).map(|i| (i as f64 * 0.04).sin()).collect();
        let p = send_params();

        // Blocked in ragged sizes: the shaped sends must not depend on how
        // the render callback happens to chop the stream up.
        let mut state = StemRouteState::new(sr, &p, &[]);
        let got = blocked(&mut state, &signal);

        let hp = butter_sos(2, p.surround_bass_cutoff_hz / (sr as f64 / 2.0), BandType::High);
        let shaped = sosfilt(&hp, &signal);
        let (left, right) = velvet_pair_seeded(sr, VELVET_SEED);

        for (index, fir) in [(3, left), (4, right)] {
            let want = fir.process(&shaped);
            for (i, (a, b)) in got[index - 3].iter().zip(want.iter()).enumerate() {
                assert!((a - b).abs() < 1e-12, "shape {index} sample {i}: {a} vs {b}");
            }
        }
    }

    #[test]
    fn height_sends_match_the_offline_elevation_eq_and_velvet_pair() {
        use crate::routing::sends::elevation_eq;

        let sr = 48_000;
        let signal: Vec<f64> = (0..9600).map(|i| (i as f64 * 0.07).sin()).collect();

        // The default skips the band section, a lifted band runs it.
        for band_gain in [1.0, 1.6] {
            let p = SendParams { height_directional_band_gain: band_gain, ..send_params() };

            let mut state = StemRouteState::new(sr, &p, &[]);
            let got = blocked(&mut state, &signal);

            let shaped = elevation_eq(
                &signal, sr, p.height_low_rolloff_hz, p.height_low_rolloff_gain,
                p.height_crossover_hz, p.height_high_shelf_gain,
                p.height_directional_band_hz, p.height_directional_band_gain,
            );
            let (left, right) = velvet_pair_seeded(sr, VELVET_SEED_HEIGHT);

            for (index, fir) in [(5, left), (6, right)] {
                let want = fir.process(&shaped);
                for (i, (a, b)) in got[index - 3].iter().zip(want.iter()).enumerate() {
                    assert!((a - b).abs() < 1e-12, "band {band_gain} shape {index} sample {i}: {a} vs {b}");
                }
            }
        }
    }

    /// The duck must land on the send input, before the filters and the
    /// velvet line, exactly as `StemRouter.route` orders it offline — and
    /// blocked ragged, since the preview chooses the block size.
    #[test]
    fn ducked_sends_match_the_offline_duck_then_shape_order() {
        use crate::kernels::biquad::sosfilt;
        use crate::routing::transient::transient_duck;

        let sr = 48_000;
        let signal: Vec<f64> = (0..24_000)
            .map(|i| {
                let bed = 0.2 * (i as f64 * 0.04).sin();
                bed + if i % 6_000 < 24 { 0.8 } else { 0.0 }
            })
            .collect();
        let p = SendParams { stem_transient_duck: 0.7, ..send_params() };

        let mut state = StemRouteState::new(sr, &p, &[]);
        let got = blocked(&mut state, &signal);

        let (ducked, _) = transient_duck(&signal, &signal, sr, p.stem_transient_duck);
        let hp = butter_sos(2, p.surround_bass_cutoff_hz / (sr as f64 / 2.0), BandType::High);
        let shaped = sosfilt(&hp, &ducked);
        let (left, right) = velvet_pair_seeded(sr, VELVET_SEED);

        for (index, fir) in [(0, left), (1, right)] {
            let want = fir.process(&shaped);
            for (i, (a, b)) in got[index].iter().zip(want.iter()).enumerate() {
                assert!((a - b).abs() < 1e-12, "send {index} sample {i}: {a} vs {b}");
            }
        }
    }

    /// Depth 0.0 must leave the shaped sends untouched, so every existing
    /// render is bit for bit what it was.
    #[test]
    fn zero_duck_depth_leaves_the_sends_bit_for_bit() {
        let sr = 48_000;
        let signal: Vec<f64> = (0..12_000).map(|i| (i as f64 * 0.04).sin()).collect();

        let mut off = StemRouteState::new(sr, &send_params(), &[]);
        let want = blocked(&mut off, &signal);
        let mut explicit = StemRouteState::new(
            sr,
            &SendParams { stem_transient_duck: 0.0, ..send_params() },
            &[],
        );
        let got = blocked(&mut explicit, &signal);
        assert_eq!(got, want);
    }

    /// The surround and height sends of one stem must not be copies of each
    /// other: they run different seeds, so a stem placed both around and
    /// overhead does not image as one hard phantom between the two.
    #[test]
    fn surround_and_height_sends_use_different_tap_sets() {
        let (surround, _) = velvet_pair_seeded(48_000, VELVET_SEED);
        let (height, _) = velvet_pair_seeded(48_000, VELVET_SEED_HEIGHT);
        assert_ne!(surround.taps(), height.taps());
    }

    #[test]
    fn lfe_bus_matches_the_offline_lowpass_and_gain() {
        use crate::kernels::biquad::sosfilt;

        let sr = 48_000;
        let signal: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.02).sin()).collect();
        let p = send_params();

        let mut bus = LfeBus::new(sr, &p);
        let got: Vec<f64> = signal.iter().map(|v| bus.tick(*v)).collect();

        let lp = linkwitz_riley_lowpass_sos(p.lfe_filter_order, p.lfe_cutoff_hz / (sr as f64 / 2.0));
        let want: Vec<f64> = sosfilt(&lp, &signal).iter().map(|v| v * p.lfe_gain).collect();

        for (a, b) in got.iter().zip(want.iter()) {
            assert!((a - b).abs() < 1e-12);
        }
    }
}
