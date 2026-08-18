//! The 2x2 crosstalk-cancellation FIR matrix.

use crate::kernels::fft::fftconvolve;

/// `[out_speaker][in_ear]` FIR taps.
pub struct XtcFilterSet {
    pub taps: [[Vec<f64>; 2]; 2],
}

/// `speaker = H · ear_signal`, trimmed to the input length.
///
/// The inputs are the intended ear signals; the outputs are what the physical
/// speakers must emit so that those ear signals survive acoustic crosstalk.
pub fn apply_xtc(left: &[f64], right: &[f64], filters: &XtcFilterSet) -> (Vec<f64>, Vec<f64>) {
    let n = left.len();
    let mix = |ear_l: &[f64], ear_r: &[f64], taps: &[Vec<f64>; 2]| -> Vec<f64> {
        let a = fftconvolve(ear_l, &taps[0]);
        let b = fftconvolve(ear_r, &taps[1]);
        a.iter().zip(b.iter()).take(n).map(|(x, y)| x + y).collect()
    };
    (
        mix(left, right, &filters.taps[0]),
        mix(left, right, &filters.taps[1]),
    )
}
