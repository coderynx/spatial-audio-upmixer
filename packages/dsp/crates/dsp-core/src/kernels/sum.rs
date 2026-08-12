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

pub fn pairwise_mean(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    pairwise_sum(values) / values.len() as f64
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pairwise_beats_naive_on_a_long_constant_run() {
        let values = vec![0.1_f64; 1_000_000];
        let exact = 100_000.0_f64;
        let naive = values.iter().fold(0.0, |a: f64, v| a + v);
        assert!((pairwise_sum(&values) - exact).abs() <= (naive - exact).abs());
    }

    #[test]
    fn rms_of_unit_dc_is_one() {
        assert!((rms(&vec![1.0; 1000]) - 1.0).abs() < 1e-15);
    }
}
