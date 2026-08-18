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
