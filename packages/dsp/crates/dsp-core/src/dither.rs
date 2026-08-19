//! Bit-depth reduction for the export tail: round-to-nearest, TPDF dither and
//! optional second-order noise shaping. See
//! `docs/standards/loudness_dsp_bs1770.md` §"Export tail".

use crate::kernels::rng::next_unit;

/// Error-feedback taps of `(1 - z^-1)^2`, the shaper `Shaped` runs.
const SHAPER: [f64; 2] = [2.0, -1.0];

/// Largest quantization error TPDF plus round-to-nearest can produce, in LSB.
/// The shaper's feedback is clamped to it so a clipped sample cannot make the
/// error loop diverge.
const MAX_ERROR_LSB: f64 = 1.5;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DitherMode {
    /// Round to nearest, no dither.
    Off,
    /// ±1 LSB triangular dither.
    Tpdf,
    /// TPDF dither plus second-order error feedback.
    Shaped,
}

impl DitherMode {
    pub fn parse(name: &str) -> Option<Self> {
        match name {
            "off" => Some(Self::Off),
            "tpdf" => Some(Self::Tpdf),
            "shaped" => Some(Self::Shaped),
            _ => None,
        }
    }
}

/// Seed of the dither stream for channel `index`. SplitMix64 decorrelates
/// sequential seeds, so consecutive channels draw independent noise.
pub fn channel_seed(seed: u64, index: usize) -> u64 {
    seed.wrapping_add(index as u64)
}

/// Quantize one channel of float samples in `[-1, 1]` onto the `bits`-deep
/// integer PCM lattice, returned in the same normalized scale.
///
/// The result is an exact multiple of `2^-(bits-1)`, so a writer handing it
/// back to libsndfile as float reproduces these codes exactly.
pub fn quantize(channel: &mut [f64], bits: u32, mode: DitherMode, seed: u64) {
    let scale = (1u64 << (bits - 1)) as f64;
    let (min_code, max_code) = (-scale, scale - 1.0);
    let mut state = seed;
    let (mut e1, mut e2) = (0.0_f64, 0.0_f64);
    for sample in channel.iter_mut() {
        let target = *sample * scale;
        let shaped = match mode {
            DitherMode::Shaped => target - (SHAPER[0] * e1 + SHAPER[1] * e2),
            _ => target,
        };
        let noise = match mode {
            DitherMode::Off => 0.0,
            _ => next_unit(&mut state) - next_unit(&mut state),
        };
        let code = (shaped + noise).round().clamp(min_code, max_code);
        e2 = e1;
        e1 = (code - shaped).clamp(-MAX_ERROR_LSB, MAX_ERROR_LSB);
        *sample = code / scale;
    }
}
