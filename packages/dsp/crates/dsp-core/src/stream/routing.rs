//! Streaming stem-to-speaker-bed routing.
//!
//! Mirrors `separation/stem_router.py::StemRouter.route` block by block: each
//! shaped send carries the filter state and decorrelator history its offline
//! counterpart would have accumulated, so the two agree sample for sample.

use crate::kernels::biquad::SosFilter;
use crate::routing::ambient::AmbientSplit;
use crate::kernels::butter::{butter_sos, linkwitz_riley_lowpass_sos, BandType};
use crate::routing::decorrelate::{
    velvet_pair_seeded, VelvetFir, VelvetLine, VELVET_SEED, VELVET_SEED_HEIGHT,
};
use crate::routing::sends::directional_band_sos;

use super::conv::StreamingConvolver;
use super::params::{SendParams, SendShape};

/// One shaped send: a filter chain and, for a send fed from the dry stem,
/// one side of a decorrelator pair. The ambient sends carry no decorrelator:
/// their two sides are already independent signals — that is what the split
/// selected them for — so a velvet pair would only smear them.
struct Send {
    filters: Vec<SosFilter>,
    velvet: Option<VelvetLine>,
    /// Elevation-EQ gains, when this send is a height send: low rolloff,
    /// high shelf, directional band.
    elevation: Option<(f64, f64, f64)>,
}

impl Send {
    fn reset(&mut self) {
        for f in &mut self.filters {
            f.reset();
        }
        if let Some(velvet) = &mut self.velvet {
            velvet.reset();
        }
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

    fn process_in_place(&mut self, buffer: &mut Vec<f64>) {
        for x in buffer.iter_mut() {
            *x = self.shape(*x);
        }
        if let Some(velvet) = &mut self.velvet {
            velvet.process(buffer);
        }
    }
}

/// EQ'd stem samples covering `[base, base + left.len())`, grown forward as
/// blocks are asked for and trimmed from behind once they are consumed.
#[derive(Default)]
struct Ahead {
    left: Vec<f64>,
    right: Vec<f64>,
    base: usize,
}

impl Ahead {
    fn clear(&mut self, at: usize) {
        self.left.clear();
        self.right.clear();
        self.base = at;
    }

    fn end(&self) -> usize {
        self.base + self.left.len()
    }

    /// Fill forward to `target`, taking raw stem PCM through the stem EQ.
    fn fill(
        &mut self,
        stem_left: &[f32],
        stem_right: &[f32],
        eq: &mut Option<(StreamingConvolver, StreamingConvolver)>,
        target: usize,
    ) {
        if target <= self.end() {
            return;
        }
        let (from, count) = (self.end(), target - self.end());
        let take = |source: &[f32]| -> Vec<f64> {
            (from..from + count)
                .map(|i| *source.get(i).unwrap_or(&0.0) as f64)
                .collect()
        };
        let (left, right) = match eq {
            Some((eq_l, eq_r)) => (eq_l.process(&take(stem_left)), eq_r.process(&take(stem_right))),
            None => (take(stem_left), take(stem_right)),
        };
        self.left.extend_from_slice(&left);
        self.right.extend_from_slice(&right);
    }

