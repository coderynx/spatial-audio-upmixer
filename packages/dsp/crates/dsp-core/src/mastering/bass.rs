//! Multichannel low-end control: band gains, LF unification with
//! redistribution, transient shaping, harmonic excitation, mid-bass
//! decorrelation, LFE trim. Profile values, the spread tables and the
//! target-weight arithmetic are owned by
//! `packages/core/src/mastering/bass.py`.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::filtfilt::sosfiltfilt;

use super::compressor::alpha;
use super::non_lfe;

/// Below this length the zero-phase pass is skipped for a single forward
/// pass, matching `BassController.process`.
const UNIFY_FILTFILT_MIN_LEN: usize = 15;

/// Keeps the punch shaper's gain at unity while both envelopes are still
/// converging from silence, instead of `0/0`.
const PUNCH_EPS: f64 = 1e-9;

#[derive(Clone, Copy, Debug, PartialEq, serde::Deserialize)]
pub struct BassParams {
    pub sub_gain_db: f64,
    pub mid_gain_db: f64,
    pub unify_hz: Option<f64>,
    pub punch: f64,
    pub excite: bool,
    pub lfe_gain_db: f64,
    pub sub_cutoff_hz: f64,
    pub mid_cutoff_hz: f64,
    pub excite_blend: f64,
    pub excite_drive: f64,
    pub punch_fast_ms: f64,
    pub punch_slow_ms: f64,
    pub punch_max_db: f64,
    pub decorrelate: f64,
    pub decorr_low_hz: f64,
    pub decorr_high_hz: f64,
    pub decorr_sections: usize,
    pub decorr_max_delay_ms: f64,
    pub decorr_fast_ms: f64,
    pub decorr_slow_ms: f64,
}

/// Boost or cut a band additively: `out = (x - band) + band * gain`.
///
/// Deliberately not a shelving/peaking biquad — the additive identity is what
/// the preview had to reproduce to stop the mono-maker flipping sign
/// (ledger D9).
fn apply_band_gain(
    channel: &[f64],
    gain_linear: f64,
    sos_lp: &[[f64; 6]],
    sos_hp: Option<&[[f64; 6]]>,
) -> Vec<f64> {
    let mut band = sosfilt(sos_lp, channel);
    if let Some(hp) = sos_hp {
        band = sosfilt(hp, &band);
    }
    channel
        .iter()
        .zip(band.iter())
        .map(|(x, b)| (x - b) + b * gain_linear)
        .collect()
}

/// A band taken zero-phase, so the complement `x - band` stays time-aligned.
pub(crate) fn zero_phase(sos: &[[f64; 6]], x: &[f64]) -> Vec<f64> {
    if x.len() > UNIFY_FILTFILT_MIN_LEN {
        sosfiltfilt(sos, x).unwrap_or_else(|| sosfilt(sos, x))
    } else {
        sosfilt(sos, x)
    }
}

/// Transient shaper on the mono LF bus, carrying its envelopes so streaming
/// can advance them across block boundaries.
///
/// `punch > 0` opens the gap between a fast and a slow envelope, so attacks
/// survive and the sustain between them drops; `punch < 0` closes it, which
/// densifies. iZotope's Punchy/Smooth switch as one signed control.
#[derive(Clone, Copy, Debug, Default)]
pub struct PunchState {
    fast: f64,
    slow: f64,
}

impl PunchState {
    pub fn run(&mut self, bus: &mut [f64], sample_rate: u32, p: &BassParams) {
        if p.punch == 0.0 {
            return;
        }
        let a_fast = alpha(p.punch_fast_ms, sample_rate);
        let a_slow = alpha(p.punch_slow_ms, sample_rate);
        let ceiling = 10.0_f64.powf(p.punch_max_db / 20.0);
        for v in bus.iter_mut() {
            let level = v.abs();
            self.fast += a_fast * (level - self.fast);
            self.slow += a_slow * (level - self.slow);
            let ratio = (self.fast + PUNCH_EPS) / (self.slow + PUNCH_EPS);
            *v *= ratio.powf(p.punch).clamp(1.0 / ceiling, ceiling);
        }
    }
}

/// Harmonics the exciter derives from the LF bus.
///
/// Kept off the LFE by the caller: tanh's third and fifth land at 3x and 5x
/// the fundamental, above the 120 Hz the channel is band-limited to.
pub(crate) fn excite_harmonics(bus: &[f64], p: &BassParams) -> Vec<f64> {
    bus.iter()
        .map(|v| (v * p.excite_drive).tanh() * p.excite_blend)
        .collect()
}

