//! The mastering bus, rendered incrementally.
//!
//! Stages split by what they need to see. Reference match, EQ, compression
//! and the bass band gains are causal and simply carry their state. The LF
//! unifier's zero-phase pass and the limiter's forward-window minimum both
//! need to look ahead, so the engine keeps a queue in front of them and only
//! emits samples that have their full look-ahead behind them.

use crate::kernels::biquad::SosFilter;
use crate::kernels::butter::{butter_sos, BandType};
use crate::mastering::bass::{excite_harmonics, BassParams, PunchState};
use crate::mastering::decorrelate::Decorrelator;
use crate::mastering::head::head_sos;
use crate::mastering::non_lfe;

use super::band::RollingBand;
use super::conv::StreamingConvolver;
use super::engine::GAIN_RAMP_MS;
use super::params::MasterParams;
use super::state::OnePole;

/// Look-behind and look-ahead the LF unifier's zero-phase pass runs with.
/// At 100 ms the 80-120 Hz filter has decayed by `e^-40`, so truncating there
/// is below any tolerance that matters — see [`super::band::RollingBand`].
pub const UNIFY_HORIZON_MS: f64 = 100.0;

/// Band the unifier's backward pass produces per warm-up — see
/// [`DECORR_CHUNK_MS`].
pub const UNIFY_CHUNK_MS: f64 = 50.0;

/// The decorrelator's own, longer horizon: its band split is a 4th-order
/// 100-300 Hz band-pass, whose impulse response outlives the unifier's
/// 2nd-order low-pass by orders of magnitude. Only the backward pass is
/// truncated here — the forward one carries its whole history — which is
/// what keeps 200 ms enough: measured against the offline pass, the band
/// lands within 5e-12, where 150 ms gives 2.5e-9 and 100 ms gives 1.3e-6.
pub const DECORR_HORIZON_MS: f64 = 200.0;

/// Band the decorrelator's backward pass produces per warm-up. Bigger spends
/// the warm-up over more output; the engine has to buffer `2 * chunk +
/// horizon` of look-ahead for it, which is what caps it.
pub const DECORR_CHUNK_MS: f64 = 50.0;

/// Causal, per-channel front of the chain.
pub struct CausalChain {
    head: Option<SosFilter>,
    reference: Option<StreamingConvolver>,
    eq: Option<StreamingConvolver>,
    reference_gain: f64,
    eq_strength: f64,
    sub: Option<(SosFilter, OnePole, f64)>,
    mid: Option<(SosFilter, SosFilter, OnePole, f64)>,
    is_lfe: bool,
}

