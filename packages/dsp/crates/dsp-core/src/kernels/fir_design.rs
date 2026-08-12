//! FIR design ports of `scipy.signal.firwin2` and
//! `scipy.signal.minimum_phase(..., method="homomorphic", half=False)`.
//!
//! Both are transcribed from the SciPy source rather than re-derived; the
//! pinned SciPy version is recorded in `packages/dsp/AGENTS.md`. The shipped
//! EQ curves are designed through this pair, so a drift here is a drift in
//! every master.

use rustfft::num_complex::Complex64;

use super::fft::RealFft;

/// `numpy.interp` — linear interpolation with endpoint clamping.
fn interp(x: f64, xp: &[f64], fp: &[f64]) -> f64 {
    if x <= xp[0] {
        return fp[0];
    }
    if x >= xp[xp.len() - 1] {
        return fp[fp.len() - 1];
    }
    let mut hi = 1;
    while hi < xp.len() - 1 && xp[hi] < x {
        hi += 1;
    }
    let lo = hi - 1;
    let span = xp[hi] - xp[lo];
    if span == 0.0 {
        return fp[hi];
    }
    fp[lo] + (fp[hi] - fp[lo]) * (x - xp[lo]) / span
}

/// `scipy.signal.windows.hamming(m, sym=True)`.
pub fn hamming(m: usize) -> Vec<f64> {
    if m <= 1 {
        return vec![1.0; m];
    }
    (0..m)
        .map(|n| {
            let fac = -std::f64::consts::PI
                + 2.0 * std::f64::consts::PI * n as f64 / (m - 1) as f64;
            0.54 + 0.46 * fac.cos()
        })
        .collect()
}

/// Repeated breakpoints break the interpolation; SciPy nudges them apart by
/// one eps of Nyquist before interpolating.
fn tweak_repeats(freq: &[f64], nyq: f64) -> Vec<f64> {
    let mut out = freq.to_vec();
    if !out.windows(2).any(|w| w[1] == w[0]) {
        return out;
    }
    let eps = f64::EPSILON * nyq;
    for k in 0..out.len() - 1 {
        if out[k] == out[k + 1] {
            out[k] -= eps;
            out[k + 1] += eps;
        }
    }
    out
}

/// `scipy.signal.firwin2(numtaps, freq, gain)` with the default Hamming
/// window and `fs = 2` (so `freq` runs 0..1 with 1 at Nyquist).
///
/// Only the Type I/II linear-phase case is implemented — the pipeline never
/// designs antisymmetric filters.
pub fn firwin2(numtaps: usize, freq: &[f64], gain: &[f64]) -> Vec<f64> {
    assert_eq!(freq.len(), gain.len(), "freq and gain must be the same length");
    assert!(numtaps >= 1, "numtaps must be positive");
    let nyq = 1.0;
    assert!(freq[0] == 0.0 && freq[freq.len() - 1] == nyq,
            "freq must start at 0 and end at Nyquist");

    let nfreqs = 1 + 2usize.pow((numtaps as f64).log2().ceil() as u32);
    let freq = tweak_repeats(freq, nyq);

    let n_out = 2 * (nfreqs - 1);
    let fft = RealFft::new(n_out);
    let mut spectrum = Vec::with_capacity(nfreqs);
    for i in 0..nfreqs {
        let x = nyq * i as f64 / (nfreqs - 1) as f64;
        let fx = interp(x, &freq, gain);
        let phase = -(numtaps as f64 - 1.0) / 2.0 * std::f64::consts::PI * x / nyq;
        spectrum.push(Complex64::from_polar(fx, phase));
    }
    let full = fft.irfft(&spectrum);

    let window = hamming(numtaps);
    full.iter()
        .take(numtaps)
        .zip(window.iter())
        .map(|(v, w)| v * w)
        .collect()
}

/// `scipy.signal.minimum_phase(h, method="homomorphic", half=False)`.
///
/// Works entirely in the real-FFT domain: the log-magnitude spectrum of a
/// real filter is even, so its inverse transform (the cepstrum) is real, and
/// the folded cepstrum's exponentiated spectrum is conjugate-symmetric.
pub fn minimum_phase(h: &[f64]) -> Vec<f64> {
    assert!(h.len() > 2, "h must be at least 3 samples long");
    let n_fft = {
        let target = 2.0 * (h.len() - 1) as f64 / 0.01;
        2usize.pow(target.log2().ceil() as u32)
    };
    assert!(n_fft >= h.len(), "n_fft must be at least len(h)");

    let fft = RealFft::new(n_fft);
    let mut mag: Vec<f64> = fft.rfft(h).iter().map(|c| c.norm()).collect();

    // Keep the log finite where the response nulls, exactly as SciPy does.
    let floor = mag
        .iter()
        .copied()
        .filter(|v| *v > 0.0)
        .fold(f64::INFINITY, f64::min);
    for v in mag.iter_mut() {
        *v = (*v + 1e-7 * floor).ln();
    }

    // Real, even half-spectrum -> real, even cepstrum.
    let cepstrum = fft.irfft(&mag.iter().map(|v| Complex64::new(*v, 0.0)).collect::<Vec<_>>());

    // Homomorphic fold: double the causal half, keep DC, halve Nyquist.
    let stop = n_fft / 2;
    let mut folded = vec![0.0; n_fft];
    folded[0] = cepstrum[0];
    for i in 1..stop {
        folded[i] = 2.0 * cepstrum[i];
    }
    folded[stop] = cepstrum[stop] * (1.0 + (n_fft % 2) as f64);

    let exponentiated: Vec<Complex64> = fft.rfft(&folded).iter().map(|c| c.exp()).collect();
    let out = fft.irfft(&exponentiated);
    out[..h.len()].to_vec()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hamming_is_symmetric_with_the_standard_endpoints() {
        let w = hamming(9);
        assert!((w[0] - 0.08).abs() < 1e-12);
        assert!((w[8] - 0.08).abs() < 1e-12);
        assert!((w[4] - 1.0).abs() < 1e-12);
        for i in 0..9 {
            assert!((w[i] - w[8 - i]).abs() < 1e-15);
        }
    }

    #[test]
    fn flat_response_designs_a_delta() {
        let taps = firwin2(65, &[0.0, 0.5, 1.0], &[1.0, 1.0, 1.0]);
        assert!((taps[32] - 1.0).abs() < 1e-6, "center tap {}", taps[32]);
        for (i, t) in taps.iter().enumerate() {
            if i != 32 {
                assert!(t.abs() < 1e-6, "tap {i} = {t}");
            }
        }
    }

    #[test]
    fn minimum_phase_preserves_magnitude_and_is_causal() {
        let linear = firwin2(255, &[0.0, 0.3, 0.6, 1.0], &[1.0, 1.5, 0.7, 1.0]);
        let minphase = minimum_phase(&linear);
        assert_eq!(minphase.len(), linear.len());

        // Energy is concentrated at the front for a minimum-phase filter.
        let head: f64 = minphase[..64].iter().map(|v| v * v).sum();
        let tail: f64 = minphase[64..].iter().map(|v| v * v).sum();
        assert!(head > tail * 10.0, "head {head} tail {tail}");

        let fft = RealFft::new(2048);
        let a: Vec<f64> = fft.rfft(&linear).iter().map(|c| c.norm()).collect();
        let b: Vec<f64> = fft.rfft(&minphase).iter().map(|c| c.norm()).collect();
        for (i, (x, y)) in a.iter().zip(b.iter()).enumerate() {
            assert!((x - y).abs() < 5e-3, "bin {i}: {x} vs {y}");
        }
    }
}