/// Collapse the low band of every non-LFE channel into one bus and hand it
/// back out over `lf_targets`.
///
/// The complement is taken by subtraction rather than by a second filter, so
/// it is exact for any low-pass — an LR4 pair would not sum flat once run
/// zero-phase. Weights are resolved by the caller and normally sum to 1,
/// which is what keeps the coherent low-frequency level unchanged.
fn lf_unify(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    lf_targets: &[(usize, f64)],
    bed_idx: &[usize],
    sample_rate: u32,
    unify_hz: f64,
    p: &BassParams,
) {
    let nyq = sample_rate as f64 / 2.0;
    let sos = butter_sos(2, (unify_hz / nyq).clamp(1e-4, 0.999), BandType::Low);

    let lows: Vec<Vec<f64>> = bed_idx.iter().map(|&i| zero_phase(&sos, &bed[i])).collect();
    let n = lows.iter().map(|l| l.len()).max().unwrap_or(0);
    if n == 0 {
        return;
    }

    let mut bus = vec![0.0; n];
    for low in &lows {
        for (acc, v) in bus.iter_mut().zip(low.iter()) {
            *acc += v;
        }
    }
    PunchState::default().run(&mut bus, sample_rate, p);

    for (&i, low) in bed_idx.iter().zip(lows.iter()) {
        for (v, l) in bed[i].iter_mut().zip(low.iter()) {
            *v -= l;
        }
    }

    let harmonics = p.excite.then(|| excite_harmonics(&bus, p));
    for &(i, weight) in lf_targets {
        if i >= bed.len() || weight == 0.0 {
            continue;
        }
        let harmonics = harmonics.as_ref().filter(|_| Some(i) != lfe);
        for (k, v) in bed[i].iter_mut().enumerate().take(n) {
            let h = harmonics.map_or(0.0, |h| h[k]);
            *v += (bus[k] + h) * weight;
        }
    }
}

