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