impl CausalChain {
    pub fn new(sample_rate: u32, p: &MasterParams, is_lfe: bool) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let bass = p.bass.as_ref();
        Self {
            head: p
                .head
                .map(|h| SosFilter::from_flat(&head_sos(sample_rate, &h, is_lfe))),
            reference: (!p.reference_fir.is_empty() && !is_lfe)
                .then(|| StreamingConvolver::new(p.reference_fir.clone())),
            eq: (!p.eq_fir.is_empty() && !is_lfe)
                .then(|| StreamingConvolver::new(p.eq_fir.clone())),
            reference_gain: p.reference_gain,
            eq_strength: p.eq_strength,
            sub: bass.filter(|_| !is_lfe).map(|b| {
                let gain = 10.0_f64.powf(b.sub_gain_db / 20.0);
                (
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::Low)),
                    OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, gain),
                    gain,
                )
            }),
            mid: bass.filter(|_| !is_lfe).map(|b| {
                let gain = 10.0_f64.powf(b.mid_gain_db / 20.0);
                (
                    SosFilter::from_flat(&butter_sos(2, b.mid_cutoff_hz / nyq, BandType::Low)),
                    SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::High)),
                    OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, gain),
                    gain,
                )
            }),
            is_lfe,
        }
    }

    /// Adopt new master params in place. The reference/EQ convolvers keep
    /// their history via [`StreamingConvolver::retune_kernel`] when their FIR
    /// content changed, and the bass filters keep their delay registers while
    /// their gains ramp to a live edit.
    pub fn retune(
        &mut self,
        sample_rate: u32,
        old: &MasterParams,
        new: &MasterParams,
        firs_changed: bool,
    ) {
        self.reference_gain = new.reference_gain;
        self.eq_strength = new.eq_strength;

        if old.head != new.head {
            self.head = new
                .head
                .map(|h| SosFilter::from_flat(&head_sos(sample_rate, &h, self.is_lfe)));
        }

        if firs_changed && !self.is_lfe && old.reference_fir != new.reference_fir {
            if new.reference_fir.is_empty() {
                self.reference = None;
            } else if let Some(conv) = &mut self.reference {
                conv.retune_kernel(new.reference_fir.clone());
            } else {
                self.reference = Some(StreamingConvolver::new(new.reference_fir.clone()));
            }
        }

        if firs_changed && !self.is_lfe && old.eq_fir != new.eq_fir {
            if new.eq_fir.is_empty() {
                self.eq = None;
            } else if let Some(conv) = &mut self.eq {
                conv.retune_kernel(new.eq_fir.clone());
            } else {
                self.eq = Some(StreamingConvolver::new(new.eq_fir.clone()));
            }
        }

        if old.bass != new.bass && !self.is_lfe {
            let nyq = sample_rate as f64 / 2.0;
            let bass = new.bass.as_ref();
            match (&mut self.sub, bass) {
                (Some((filter, _, target)), Some(b)) => {
                    filter.retune_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::Low));
                    *target = 10.0_f64.powf(b.sub_gain_db / 20.0);
                }
                (slot @ None, Some(b)) => {
                    let gain = 10.0_f64.powf(b.sub_gain_db / 20.0);
                    *slot = Some((
                        SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::Low)),
                        OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, gain),
                        gain,
                    ));
                }
                (slot, None) => *slot = None,
            }
            match (&mut self.mid, bass) {
                (Some((low, high, _, target)), Some(b)) => {
                    low.retune_flat(&butter_sos(2, b.mid_cutoff_hz / nyq, BandType::Low));
                    high.retune_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::High));
                    *target = 10.0_f64.powf(b.mid_gain_db / 20.0);
                }
                (slot @ None, Some(b)) => {
                    let gain = 10.0_f64.powf(b.mid_gain_db / 20.0);
                    *slot = Some((
                        SosFilter::from_flat(&butter_sos(2, b.mid_cutoff_hz / nyq, BandType::Low)),
                        SosFilter::from_flat(&butter_sos(2, b.sub_cutoff_hz / nyq, BandType::High)),
                        OnePole::new_at(GAIN_RAMP_MS, sample_rate as f64, gain),
                        gain,
                    ));
                }
                (slot, None) => *slot = None,
            }
        }
    }

    pub fn reset(&mut self) {
        if let Some(head) = &mut self.head {
            head.reset();
        }
        if let Some(reference) = &mut self.reference {
            reference.reset();
        }
        if let Some(eq) = &mut self.eq {
            eq.reset();
        }
        if let Some((filter, smoother, target)) = &mut self.sub {
            filter.reset();
            smoother.set(*target);
        }
        if let Some((low, high, smoother, target)) = &mut self.mid {
            low.reset();
            high.reset();
            smoother.set(*target);
        }
    }

    pub fn set_reference_fir(&mut self, taps: &[f64]) {
        if self.is_lfe {
            return;
        }
        if taps.is_empty() {
            self.reference = None;
        } else if let Some(conv) = &mut self.reference {
            conv.retune_kernel(taps.to_vec());
        } else {
            self.reference = Some(StreamingConvolver::new(taps.to_vec()));
        }
    }

    pub fn set_eq_fir(&mut self, taps: &[f64]) {
        if self.is_lfe {
            return;
        }
        if taps.is_empty() {
            self.eq = None;
        } else if let Some(conv) = &mut self.eq {
            conv.retune_kernel(taps.to_vec());
        } else {
            self.eq = Some(StreamingConvolver::new(taps.to_vec()));
        }
    }

    /// Everything before the compressor, which needs the linked bus first.
    /// The head stage leads: nothing downstream should be matching, shaping
    /// or measuring DC and sub-20 Hz rumble.
    pub fn pre_compressor(&mut self, block: &[f64]) -> Vec<f64> {
        let mut out: Vec<f64> = block.to_vec();
        if let Some(head) = &mut self.head {
            head.process(&mut out);
        }
        for v in out.iter_mut() {
            *v *= self.reference_gain;
        }
        if let Some(conv) = &mut self.reference {
            out = conv.process(&out);
        }
        if let Some(conv) = &mut self.eq {
            let wet = conv.process(&out);
            if self.eq_strength < 1.0 {
                for (dry, w) in out.iter_mut().zip(wet.iter()) {
                    *dry = (1.0 - self.eq_strength) * *dry + self.eq_strength * w;
                }
            } else {
                out = wet;
            }
        }
        out
    }

    /// The band gains, which run before the LF unifier.
    pub fn band_gains(&mut self, block: &mut [f64]) {
        if let Some((filter, smoother, target)) = &mut self.sub {
            for v in block.iter_mut() {
                let band = filter.tick(*v);
                *v = (*v - band) + band * smoother.tick(*target);
            }
        }
        if let Some((lp, hp, smoother, target)) = &mut self.mid {
            for v in block.iter_mut() {
                let band = hp.tick(lp.tick(*v));
                *v = (*v - band) + band * smoother.tick(*target);
            }
        }
    }

    /// The LFE trim, which `BassController` runs *after* the LF unifier —
    /// reversing them measurably changes the low end.
    pub fn lfe_trim(&mut self, block: &mut [f64], lfe_gain_db: f64) {
        if self.is_lfe && lfe_gain_db != 0.0 {
            let g = 10.0_f64.powf(lfe_gain_db / 20.0);
            for v in block.iter_mut() {
                *v *= g;
            }
        }
    }
}

