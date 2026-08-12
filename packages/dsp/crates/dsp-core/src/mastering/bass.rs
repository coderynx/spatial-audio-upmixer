//! Multichannel low-end control: band gains, bass mono-maker, harmonic
//! exciter, LFE trim. Profile values and the stereo-pair table are owned by
//! `packages/core/src/mastering/bass.py`.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_sos, BandType};
use crate::kernels::filtfilt::sosfiltfilt;

use super::non_lfe;

/// Below this length the zero-phase pass is skipped for a single forward
/// pass, matching `BassController.process`.
const MONO_FILTFILT_MIN_LEN: usize = 15;

#[derive(Clone, Copy, Debug)]
pub struct BassParams {
    pub sub_gain_db: f64,
    pub mid_gain_db: f64,
    pub mono_cutoff_hz: Option<f64>,
    pub excite: bool,
    pub lfe_gain_db: f64,
    pub sub_cutoff_hz: f64,
    pub mid_cutoff_hz: f64,
    pub excite_blend: f64,
    pub excite_drive: f64,
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

/// Apply the low-end chain in place.
///
/// `stereo_pairs` carries channel indices resolved by the caller from the
/// layout, so the core never needs to know channel names.
pub fn bass_control(
    bed: &mut super::Bed,
    lfe: Option<usize>,
    stereo_pairs: &[(usize, usize)],
    sample_rate: u32,
    p: &BassParams,
) {
    let nyq = sample_rate as f64 / 2.0;
    let sos_sub_lp = butter_sos(2, p.sub_cutoff_hz / nyq, BandType::Low);
    let bed_idx = non_lfe(bed.len(), lfe);

    if !bed_idx.is_empty() && (p.sub_gain_db != 0.0 || p.mid_gain_db != 0.0) {
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

    if let Some(mono_hz) = p.mono_cutoff_hz {
        let mono_norm = (mono_hz / nyq).clamp(1e-4, 0.999);
        let sos_mono = butter_sos(2, mono_norm, BandType::Low);
        for &(l, r) in stereo_pairs {
            if l >= bed.len() || r >= bed.len() {
                continue;
            }
            let n = bed[l].len().min(bed[r].len());
            if n == 0 {
                continue;
            }
            let low = |x: &[f64]| -> Vec<f64> {
                if x.len() > MONO_FILTFILT_MIN_LEN {
                    sosfiltfilt(&sos_mono, x).unwrap_or_else(|| sosfilt(&sos_mono, x))
                } else {
                    sosfilt(&sos_mono, x)
                }
            };
            let low_l = low(&bed[l]);
            let low_r = low(&bed[r]);
            for i in 0..n {
                let mono_bass = (low_l[i] + low_r[i]) * 0.5;
                let (dry_l, dry_r) = (bed[l][i], bed[r][i]);
                bed[l][i] = mono_bass + (dry_l - low_l[i]);
                bed[r][i] = mono_bass + (dry_r - low_r[i]);
            }
        }
    }

    if p.excite {
        for &i in &bed_idx {
            let sub = sosfilt(&sos_sub_lp, &bed[i]);
            for (v, s) in bed[i].iter_mut().zip(sub.iter()) {
                *v += (s * p.excite_drive).tanh() * p.excite_blend;
            }
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
            mono_cutoff_hz: None,
            excite: false,
            lfe_gain_db: 0.0,
            sub_cutoff_hz: 80.0,
            mid_cutoff_hz: 200.0,
            excite_blend: 0.15,
            excite_drive: 3.0,
        }
    }

    #[test]
    fn all_stages_off_is_a_bypass() {
        let mut bed = vec![vec![0.3; 512], vec![-0.2; 512]];
        let before = bed.clone();
        bass_control(&mut bed, Some(1), &[], 48_000, &params());
        assert_eq!(bed, before);
    }

    #[test]
    fn mono_maker_collapses_out_of_phase_bass() {
        let sr = 48_000;
        let n = 4800;
        let tone = |sign: f64| -> Vec<f64> {
            (0..n)
                .map(|i| sign * (2.0 * std::f64::consts::PI * 40.0 * i as f64 / sr as f64).sin())
                .collect()
        };
        let dry = tone(1.0);
        let mut bed = vec![dry.clone(), tone(-1.0)];
        let p = BassParams { mono_cutoff_hz: Some(100.0), ..params() };
        bass_control(&mut bed, None, &[(0, 1)], sr, &p);
        // Anti-phase 40 Hz content cancels once summed to mono; what is left
        // is only the part the 100 Hz lowpass does not capture.
        let before: f64 = dry[2400..].iter().map(|v| v * v).sum();
        let after: f64 = bed[0][2400..].iter().map(|v| v * v).sum();
        assert!(after < before * 0.02, "residual {after} of {before}");
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
        let low: Vec<f64> = (0..4800)
            .map(|i| (2.0 * std::f64::consts::PI * 40.0 * i as f64 / sr as f64).sin())
            .collect();
        let mut bed = vec![low.clone()];
        let p = BassParams { sub_gain_db: 6.0, ..params() };
        bass_control(&mut bed, None, &[], sr, &p);
        let before: f64 = low[2400..].iter().map(|v| v * v).sum();
        let after: f64 = bed[0][2400..].iter().map(|v| v * v).sum();
        assert!(after > before * 3.0, "{after} vs {before}");
    }
}
