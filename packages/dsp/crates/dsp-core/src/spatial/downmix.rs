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

impl DownmixRole {
    /// The channel names `packages/core` uses; anything else (LFE) has no
    /// downmix contribution.
    pub fn from_name(name: &str) -> Option<Self> {
        Some(match name {
            "FL" => Self::Fl,
            "FR" => Self::Fr,
            "C" => Self::C,
            "SL" => Self::Sl,
            "SR" => Self::Sr,
            "BL" => Self::Bl,
            "BR" => Self::Br,
            "TFL" => Self::Tfl,
            "TFR" => Self::Tfr,
            "TBL" => Self::Tbl,
            "TBR" => Self::Tbr,
            _ => return None,
        })
    }
}

/// The five weighted channels of the 5.1 re-render delivery specs measure
/// integrated loudness on, in order. LFE is absent: BS.1770 weights it zero,
/// so it never reaches the loudness sum.
pub const FOLD_51_CHANNELS: [&str; 5] = ["FL", "FR", "C", "SL", "SR"];

/// BS.1770-5 Annex 3 Table 5 weights for [`FOLD_51_CHANNELS`] — only the
/// ear-level side surrounds carry the +1.5 dB.
pub const FOLD_51_WEIGHTS: [f64; 5] = [1.0, 1.0, 1.0, 1.41, 1.41];

/// The 5.1 re-render Dolby Atmos Music and Netflix measure integrated
/// loudness on: heights onto their base-layer channels, the back pair onto
/// the surround pair. Coefficients are BS.775-4 Annex D's `b₀` for
/// back→side and the project's `k_h` for heights, both 1/√2 — see
/// docs/standards/spatial_layouts_bs775_bs2051.md §"5.1 re-render fold".
///
/// The fold is memoryless, so the offline and streaming measurement paths run
/// the same taps a block at a time.
pub struct FoldTo51 {
    /// Per [`FOLD_51_CHANNELS`] entry, the `(source index, gain)` taps that
    /// sum into it.
    taps: [Vec<(usize, f64)>; 5],
}

impl FoldTo51 {
    /// `None` when `names` carries no back or height channel, where the fold
    /// would be the identity and the delivered bed is already the programme.
    pub fn new<S: AsRef<str>>(names: &[S]) -> Option<Self> {
        let roles: Vec<Option<DownmixRole>> =
            names.iter().map(|n| DownmixRole::from_name(n.as_ref())).collect();
        let wider_than_51 = roles.iter().flatten().any(|r| {
            matches!(
                r,
                DownmixRole::Bl
                    | DownmixRole::Br
                    | DownmixRole::Tfl
                    | DownmixRole::Tfr
                    | DownmixRole::Tbl
                    | DownmixRole::Tbr
            )
        });
        if !wider_than_51 {
            return None;
        }

        let k = ITU_CENTER_COEFF;
        let mut taps: [Vec<(usize, f64)>; 5] = Default::default();
        for (index, role) in roles.iter().enumerate() {
            let Some(role) = role else { continue };
            let (target, gain) = match role {
                DownmixRole::Fl => (0, 1.0),
                DownmixRole::Fr => (1, 1.0),
                DownmixRole::C => (2, 1.0),
                DownmixRole::Sl => (3, 1.0),
                DownmixRole::Sr => (4, 1.0),
                DownmixRole::Bl => (3, k),
                DownmixRole::Br => (4, k),
                DownmixRole::Tfl => (0, k),
                DownmixRole::Tfr => (1, k),
                DownmixRole::Tbl => (3, k),
                DownmixRole::Tbr => (4, k),
            };
            taps[target].push((index, gain));
        }
        Some(Self { taps })
    }