/// LF unification over a look-ahead window.
///
/// Mirrors `mastering::bass::lf_unify`: one low band per non-LFE channel,
/// summed to a mono bus, transient-shaped, excited, then handed back over the
/// caller-resolved target weights. The punch envelopes are causal, so they
/// advance only over what is emitted — the same discipline
/// [`super::limiter::StreamingLimiter`]'s release smoother follows.
pub struct LfUnifier {
    filter: RollingBand,
    lf_targets: Vec<(usize, f64)>,
    lfe: Option<usize>,
    params: BassParams,
    punch: PunchState,
    sample_rate: u32,
}

impl LfUnifier {
    pub fn new(
        sample_rate: u32,
        n_channels: usize,
        lfe: Option<usize>,
        params: BassParams,
        lf_targets: Vec<(usize, f64)>,
        base: usize,
    ) -> Self {
        let nyq = sample_rate as f64 / 2.0;
        let scale = |ms: f64| (sample_rate as f64 * ms / 1000.0) as usize;
        let cutoff = params.unify_hz.unwrap_or(nyq);
        let sos = butter_sos(2, (cutoff / nyq).clamp(1e-4, 0.999), BandType::Low);
        Self {
            filter: RollingBand::new(
                sos,
                scale(UNIFY_HORIZON_MS),
                scale(UNIFY_CHUNK_MS),
                non_lfe(n_channels, lfe),
                base,
            ),
            lf_targets,
            lfe,
            params,
            punch: PunchState::default(),
            sample_rate,
        }
    }

    /// Source frames ahead of `end` its band split needs — see
    /// [`RollingBand::look_ahead`].
    pub fn look_ahead(&self) -> usize {
        self.filter.look_ahead()
    }

    pub fn retune(&mut self, params: BassParams, lf_targets: Vec<(usize, f64)>) {
        self.params = params;
        self.lf_targets = lf_targets;
    }

    pub fn prewarm(
        &mut self,
        source: &[Vec<f64>],
        base: usize,
        total: usize,
        end: usize,
        budget: usize,
    ) -> bool {
        self.filter.prewarm(source, base, total, end, budget)
    }

