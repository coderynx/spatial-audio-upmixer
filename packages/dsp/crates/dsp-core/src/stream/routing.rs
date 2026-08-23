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
    /// Last block's signals, indexed by [`shape_index`]: the dry pair, their
    /// mono sum, then the four shaped sends.
    shaped: [Vec<f64>; 7],
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

    /// Shape a whole block into the seven signals a speaker can draw on,
    /// readable through [`Self::signal`].
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
        dry(left, &mut self.shaped[0]);
        dry(right, &mut self.shaped[1]);
        self.shaped[2].clear();
        self.shaped[2].extend(left.iter().zip(right).map(|(l, r)| (l + r) * 0.5));
        for (i, source) in [left, right].into_iter().enumerate() {
            if surround {
                self.surround[i].process(source, &mut self.shaped[3 + i]);
            } else {
                dry(source, &mut self.shaped[3 + i]);
            }
            if height {
                self.height[i].process(source, &mut self.shaped[5 + i]);
            } else {
                dry(source, &mut self.shaped[5 + i]);
            }
        }
    }

    /// One signal of the block [`Self::process`] just shaped, by
    /// [`shape_index`].
    #[inline]
    pub fn signal(&self, index: usize) -> &[f64] {
        &self.shaped[index]
    }
}

/// Index into the seven signals a speaker can draw on: the three dry shapes,
/// then the four shaped sends.
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
