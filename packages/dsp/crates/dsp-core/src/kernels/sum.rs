//! Pairwise summation, matching NumPy's accumulation shape.
//!
//! Naive accumulation over a multi-minute track drifts far enough to show up
//! in a gain derived from the sum; pairwise keeps the error at O(log n) eps
//! for free.

const BLOCK: usize = 128;

pub fn pairwise_sum(values: &[f64]) -> f64 {
    if values.len() <= BLOCK {
        return values.iter().sum();
    }
    let half = values.len() / 2;
    pairwise_sum(&values[..half]) + pairwise_sum(&values[half..])
}

/// Sum of squares, pairwise, without materializing the squared vector.
pub fn pairwise_sum_squares(values: &[f64]) -> f64 {
    if values.len() <= BLOCK {
        return values.iter().map(|v| v * v).sum();
    }
    let half = values.len() / 2;
    pairwise_sum_squares(&values[..half]) + pairwise_sum_squares(&values[half..])
}

pub fn rms(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    (pairwise_sum_squares(values) / values.len() as f64).sqrt()
}
