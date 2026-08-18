//! `scipy.signal.sosfiltfilt` — zero-phase forward/backward SOS filtering.
//!
//! The odd extension, the `padlen` rule, and the `sosfilt_zi` seeding are all
//! load-bearing: the mono-maker's net effect flips sign if they are wrong
//! (see ledger D9 in `docs/contracts/preview_export_parity.md`).

use super::biquad::SosFilter;

/// SciPy's default `padlen`: `3 * ntaps`, where `ntaps` shrinks for sections
/// that are really first-order.
pub fn default_padlen(sections: &[[f64; 6]]) -> usize {
    let n = sections.len();
    let zero_b2 = sections.iter().filter(|s| s[2] == 0.0).count();
    let zero_a2 = sections.iter().filter(|s| s[5] == 0.0).count();
    let ntaps = 2 * n + 1 - zero_b2.min(zero_a2);
    3 * ntaps
}

/// `scipy.signal._arraytools.odd_ext` — antisymmetric reflection about each
/// endpoint.
pub fn odd_ext(x: &[f64], n: usize) -> Vec<f64> {
    if n < 1 {
        return x.to_vec();
    }
    let len = x.len();
    let mut out = Vec::with_capacity(len + 2 * n);
    for i in (1..=n).rev() {
        out.push(2.0 * x[0] - x[i]);
    }
    out.extend_from_slice(x);
    for i in 1..=n {
        out.push(2.0 * x[len - 1] - x[len - 1 - i]);
    }
    out
}

/// `scipy.signal.sosfiltfilt(sos, x)` with the default odd padding.
///
/// Returns `None` when the signal is not longer than the pad length — the
/// same condition SciPy raises on, which call sites handle by falling back to
/// a single forward pass.
pub fn sosfiltfilt(sections: &[[f64; 6]], x: &[f64]) -> Option<Vec<f64>> {
    let edge = default_padlen(sections);
    if x.len() <= edge {
        return None;
    }
    let ext = odd_ext(x, edge);

    let mut fwd = SosFilter::from_flat(sections);
    fwd.set_step_state(ext[0]);
    let mut y = ext;
    fwd.process(&mut y);

    let last = *y.last().expect("extended signal cannot be empty");
    y.reverse();
    let mut bwd = SosFilter::from_flat(sections);
    bwd.set_step_state(last);
    bwd.process(&mut y);
    y.reverse();

    Some(y[edge..y.len() - edge].to_vec())
}
