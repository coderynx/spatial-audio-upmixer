//! Real FFT helpers over `realfft`/`rustfft`.
//!
//! These do not reproduce pocketfft bit-for-bit — no FFT library does — but
//! agree to ~1e-15 per element, which lands far inside the per-stage budget
//! in `packages/dsp/AGENTS.md`.

use realfft::RealFftPlanner;
use rustfft::num_complex::Complex64;

/// Cached real-FFT plans for one transform length.
pub struct RealFft {
    len: usize,
    forward: std::sync::Arc<dyn realfft::RealToComplex<f64>>,
    inverse: std::sync::Arc<dyn realfft::ComplexToReal<f64>>,
}

impl RealFft {
    pub fn new(len: usize) -> Self {
        let mut planner = RealFftPlanner::<f64>::new();
        Self {
            len,
            forward: planner.plan_fft_forward(len),
            inverse: planner.plan_fft_inverse(len),
        }
    }

    pub fn len(&self) -> usize {
        self.len
    }

    pub fn is_empty(&self) -> bool {
        self.len == 0
    }

    /// `numpy.fft.rfft` of a signal zero-padded (or truncated) to the plan
    /// length; result has `len/2 + 1` bins.
    pub fn rfft(&self, signal: &[f64]) -> Vec<Complex64> {
        let mut input = vec![0.0; self.len];
        let n = signal.len().min(self.len);
        input[..n].copy_from_slice(&signal[..n]);
        let mut spectrum = self.forward.make_output_vec();
        self.forward
            .process(&mut input, &mut spectrum)
            .expect("rfft length mismatch");
        spectrum
    }

    /// `numpy.fft.irfft`, including the 1/n normalization NumPy applies.
    pub fn irfft(&self, spectrum: &[Complex64]) -> Vec<f64> {
        let mut input = self.inverse.make_input_vec();
        let n = spectrum.len().min(input.len());
        input[..n].copy_from_slice(&spectrum[..n]);
        // A real signal's DC and Nyquist bins carry no imaginary part; the
        // inverse plan rejects the transform otherwise.
        input[0].im = 0.0;
        if self.len % 2 == 0 {
            let last = input.len() - 1;
            input[last].im = 0.0;
        }
        let mut output = self.inverse.make_output_vec();
        self.inverse
            .process(&mut input, &mut output)
            .expect("irfft length mismatch");
        let scale = 1.0 / self.len as f64;
        for v in output.iter_mut() {
            *v *= scale;
        }
        output
    }
}

/// Next length ≥ `n` that factors into 2, 3, and 5 — the sizes `rustfft`
/// plans without falling back to Bluestein.
pub fn next_fast_len(n: usize) -> usize {
    if n <= 6 {
        return n.max(1);
    }
    let mut best = usize::MAX;
    let mut p5 = 1usize;
    while p5 < n * 2 {
        let mut p35 = p5;
        while p35 < n * 2 {
            let mut p2 = p35;
            while p2 < n {
                p2 *= 2;
            }
            if p2 < best {
                best = p2;
            }
            if p35 > usize::MAX / 3 {
                break;
            }
            p35 *= 3;
        }
        if p5 > usize::MAX / 5 {
            break;
        }
        p5 *= 5;
    }
    best
}

/// Full linear convolution via FFT, matching `scipy.signal.fftconvolve`
/// with `mode="full"`.
pub fn fftconvolve(a: &[f64], b: &[f64]) -> Vec<f64> {
    if a.is_empty() || b.is_empty() {
        return Vec::new();
    }
    let out_len = a.len() + b.len() - 1;
    // Direct convolution wins outright at these sizes and avoids the FFT's
    // rounding entirely.
    if a.len().min(b.len()) <= 16 {
        let mut out = vec![0.0; out_len];
        for (i, &x) in a.iter().enumerate() {
            if x == 0.0 {
                continue;
            }
            for (j, &h) in b.iter().enumerate() {
                out[i + j] += x * h;
            }
        }
        return out;
    }
    // A whole-file transform of a multi-minute bed costs hundreds of
    // megabytes; overlap-save computes the identical linear convolution in
    // bounded memory.
    let (long, short) = if a.len() >= b.len() { (a, b) } else { (b, a) };
    if long.len() > 8 * short.len() && short.len() >= 64 {
        return overlap_save(long, short, out_len);
    }

    let n = next_fast_len(out_len);
    let fft = RealFft::new(n);
    let sa = fft.rfft(a);
    let sb = fft.rfft(b);
    let prod: Vec<Complex64> = sa.iter().zip(sb.iter()).map(|(x, y)| x * y).collect();
    let mut out = fft.irfft(&prod);
    out.truncate(out_len);
    out
}

fn overlap_save(signal: &[f64], kernel: &[f64], out_len: usize) -> Vec<f64> {
    let m = kernel.len();
    let n = next_fast_len((8 * m).max(2048));
    let hop = n - m + 1;
    let fft = RealFft::new(n);
    let spectrum_k = fft.rfft(kernel);

    // The signal reads as if zero-padded in both directions: the output runs
    // past its end by the kernel's length, and each block also reaches back
    // for the previous block's tail so the circular wrap lands entirely in
    // the discarded head.
    let sample = |i: i64| -> f64 {
        if i < 0 || i as usize >= signal.len() {
            0.0
        } else {
            signal[i as usize]
        }
    };

    let mut out = vec![0.0; out_len];
    let mut block = vec![0.0; n];
    let mut pos = 0usize;
    while pos < out_len {
        let base = pos as i64 - (m as i64 - 1);
        for (j, slot) in block.iter_mut().enumerate() {
            *slot = sample(base + j as i64);
        }

        let spectrum: Vec<Complex64> = fft
            .rfft(&block)
            .iter()
            .zip(spectrum_k.iter())
            .map(|(x, y)| x * y)
            .collect();
        let filtered = fft.irfft(&spectrum);

        let copy = hop.min(out_len - pos);
        out[pos..pos + copy].copy_from_slice(&filtered[m - 1..m - 1 + copy]);
        pos += hop;
    }
    out
}