    /// Drop samples before `keep_from`, in whole chunks so the copy is rare.
    fn trim(&mut self, keep_from: usize) {
        let drop = keep_from.saturating_sub(self.base);
        if drop < self.left.len() / 2 {
            return;
        }
        self.left.drain(..drop);
        self.right.drain(..drop);
        self.base += drop;
    }
}

/// First of the two ambient-surround signals; the right side follows it.
pub const AMBIENT_SURROUND: usize = 7;
/// First of the two ambient-height signals; the right side follows it.
pub const AMBIENT_HEIGHT: usize = 9;
/// The dry pair as it stood before the ambient half was taken out of it —
/// the stem's own level, which is what the route normalization matches the
/// routed sum to. Reading the post-split pair instead would make a stem get
/// quieter as its sends come up, since the sends are inside the routed sum.
pub const STEM_INPUT: usize = 11;
/// Signals a stem's routing can draw on: [`shape_index`]'s seven, the four
/// ambient sends, then the input pair.
pub const SIGNALS: usize = 13;

/// Per-stem shaping state: the four sends plus the optional stem EQ, and —
/// when the stem asks for it — the primary/ambient split and the four extra
/// sends its ambient half plays through.
pub struct StemRouteState {
    pub eq: Option<(StreamingConvolver, StreamingConvolver)>,
    surround: [Send; 2],
    height: [Send; 2],
    split: Option<AmbientSplit>,
    /// Stem samples past the stem EQ, filled ahead of the block being
    /// rendered. The split's mask needs a window that ends after the block
    /// does, and it has to be the same signal the export splits — which is
    /// the EQ'd stem, since the offline path EQs before it routes.
    ahead: Ahead,
    ambient_surround: [Send; 2],
    ambient_height: [Send; 2],
    /// The dry pair of the block being shaped, after the ambient half has
    /// been taken out of it.
    scratch: [Vec<f64>; 2],
    /// Last block's signals, indexed by [`shape_index`]: the dry pair, their
    /// mono sum, the four shaped sends, then the four ambient sends.
    shaped: [Vec<f64>; SIGNALS],
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

        let surround_send = |fir: Option<&VelvetFir>| Send {
            filters: vec![SosFilter::from_flat(&surround_hp)],
            velvet: fir.map(VelvetLine::new),
            elevation: None,
        };
        let height_send = |fir: Option<&VelvetFir>| Send {
            filters: vec![
                SosFilter::from_flat(&height_lp),
                SosFilter::from_flat(&height_hp),
                SosFilter::from_flat(&[height_band]),
            ],
            velvet: fir.map(VelvetLine::new),
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
            surround: [surround_send(Some(&surround_l)), surround_send(Some(&surround_r))],
            height: [height_send(Some(&height_l)), height_send(Some(&height_r))],
            split: None,
            ahead: Ahead::default(),
            ambient_surround: [surround_send(None), surround_send(None)],
            ambient_height: [height_send(None), height_send(None)],
            scratch: Default::default(),
            shaped: Default::default(),
        }
    }

    /// Build or drop the ambient half.
    pub fn set_ambient(
        &mut self,
        sample_rate: u32,
        p: &SendParams,
        wanted: bool,
        height_crossover_hz: f64,
    ) {
        if !wanted {
            self.split = None;
            return;
        }
        if self.split.is_none() {
            self.split = Some(AmbientSplit::with_height_crossover(
                sample_rate,
                height_crossover_hz,
            ));
        } else if let Some(split) = &mut self.split {
            split.set_height_crossover(height_crossover_hz);
        }
        for s in self.ambient_surround.iter_mut() {
            s.retune_surround(sample_rate, p);
        }
        for s in self.ambient_height.iter_mut() {
            s.retune_height(sample_rate, p);
        }
    }

    pub fn has_ambient(&self) -> bool {
        self.split.is_some()
    }

    /// Shape one block of a stem into every signal a speaker can draw on.
    ///
    /// The stem EQ runs ahead of the block rather than in step with it, so
    /// the ambient split can read the window its mask needs without the
    /// output being delayed by one. `rear`/`height` are the amounts moved out
    /// of the dry pair and into the ambient sends.
    #[allow(clippy::too_many_arguments)]
    pub fn process_block(
        &mut self,
        stem_left: &[f32],
        stem_right: &[f32],
        start: usize,
        count: usize,
        rear: f64,
        height: f64,
        surround: bool,
        height_send: bool,
    ) {
        let look_ahead = self.split.as_ref().map_or(0, |s| s.look_ahead());
        if start < self.ahead.base || start > self.ahead.end() {
            // A seek, or the first block: the EQ's history and the split's
            // frame grid both restart where the transport landed.
            self.ahead.clear(start);
            if let Some((eq_l, eq_r)) = &mut self.eq {
                eq_l.reset();
                eq_r.reset();
            }
            if let Some(split) = &mut self.split {
                split.reset();
            }
        }
        self.ahead
            .fill(stem_left, stem_right, &mut self.eq, start + count + look_ahead);

        let offset = start - self.ahead.base;
        self.scratch[0].clear();
        self.scratch[0].extend_from_slice(&self.ahead.left[offset..offset + count]);
        self.scratch[1].clear();
        self.scratch[1].extend_from_slice(&self.ahead.right[offset..offset + count]);

        for i in 0..2 {
            self.shaped[STEM_INPUT + i].clear();
            self.shaped[STEM_INPUT + i].extend_from_slice(&self.scratch[i]);
        }
        if self.split.is_some() && (rear > 0.0 || height > 0.0) {
            self.split_ambient(start, count, rear, height);
        }
        self.shape_sends(count, surround, height_send);
        self.ahead.trim(start + count);
    }

