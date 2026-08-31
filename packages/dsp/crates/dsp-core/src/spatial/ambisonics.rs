//! Order-3 real spherical-harmonic (ACN/N3D) ambisonic encoding and the
//! HOA-to-binaural decode convolution.

use crate::kernels::fft::fftconvolve;

pub const AMBISONIC_ORDER: usize = 3;
pub const N_ACN_CHANNELS: usize = (AMBISONIC_ORDER + 1) * (AMBISONIC_ORDER + 1);

/// ACN/N3D encode gains for one source direction.
///
/// Azimuth is 0 at front and positive to the left; elevation is 0 at the
/// horizon and positive up — the convention `binaural/geometry.py` uses.
pub fn encode_gains(azimuth_rad: f64, elevation_rad: f64) -> [f64; N_ACN_CHANNELS] {
    let (theta, delta) = (azimuth_rad, elevation_rad);
    let (sin_t, cos_t) = (theta.sin(), theta.cos());
    let (sin_d, cos_d) = (delta.sin(), delta.cos());
    let (sin_2t, cos_2t) = ((2.0 * theta).sin(), (2.0 * theta).cos());
    let (sin_3t, cos_3t) = ((3.0 * theta).sin(), (3.0 * theta).cos());

    let mut g = [0.0; N_ACN_CHANNELS];
    g[0] = 1.0;
    g[1] = 3.0_f64.sqrt() * cos_d * sin_t;
    g[2] = 3.0_f64.sqrt() * sin_d;
    g[3] = 3.0_f64.sqrt() * cos_d * cos_t;
    g[4] = (15.0_f64.sqrt() / 2.0) * cos_d.powi(2) * sin_2t;
    g[5] = 15.0_f64.sqrt() * sin_d * cos_d * sin_t;
    g[6] = (5.0_f64.sqrt() / 2.0) * (3.0 * sin_d.powi(2) - 1.0);
    g[7] = 15.0_f64.sqrt() * sin_d * cos_d * cos_t;
    g[8] = (15.0_f64.sqrt() / 2.0) * cos_d.powi(2) * cos_2t;
    g[9] = (35.0_f64 / 8.0).sqrt() * cos_d.powi(3) * sin_3t;
    g[10] = (105.0_f64.sqrt() / 2.0) * sin_d * cos_d.powi(2) * sin_2t;
    g[11] = (21.0_f64 / 8.0).sqrt() * cos_d * (5.0 * sin_d.powi(2) - 1.0) * sin_t;
    g[12] = 0.5 * sin_d * (5.0 * sin_d.powi(2) - 3.0);
    g[13] = (21.0_f64 / 8.0).sqrt() * cos_d * (5.0 * sin_d.powi(2) - 1.0) * cos_t;
    g[14] = (105.0_f64.sqrt() / 2.0) * sin_d * cos_d.powi(2) * cos_2t;
    g[15] = (35.0_f64 / 8.0).sqrt() * cos_d.powi(3) * cos_3t;
    g
}

/// The 16-channel ambisonic bus a set of positional speakers encodes into.
pub struct HoaBus {
    pub channels: Vec<Vec<f64>>,
}

impl HoaBus {
    pub fn new(n_samples: usize) -> Self {
        Self {
            channels: vec![vec![0.0; n_samples]; N_ACN_CHANNELS],
        }
    }

    /// Encode one speaker feed at a fixed direction and sum it in.
    pub fn add_source(&mut self, signal: &[f64], azimuth_rad: f64, elevation_rad: f64) {
        if signal.iter().all(|v| *v == 0.0) {
            return;
        }
        let gains = encode_gains(azimuth_rad, elevation_rad);
        for (acn, gain) in gains.iter().enumerate() {
            if *gain == 0.0 {
                continue;
            }
            for (out, v) in self.channels[acn].iter_mut().zip(signal.iter()) {
                *out += gain * v;
            }
        }
    }
}

/// Decode FIR taps, `[acn][ear][tap]`.
pub struct DecodeFilterSet {
    pub taps: Vec<[Vec<f64>; 2]>,
}

/// Convolve the HOA bus to stereo, trimmed to the input length.
pub fn decode_to_binaural(hoa: &HoaBus, filters: &DecodeFilterSet) -> (Vec<f64>, Vec<f64>) {
    assert_eq!(
        hoa.channels.len(),
        N_ACN_CHANNELS,
        "unexpected HOA channel count"
    );
    assert_eq!(
        filters.taps.len(),
        N_ACN_CHANNELS,
        "unexpected decode filter count"
    );
    let n_samples = hoa.channels[0].len();
    let mut left = vec![0.0; n_samples];
    let mut right = vec![0.0; n_samples];

    for (acn, channel) in hoa.channels.iter().enumerate() {
        if channel.iter().all(|v| *v == 0.0) {
            continue;
        }
        for (ear, out) in [&mut left, &mut right].into_iter().enumerate() {
            let convolved = fftconvolve(channel, &filters.taps[acn][ear]);
            for (dst, src) in out.iter_mut().zip(convolved.iter()) {
                *dst += src;
            }
        }
    }
    (left, right)
}
