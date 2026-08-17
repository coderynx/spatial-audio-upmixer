//! SplitMix64, the seeded generator every deterministic DSP stage draws from.
//!
//! Chosen because it is pure integer arithmetic: the same seed yields the same
//! stream on every target, so PyO3 and wasm build identical filters.

pub fn splitmix64(state: &mut u64) -> u64 {
    *state = state.wrapping_add(0x9E37_79B9_7F4A_7C15);
    let mut z = *state;
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^ (z >> 31)
}

/// Next draw in `[0, 1)`, taken from the top 53 bits so the mapping is exact.
pub fn next_unit(state: &mut u64) -> f64 {
    (splitmix64(state) >> 11) as f64 / (1u64 << 53) as f64
}

/// Next draw as ±1, from the sign bit rather than a comparison on `next_unit`.
pub fn next_sign(state: &mut u64) -> f64 {
    if splitmix64(state) >> 63 != 0 {
        1.0
    } else {
        -1.0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn draws_are_in_range_and_reproducible() {
        let mut a = 12345;
        let mut b = 12345;
        for _ in 0..1000 {
            let x = next_unit(&mut a);
            assert!((0.0..1.0).contains(&x));
            assert_eq!(x, next_unit(&mut b));
        }
        assert!(next_sign(&mut a).abs() == 1.0);
    }
}