    /// Take the block's ambient half out of the scratch pair and leave it,
    /// shaped, in the four ambient signals.
    fn split_ambient(&mut self, start: usize, count: usize, rear: f64, height: f64) {
        let Some(split) = &mut self.split else { return };
        let block = split.advance(
            self.ahead.base,
            &self.ahead.left,
            &self.ahead.right,
            start,
            count,
        );
        let sources = [
            (AMBIENT_SURROUND, block.rear[0]),
            (AMBIENT_SURROUND + 1, block.rear[1]),
            (AMBIENT_HEIGHT, block.height[0]),
            (AMBIENT_HEIGHT + 1, block.height[1]),
        ];
        for (slot, source) in sources {
            self.shaped[slot].clear();
            self.shaped[slot].extend_from_slice(source);
        }
        for i in 0..count {
            self.scratch[0][i] -=
                rear * self.shaped[AMBIENT_SURROUND][i] + height * self.shaped[AMBIENT_HEIGHT][i];
            self.scratch[1][i] -= rear * self.shaped[AMBIENT_SURROUND + 1][i]
                + height * self.shaped[AMBIENT_HEIGHT + 1][i];
        }
        for i in 0..2 {
            self.ambient_surround[i].process_in_place(&mut self.shaped[AMBIENT_SURROUND + i]);
            self.ambient_height[i].process_in_place(&mut self.shaped[AMBIENT_HEIGHT + i]);
        }
    }

    pub fn reset(&mut self) {
        if let Some((l, r)) = &mut self.eq {
            l.reset();
            r.reset();
        }
        for s in self
            .surround
            .iter_mut()
            .chain(self.height.iter_mut())
            .chain(self.ambient_surround.iter_mut())
            .chain(self.ambient_height.iter_mut())
        {
            s.reset();
        }
        if let Some(split) = &mut self.split {
            split.reset();
        }
        self.ahead.clear(0);
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
        self.scratch[0].clear();
        self.scratch[0].extend_from_slice(left);
        self.scratch[1].clear();
        self.scratch[1].extend_from_slice(right);
        self.shape_sends(left.len().min(right.len()), surround, height);
    }

    /// The dry pair in `scratch` into the seven dry signals.
    fn shape_sends(&mut self, count: usize, surround: bool, height: bool) {
        for i in 0..2 {
            self.shaped[i].clear();
            self.shaped[i].extend_from_slice(&self.scratch[i][..count]);
        }
        self.shaped[2].clear();
        self.shaped[2].extend(
            self.scratch[0][..count]
                .iter()
                .zip(&self.scratch[1][..count])
                .map(|(l, r)| (l + r) * 0.5),
        );
        for i in 0..2 {
            if surround {
                let (send, out) = (&mut self.surround[i], &mut self.shaped[3 + i]);
                out.clear();
                out.extend_from_slice(&self.scratch[i][..count]);
                send.process_in_place(out);
            } else {
                self.shaped[3 + i].clear();
                self.shaped[3 + i].extend_from_slice(&self.scratch[i][..count]);
            }
            if height {
                let (send, out) = (&mut self.height[i], &mut self.shaped[5 + i]);
                out.clear();
                out.extend_from_slice(&self.scratch[i][..count]);
                send.process_in_place(out);
            } else {
                self.shaped[5 + i].clear();
                self.shaped[5 + i].extend_from_slice(&self.scratch[i][..count]);
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

/// Index into the dry signals a speaker can draw on: the three dry shapes,
/// then the four shaped sends. The ambient sends are addressed by
/// [`AMBIENT_SURROUND`]/[`AMBIENT_HEIGHT`] instead — a speaker's shape says
/// which class it belongs to, not which of the two feeds it draws.
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
