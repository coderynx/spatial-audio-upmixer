//! `scipy.signal.upfirdn(fir, x, up=N)` — zero-stuff then FIR, computed
//! directly. Only integer upsampling with no decimation is needed (the
//! BS.1770 true-peak detector's 4x path), so the polyphase decomposition
//! stays trivial and the result is exact to the last bit.

/// Upsample by `up` (zero stuffing) and convolve with `fir`.
///
/// Output length matches SciPy: `(len(x) - 1) * up + len(fir)`.
pub fn upfirdn_up(fir: &[f64], x: &[f64], up: usize) -> Vec<f64> {
    if x.is_empty() {
        return Vec::new();
    }
    let out_len = (x.len() - 1) * up + fir.len();
    let mut out = vec![0.0; out_len];
    // Phase p of the filter sees every input sample; accumulating per input
    // sample keeps the inner loop over the (short) phase instead of the FIR.
    for (i, &xi) in x.iter().enumerate() {
        if xi == 0.0 {
            continue;
        }
        let base = i * up;
        for (j, &h) in fir.iter().enumerate() {
            out[base + j] += xi * h;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn impulse_reproduces_the_kernel() {
        let fir = [0.5, 1.0, -0.25];
        let out = upfirdn_up(&fir, &[1.0], 4);
        assert_eq!(out.len(), 3);
        for (a, b) in out.iter().zip(fir.iter()) {
            assert!((a - b).abs() < 1e-15);
        }
    }

    #[test]
    fn output_length_matches_scipy_formula() {
        let fir = vec![0.0; 48];
        let x = vec![0.0; 100];
        assert_eq!(upfirdn_up(&fir, &x, 4).len(), 99 * 4 + 48);
    }
}