    /// Unify the low band across `window`, which holds `source[..][start..end]`
    /// and is the only thing written — the zero-phase pass reads its context
    /// outward from `start`/`end` in `source`, which stays untouched so the
    /// decorrelator still sees the pre-unification signal offline hands it.
    pub fn process(
        &mut self,
        source: &[Vec<f64>],
        base: usize,
        total: usize,
        window: &mut [Vec<f64>],
        start: usize,
        end: usize,
    ) {
        if end <= start || self.lf_targets.is_empty() {
            return;
        }
        let n = end - start;
        let mut bus = vec![0.0; n];
        self.filter.advance(source, base, total, start, end);
        for slot in 0..self.filter.channels().len() {
            for (acc, v) in bus.iter_mut().zip(self.filter.band(slot, start, end)) {
                *acc += v;
            }
        }
        self.punch.run(&mut bus, self.sample_rate, &self.params);

        for (slot, &i) in self.filter.channels().iter().enumerate() {
            let Some(out) = window.get_mut(i) else {
                continue;
            };
            for (v, l) in out.iter_mut().zip(self.filter.band(slot, start, end)) {
                *v -= l;
            }
        }

        let harmonics = self
            .params
            .excite
            .then(|| excite_harmonics(&bus, &self.params));
        for &(i, weight) in &self.lf_targets {
            if i >= window.len() || weight == 0.0 {
                continue;
            }
            let harmonics = harmonics.as_ref().filter(|_| Some(i) != self.lfe);
            for k in 0..n.min(window[i].len()) {
                let h = harmonics.map_or(0.0, |h| h[k]);
                window[i][k] += (bus[k] + h) * weight;
            }
        }
    }
}

/// Mid-bass decorrelation over its own look-ahead window.
///
/// Mirrors the tail of `mastering::bass::bass_control`: the zero-phase band
/// comes off the *pre*-unification queue, exactly as offline takes it before
/// calling `lf_unify`, and the resulting delta is added to the unified block.
/// That ordering is what lets this read its look-ahead out of `pre`, whose
/// samples are already final, instead of needing a second horizon behind the
/// unifier.
pub struct StreamingDecorrelator {
    band: RollingBand,
    channels: Vec<Decorrelator>,
}

impl StreamingDecorrelator {
    /// `None` when the stage is off or its band collapsed — see
    /// [`crate::mastering::decorrelate::band_sos`].
    pub fn new(
        sample_rate: u32,
        n_channels: usize,
        lfe: Option<usize>,
        params: &BassParams,
        base: usize,
    ) -> Option<Self> {
        let sos = crate::mastering::decorrelate::band_sos(sample_rate, params)?;
        let scale = |ms: f64| (sample_rate as f64 * ms / 1000.0) as usize;
        let bed = non_lfe(n_channels, lfe);
        Some(Self {
            channels: bed
                .iter()
                .map(|i| Decorrelator::new(*i, sample_rate, params))
                .collect(),
            band: RollingBand::new(
                sos,
                scale(DECORR_HORIZON_MS),
                scale(DECORR_CHUNK_MS),
                bed,
                base,
            ),
        })
    }

    pub fn prewarm(
        &mut self,
        source: &[Vec<f64>],
        base: usize,
        total: usize,
        end: usize,
        budget: usize,
    ) -> bool {
        self.band.prewarm(source, base, total, end, budget)
    }

    /// Source frames ahead of `end` its band split needs — see
    /// [`RollingBand::look_ahead`].
    pub fn look_ahead(&self) -> usize {
        self.band.look_ahead()
    }

    pub fn retune_amount(&mut self, amount: f64) {
        for channel in &mut self.channels {
            channel.retune_amount(amount);
        }
    }

    pub fn fade_in(&mut self) {
        for channel in &mut self.channels {
            channel.fade_in();
        }
    }

    /// Decorrelate `window[..][..]`, taking the band from `pre[..][start..end]`
    /// and reading ahead of it for the zero-phase pass's context.
    pub fn process(
        &mut self,
        pre: &[Vec<f64>],
        pre_base: usize,
        total: usize,
        window: &mut [Vec<f64>],
        start: usize,
        end: usize,
    ) {
        if end <= start {
            return;
        }
        self.band.advance(pre, pre_base, total, start, end);
        for (slot, decorrelator) in self.channels.iter_mut().enumerate() {
            let channel = self.band.channels()[slot];
            let Some(out) = window.get_mut(channel) else {
                continue;
            };
            decorrelator.run(out, self.band.band(slot, start, end));
        }
    }
}
