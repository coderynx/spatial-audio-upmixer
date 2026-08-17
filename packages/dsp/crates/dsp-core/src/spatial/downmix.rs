//! ITU-R BS.775-4 Annex 4 downmixes and the memoryless soft limiter.

use std::f64::consts::SQRT_2;

/// The centre and back-fold coefficient is exactly 1/√2 and is independent of
/// the configurable surround coefficient — conflating the two cost ~98 dB of
/// mismatch once (ledger D6).
pub const ITU_CENTER_COEFF: f64 = 1.0 / SQRT_2;

/// Source channel of a downmix contribution. LFE has no role here; the height
/// roles fold in by project convention, not by BS.775 — see
/// docs/standards/spatial_layouts_bs775_bs2051.md §"Height fold-down".
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DownmixRole {
    Fl,
    Fr,
    C,
    Sl,
    Sr,
    Bl,
    Br,
    Tfl,
    Tfr,
    Tbl,
    Tbr,
}

fn pick<'a>(channels: &[(DownmixRole, &'a [f64])], role: DownmixRole) -> Option<&'a [f64]> {
    channels.iter().find(|(r, _)| *r == role).map(|(_, s)| *s)
}

fn add_scaled(target: &mut [f64], source: Option<&[f64]>, gain: f64) {
    let Some(source) = source else { return };
    if gain == 0.0 {
        return;
    }
    for (t, s) in target.iter_mut().zip(source.iter()) {
        *t += gain * s;
    }
}

fn frame_count(channels: &[(DownmixRole, &[f64])]) -> usize {
    channels.first().map(|(_, s)| s.len()).unwrap_or(0)
}

/// `L' = FL + (1/√2)·C + k_s·(SL + (1/√2)·BL) + k_h·(TFL + k_s·TBL)`, and the
/// mirror for the right.
pub fn itu_downmix_stereo(
    channels: &[(DownmixRole, &[f64])],
    surround_coeff: f64,
    height_coeff: f64,
) -> (Vec<f64>, Vec<f64>) {
    let n = frame_count(channels);
    let mut left = vec![0.0; n];
    let mut right = vec![0.0; n];
    if n == 0 {
        return (left, right);
    }

    add_scaled(&mut left, pick(channels, DownmixRole::Fl), 1.0);
    add_scaled(&mut right, pick(channels, DownmixRole::Fr), 1.0);
    add_scaled(&mut left, pick(channels, DownmixRole::C), ITU_CENTER_COEFF);
    add_scaled(&mut right, pick(channels, DownmixRole::C), ITU_CENTER_COEFF);
    add_scaled(&mut left, pick(channels, DownmixRole::Sl), surround_coeff);
    add_scaled(&mut right, pick(channels, DownmixRole::Sr), surround_coeff);
    add_scaled(&mut left, pick(channels, DownmixRole::Bl), surround_coeff * ITU_CENTER_COEFF);
    add_scaled(&mut right, pick(channels, DownmixRole::Br), surround_coeff * ITU_CENTER_COEFF);
    add_scaled(&mut left, pick(channels, DownmixRole::Tfl), height_coeff);
    add_scaled(&mut right, pick(channels, DownmixRole::Tfr), height_coeff);
    add_scaled(&mut left, pick(channels, DownmixRole::Tbl), height_coeff * surround_coeff);
    add_scaled(&mut right, pick(channels, DownmixRole::Tbr), height_coeff * surround_coeff);
    (left, right)
}