    /// Fold `frames` of the channel-major bed `src` into `dst`, which is
    /// resized to the five [`FOLD_51_CHANNELS`].
    pub fn apply(&self, src: &[&[f64]], frames: usize, dst: &mut Vec<Vec<f64>>) {
        dst.resize(FOLD_51_CHANNELS.len(), Vec::new());
        for (out, taps) in dst.iter_mut().zip(self.taps.iter()) {
            out.clear();
            out.resize(frames, 0.0);
            for (index, gain) in taps {
                let Some(source) = src.get(*index) else { continue };
                for (o, s) in out.iter_mut().zip(source[..frames].iter()) {
                    *o += gain * s;
                }
            }
        }
    }
}

/// The `(left, right)` stereo-downmix gain BS.775-4 Annex 4 Table 2 assigns a
/// role, plus the project's height fold. The sole source of the coefficient
/// table: `itu_downmix_stereo` and `stream::output`'s streaming collapse both
/// read it instead of each carrying their own copy.
pub fn stereo_pair(role: DownmixRole, surround_coeff: f64, height_coeff: f64) -> (f64, f64) {
    match role {
        DownmixRole::Fl => (1.0, 0.0),
        DownmixRole::Fr => (0.0, 1.0),
        DownmixRole::C => (ITU_CENTER_COEFF, ITU_CENTER_COEFF),
        DownmixRole::Sl => (surround_coeff, 0.0),
        DownmixRole::Sr => (0.0, surround_coeff),
        DownmixRole::Bl => (surround_coeff * ITU_CENTER_COEFF, 0.0),
        DownmixRole::Br => (0.0, surround_coeff * ITU_CENTER_COEFF),
        DownmixRole::Tfl => (height_coeff, 0.0),
        DownmixRole::Tfr => (0.0, height_coeff),
        DownmixRole::Tbl => (height_coeff * surround_coeff, 0.0),
        DownmixRole::Tbr => (0.0, height_coeff * surround_coeff),
    }
}

const ALL_ROLES: [DownmixRole; 11] = [
    DownmixRole::Fl,
    DownmixRole::Fr,
    DownmixRole::C,
    DownmixRole::Sl,
    DownmixRole::Sr,
    DownmixRole::Bl,
    DownmixRole::Br,
    DownmixRole::Tfl,
    DownmixRole::Tfr,
    DownmixRole::Tbl,
    DownmixRole::Tbr,
];

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

    for role in ALL_ROLES {
        let (gl, gr) = stereo_pair(role, surround_coeff, height_coeff);
        let signal = pick(channels, role);
        add_scaled(&mut left, signal, gl);
        add_scaled(&mut right, signal, gr);
    }
    (left, right)
}

/// Add the front residual that makes a routed bed fold back to `input`.
///
/// LFE has no [`DownmixRole`], so it is intentionally unchanged.
pub fn apply_stereo_downmix_lock<I>(
    roles: I,
    bed: &mut [Vec<f64>],
    input_left: &[f64],
    input_right: &[f64],
    surround_coeff: f64,
    height_coeff: f64,
) where
    I: IntoIterator<Item = Option<DownmixRole>>,
{
    let n = input_left.len().min(input_right.len());
    let mut residual_left = input_left[..n].to_vec();
    let mut residual_right = input_right[..n].to_vec();
    let mut front = [None; 2];

    for (index, role) in roles.into_iter().enumerate() {
        let Some(role) = role else { continue };
        let Some(channel) = bed.get(index) else {
            continue;
        };
        let (left_gain, right_gain) = stereo_pair(role, surround_coeff, height_coeff);
        for (residual, sample) in residual_left.iter_mut().zip(channel.iter()) {
            *residual -= left_gain * sample;
        }
        for (residual, sample) in residual_right.iter_mut().zip(channel.iter()) {
            *residual -= right_gain * sample;
        }
        match role {
            DownmixRole::Fl => front[0] = Some(index),
            DownmixRole::Fr => front[1] = Some(index),
            _ => {}
        }
    }

    let (Some(left), Some(right)) = (front[0], front[1]) else {
        return;
    };
    for (target, residual) in bed[left].iter_mut().zip(residual_left) {
        *target += residual;
    }
    for (target, residual) in bed[right].iter_mut().zip(residual_right) {
        *target += residual;
    }
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
