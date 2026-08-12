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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_identity_matrix_passes_both_ears_through() {
        let filters = XtcFilterSet {
            taps: [
                [vec![1.0, 0.0], vec![0.0, 0.0]],
                [vec![0.0, 0.0], vec![1.0, 0.0]],
            ],
        };
        let l = [1.0, 2.0, 3.0];
        let r = [-1.0, -2.0, -3.0];
        let (sl, sr) = apply_xtc(&l, &r, &filters);
        assert_eq!(sl, l.to_vec());
        assert_eq!(sr, r.to_vec());
    }

    #[test]
    fn a_swap_matrix_exchanges_the_ears() {
        let filters = XtcFilterSet {
            taps: [
                [vec![0.0], vec![1.0]],
                [vec![1.0], vec![0.0]],
            ],
        };
        let (sl, sr) = apply_xtc(&[1.0, 2.0], &[3.0, 4.0], &filters);
        assert_eq!(sl, vec![3.0, 4.0]);
        assert_eq!(sr, vec![1.0, 2.0]);
    }
}