/// `M = (1/√2)·(FL + FR + k_h·(TFL + TFR)) + C
///      + k_s·(SL + SR + (1/√2)·(BL + BR) + k_h·(TBL + TBR))`.
pub fn itu_downmix_mono(
    channels: &[(DownmixRole, &[f64])],
    surround_coeff: f64,
    height_coeff: f64,
) -> Vec<f64> {
    let n = frame_count(channels);
    let mut out = vec![0.0; n];
    if n == 0 {
        return out;
    }
    add_scaled(&mut out, pick(channels, DownmixRole::Fl), ITU_CENTER_COEFF);
    add_scaled(&mut out, pick(channels, DownmixRole::Fr), ITU_CENTER_COEFF);
    add_scaled(&mut out, pick(channels, DownmixRole::C), 1.0);
    add_scaled(&mut out, pick(channels, DownmixRole::Sl), surround_coeff);
    add_scaled(&mut out, pick(channels, DownmixRole::Sr), surround_coeff);
    add_scaled(&mut out, pick(channels, DownmixRole::Bl), surround_coeff * ITU_CENTER_COEFF);
    add_scaled(&mut out, pick(channels, DownmixRole::Br), surround_coeff * ITU_CENTER_COEFF);
    add_scaled(&mut out, pick(channels, DownmixRole::Tfl), height_coeff * ITU_CENTER_COEFF);
    add_scaled(&mut out, pick(channels, DownmixRole::Tfr), height_coeff * ITU_CENTER_COEFF);
    add_scaled(&mut out, pick(channels, DownmixRole::Tbl), height_coeff * surround_coeff);
    add_scaled(&mut out, pick(channels, DownmixRole::Tbr), height_coeff * surround_coeff);
    out
}

/// Memoryless tanh saturation above `threshold`.
pub fn soft_limit(signal: &mut [f64], threshold: f64) {
    let headroom = 1.0 - threshold;
    for v in signal.iter_mut() {
        let magnitude = v.abs();
        if magnitude > threshold {
            let over = magnitude - threshold;
            let compressed = threshold + headroom * (over / headroom).tanh();
            *v = v.signum() * compressed;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn back_channels_fold_into_the_matching_side() {
        let sl = [1.0, 1.0];
        let bl = [1.0, 1.0];
        let (left, _) = itu_downmix_stereo(
            &[(DownmixRole::Sl, &sl), (DownmixRole::Bl, &bl)],
            ITU_CENTER_COEFF,
            ITU_CENTER_COEFF,
        );
        let want = ITU_CENTER_COEFF + ITU_CENTER_COEFF * ITU_CENTER_COEFF;
        assert!((left[0] - want).abs() < 1e-15);
    }

    #[test]
    fn centre_splits_equally_and_uses_the_exact_coefficient() {
        let c = [1.0];
        let (left, right) = itu_downmix_stereo(&[(DownmixRole::C, &c)], 0.0, 0.0);
        assert_eq!(left, right);
        assert!((left[0] - 1.0 / SQRT_2).abs() < 1e-16);
    }

    #[test]
    fn heights_fold_onto_their_own_side_at_the_height_coefficient() {
        let tfl = [1.0];
        let tbl = [1.0];
        let inputs = [(DownmixRole::Tfl, &tfl[..]), (DownmixRole::Tbl, &tbl[..])];
        let (left, right) = itu_downmix_stereo(&inputs, 0.5, ITU_CENTER_COEFF);
        assert!((left[0] - (ITU_CENTER_COEFF + ITU_CENTER_COEFF * 0.5)).abs() < 1e-15);
        assert_eq!(right[0], 0.0);

        let mono = itu_downmix_mono(&inputs, 0.5, ITU_CENTER_COEFF);
        let want = ITU_CENTER_COEFF * ITU_CENTER_COEFF + ITU_CENTER_COEFF * 0.5;
        assert!((mono[0] - want).abs() < 1e-15);
    }

    #[test]
    fn a_zero_height_coefficient_drops_the_height_channels() {
        let tfl = [1.0];
        let fl = [1.0];
        let inputs = [(DownmixRole::Fl, &fl[..]), (DownmixRole::Tfl, &tfl[..])];
        let (left, _) = itu_downmix_stereo(&inputs, 0.7071, 0.0);
        assert_eq!(left[0], 1.0);
        assert_eq!(itu_downmix_mono(&inputs, 0.7071, 0.0)[0], ITU_CENTER_COEFF);
    }

    #[test]
    fn soft_limit_leaves_sub_threshold_samples_alone() {
        let mut signal = [0.5, -0.9, 0.94];
        let before = signal;
        soft_limit(&mut signal, 0.95);
        assert_eq!(signal, before);
    }

    #[test]
    fn soft_limit_keeps_output_under_unity_and_preserves_sign() {
        let mut signal = [5.0, -5.0];
        soft_limit(&mut signal, 0.95);
        // The tanh asymptote is exactly 1.0 and saturates there in f64.
        assert!(signal[0] > 0.95 && signal[0] <= 1.0);
        assert!((signal[0] + signal[1]).abs() < 1e-15);
    }
}