/// Apply the low-end chain in place.
///
/// `lf_targets` carries `(channel index, weight)` pairs resolved by the
/// caller from the layout and the LFE mode, so the core never needs to know
/// channel names.
pub fn bass_control(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    lf_targets: &[(usize, f64)],
    sample_rate: u32,
    p: &BassParams,
) {
    let nyq = sample_rate as f64 / 2.0;
    let bed_idx = non_lfe(bed.len(), lfe);

    if !bed_idx.is_empty() && (p.sub_gain_db != 0.0 || p.mid_gain_db != 0.0) {
        let sos_sub_lp = butter_sos(2, p.sub_cutoff_hz / nyq, BandType::Low);
        let sos_mid_lp = butter_sos(2, p.mid_cutoff_hz / nyq, BandType::Low);
        let sos_mid_hp = butter_sos(2, p.sub_cutoff_hz / nyq, BandType::High);
        let sub_lin = 10.0_f64.powf(p.sub_gain_db / 20.0);
        let mid_lin = 10.0_f64.powf(p.mid_gain_db / 20.0);
        for &i in &bed_idx {
            let mut shaped = std::mem::take(&mut bed[i]);
            if p.sub_gain_db != 0.0 {
                shaped = apply_band_gain(&shaped, sub_lin, &sos_sub_lp, None);
            }
            if p.mid_gain_db != 0.0 {
                shaped = apply_band_gain(&shaped, mid_lin, &sos_mid_lp, Some(&sos_mid_hp));
            }
            bed[i] = shaped;
        }
    }

    // Taken before unification, which is what lets the streaming mirror read
    // its look-ahead out of the same pre-unification queue. The two bands are
    // disjoint — the decorrelator's low corner is clamped up to `unify_hz` —
    // so nothing the unifier does belongs in this band anyway.
    let bands = super::decorrelate::band_sos(sample_rate, p).map(|sos| {
        bed_idx
            .iter()
            .map(|&i| zero_phase(&sos, &bed[i]))
            .collect::<Vec<_>>()
    });

    if let Some(unify_hz) = p.unify_hz {
        if !bed_idx.is_empty() && !lf_targets.is_empty() {
            lf_unify(bed, lfe, lf_targets, &bed_idx, sample_rate, unify_hz, p);
        }
    }

    if let Some(bands) = bands {
        for (&i, band) in bed_idx.iter().zip(bands.iter()) {
            super::decorrelate::Decorrelator::new(i, sample_rate, p).run(&mut bed[i], band);
        }
    }

    if p.lfe_gain_db != 0.0 {
        if let Some(i) = lfe {
            let g = 10.0_f64.powf(p.lfe_gain_db / 20.0);
            for v in bed[i].iter_mut() {
                *v *= g;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn params() -> BassParams {
        BassParams {
            sub_gain_db: 0.0,
            mid_gain_db: 0.0,
            unify_hz: None,
            punch: 0.0,
            excite: false,
            lfe_gain_db: 0.0,
            sub_cutoff_hz: 80.0,
            mid_cutoff_hz: 200.0,
            excite_blend: 0.15,
            excite_drive: 3.0,
            punch_fast_ms: 10.0,
            punch_slow_ms: 120.0,
            punch_max_db: 6.0,
            decorrelate: 0.0,
            decorr_low_hz: 100.0,
            decorr_high_hz: 300.0,
            decorr_sections: 32,
            decorr_max_delay_ms: 30.0,
            decorr_fast_ms: 30.0,
            decorr_slow_ms: 300.0,
        }
    }

    fn tone(freq: f64, sample_rate: u32, n: usize, amplitude: f64) -> Vec<f64> {
        (0..n)
            .map(|i| {
                amplitude * (2.0 * std::f64::consts::PI * freq * i as f64 / sample_rate as f64).sin()
            })
            .collect()
    }

    fn energy(x: &[f64]) -> f64 {
        x[2400..].iter().map(|v| v * v).sum()
    }

    /// Equal weights over `targets`, the arithmetic `bass.py` performs for a
    /// spread with no LFE send.
    fn even(targets: &[usize]) -> Vec<(usize, f64)> {
        targets.iter().map(|&i| (i, 1.0 / targets.len() as f64)).collect()
    }

    #[test]
    fn all_stages_off_is_a_bypass() {
        let mut bed = vec![vec![0.3; 512], vec![-0.2; 512]];
        let before = bed.clone();
        bass_control(&mut bed, Some(1), &[], 48_000, &params());
        assert_eq!(bed, before);
    }

    #[test]
    fn unify_preserves_the_coherent_low_end_and_spreads_it() {
        let sr = 48_000;
        let n = 9600;
        let bass = tone(40.0, sr, n, 0.5);
        // Bass in the front pair only, silence in the surrounds — a stereo
        // source as the router leaves it.
        let mut bed = vec![bass.clone(), bass.clone(), vec![0.0; n], vec![0.0; n]];
        let sum_before: Vec<f64> = (0..n).map(|i| bed.iter().map(|c| c[i]).sum()).collect();

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut bed, None, &even(&[0, 1, 2, 3]), sr, &p);

        let sum_after: Vec<f64> = (0..n).map(|i| bed.iter().map(|c| c[i]).sum()).collect();
        let residual: f64 = (2400..n).map(|i| (sum_after[i] - sum_before[i]).powi(2)).sum();
        assert!(
            residual < energy(&sum_before) * 1e-6,
            "coherent sum moved: residual {residual} of {}",
            energy(&sum_before)
        );

        // The surrounds, silent before, now carry the redistributed share.
        assert!(energy(&bed[2]) > energy(&bass) * 0.01, "surround got no low end");
        assert!(energy(&bed[0]) < energy(&bass) * 0.5, "front pair kept its low end");
    }

    #[test]
    fn split_conserves_the_low_end_through_lfe_replay_gain() {
        let sr = 48_000;
        let n = 9600;
        let bass = tone(40.0, sr, n, 0.5);
        let mut mains_only = vec![bass.clone(), bass.clone(), vec![0.0; n]];
        let mut split = mains_only.clone();

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut mains_only, Some(2), &even(&[0, 1]), sr, &p);

        // `split` at 0.5: mains share 0.5, LFE takes 0.5 scaled by the -10 dB
        // BS.775 authoring gain that playback's +10 dB undoes.
        let authoring = 0.316_227_766_016_837_94;
        let targets = vec![(0, 0.25), (1, 0.25), (2, 0.5 * authoring)];
        bass_control(&mut split, Some(2), &targets, sr, &p);

        let replay = 10.0_f64.powf(10.0 / 20.0);
        for i in 2400..n {
            let reference = mains_only[0][i] + mains_only[1][i] + mains_only[2][i] * replay;
            let played = split[0][i] + split[1][i] + split[2][i] * replay;
            assert!(
                (played - reference).abs() < 1e-9,
                "sample {i}: {played} vs {reference}"
            );
        }
    }

    #[test]
    fn an_lfe_send_leaves_the_mains_untouched() {
        let sr = 48_000;
        let n = 4800;
        let bass = tone(40.0, sr, n, 0.5);
        let mut without = vec![bass.clone(), bass.clone(), vec![0.0; n]];
        let mut with = without.clone();

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut without, Some(2), &even(&[0, 1]), sr, &p);

        let mut targets = even(&[0, 1]);
        targets.push((2, 0.25));
        bass_control(&mut with, Some(2), &targets, sr, &p);

        assert_eq!(without[0], with[0], "an LFE send moved the mains");
        assert_eq!(without[1], with[1], "an LFE send moved the mains");
        assert!(energy(&with[2]) > 0.0, "LFE got nothing");
    }

    #[test]
    fn the_exciter_stays_out_of_the_lfe() {
        let sr = 48_000;
        let n = 4800;
        let bass = tone(40.0, sr, n, 0.5);
        let mut plain = vec![bass.clone(), bass.clone(), vec![0.0; n]];
        let mut excited = plain.clone();

        let mut targets = even(&[0, 1]);
        targets.push((2, 0.25));
        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut plain, Some(2), &targets, sr, &p);
        bass_control(&mut excited, Some(2), &targets, sr, &BassParams { excite: true, ..p });

        assert_eq!(plain[2], excited[2], "harmonics reached the LFE");
        assert!(energy(&excited[0]) > energy(&plain[0]), "exciter did nothing");
    }

    #[test]
    fn unification_commutes_with_a_shared_upstream_gain() {
        // The EQ and reference-match stages apply one shared curve to every
        // bed channel; that is what lets bass control ignore them.
        let sr = 48_000;
        let n = 9600;
        let mut before = vec![tone(40.0, sr, n, 0.5), tone(55.0, sr, n, 0.4), tone(70.0, sr, n, 0.3)];
        let mut after = before.clone();
        let gain = 1.7;

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        for ch in before.iter_mut() {
            for v in ch.iter_mut() {
                *v *= gain;
            }
        }
        bass_control(&mut before, None, &even(&[0, 1, 2]), sr, &p);

        bass_control(&mut after, None, &even(&[0, 1, 2]), sr, &p);
        for ch in after.iter_mut() {
            for v in ch.iter_mut() {
                *v *= gain;
            }
        }

        for (a, b) in before.iter().zip(after.iter()) {
            for (x, y) in a.iter().zip(b.iter()) {
                assert!((x - y).abs() < 1e-9, "{x} vs {y}");
            }
        }
    }

    #[test]
    fn punch_off_is_a_bypass_and_punch_up_favours_the_attack() {
        let sr = 48_000;
        let n = 24_000;
        // A 40 Hz burst that stops a third of the way through, so the shaper
        // has an attack and a decaying sustain to separate.
        let burst: Vec<f64> = tone(40.0, sr, n, 0.5)
            .iter()
            .enumerate()
            .map(|(i, v)| if i < n / 3 { *v } else { v * 0.2 })
            .collect();

        let mut flat = vec![burst.clone()];
        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut flat, None, &even(&[0]), sr, &p);

        let mut shaped = vec![burst.clone()];
        bass_control(&mut shaped, None, &even(&[0]), sr, &BassParams { punch: 0.5, ..p });

        let ratio = |ch: &[f64]| {
            let attack: f64 = ch[2400..n / 3].iter().map(|v| v * v).sum();
            let sustain: f64 = ch[n / 3 + 4800..].iter().map(|v| v * v).sum();
            attack / sustain.max(1e-20)
        };
        assert!(
            ratio(&shaped[0]) > ratio(&flat[0]) * 1.05,
            "{} vs {}",
            ratio(&shaped[0]),
            ratio(&flat[0])
        );

        let mut bypass = vec![burst.clone()];
        bass_control(&mut bypass, None, &even(&[0]), sr, &BassParams { punch: 0.0, ..p });
        assert_eq!(bypass[0], flat[0]);
    }

    #[test]
    fn lfe_trim_only_touches_lfe() {
        let mut bed = vec![vec![0.5; 64], vec![0.5; 64]];
        let p = BassParams { lfe_gain_db: 6.0, ..params() };
        bass_control(&mut bed, Some(1), &[], 48_000, &p);
        assert!((bed[0][0] - 0.5).abs() < 1e-12);
        assert!((bed[1][0] - 0.5 * 10.0_f64.powf(0.3)).abs() < 1e-12);
    }

    #[test]
    fn sub_boost_raises_low_frequency_energy() {
        let sr = 48_000;
        let low = tone(40.0, sr, 4800, 1.0);
        let mut bed = vec![low.clone()];
        let p = BassParams { sub_gain_db: 6.0, ..params() };
        bass_control(&mut bed, None, &[], sr, &p);
        assert!(energy(&bed[0]) > energy(&low) * 3.0);
    }
}
