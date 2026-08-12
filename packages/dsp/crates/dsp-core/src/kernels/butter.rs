//! Butterworth IIR design matching `scipy.signal.butter(..., output="sos")`.
//!
//! Only low/high-pass is implemented — the only shapes the pipeline designs.
//! Both have `N` poles and `N` identical real zeros (−1 low-pass, +1
//! high-pass) after the bilinear map, so `zpk2sos`'s pairing reduces to the
//! real-root case. The odd-order padding matters: SciPy appends a pole and a
//! zero at the origin, and the appended zero then wins the nearest-zero
//! search for the highest-Q section, which changes that section's numerator.

use num_complex::Complex64;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BandType {
    Low,
    High,
}

/// Analog Butterworth prototype poles, `scipy.signal.buttap`.
fn buttap(order: usize) -> Vec<Complex64> {
    let n = order as i64;
    (0..order)
        .map(|k| {
            let m = -n + 1 + 2 * k as i64;
            let theta = std::f64::consts::PI * (m as f64) / (2.0 * n as f64);
            -Complex64::from_polar(1.0, theta)
        })
        .collect()
}

/// `scipy.signal.butter(order, cutoff_hz / nyquist, btype, output="sos")`.
///
/// `wn` is the cutoff normalized so that 1.0 is Nyquist, matching how every
/// call site in the pipeline passes `hz / (sr / 2)`.
pub fn butter_sos(order: usize, wn: f64, band: BandType) -> Vec<[f64; 6]> {
    assert!(order >= 1, "filter order must be >= 1");
    assert!(wn > 0.0 && wn < 1.0, "cutoff must be inside (0, Nyquist)");

    // scipy pre-warps against a nominal fs = 2, so warped = 4·tan(π·wn/2).
    let warped = 4.0 * (std::f64::consts::PI * wn / 2.0).tan();

    let proto = buttap(order);
    let (poles_a, gain_a) = match band {
        BandType::Low => {
            let p: Vec<Complex64> = proto.iter().map(|p| p * warped).collect();
            (p, warped.powi(order as i32))
        }
        BandType::High => {
            let p: Vec<Complex64> = proto.iter().map(|p| warped / p).collect();
            let denom: Complex64 = proto.iter().map(|p| -p).product();
            (p, (1.0 / denom).re)
        }
    };

    // Bilinear transform at fs = 2 (so fs2 = 4). Low-pass has no analog
    // zeros, high-pass has `order` zeros at the origin.
    let fs2 = 4.0;
    let zeros_a: Vec<Complex64> = match band {
        BandType::Low => Vec::new(),
        BandType::High => vec![Complex64::new(0.0, 0.0); order],
    };
    let poles_z: Vec<Complex64> = poles_a
        .iter()
        .map(|p| (Complex64::new(fs2, 0.0) + p) / (Complex64::new(fs2, 0.0) - p))
        .collect();
    let mut zeros_z: Vec<Complex64> = zeros_a
        .iter()
        .map(|z| (Complex64::new(fs2, 0.0) + z) / (Complex64::new(fs2, 0.0) - z))
        .collect();
    let num_z: Complex64 = zeros_a.iter().map(|z| Complex64::new(fs2, 0.0) - z).product();
    let den_z: Complex64 = poles_a.iter().map(|p| Complex64::new(fs2, 0.0) - p).product();
    let gain_z = gain_a * (num_z / den_z).re;
    zeros_z.resize(order, Complex64::new(-1.0, 0.0));

    zpk2sos_real_zeros(&poles_z, &zeros_z, gain_z)
}

fn is_real(v: &Complex64) -> bool {
    v.im == 0.0
}

/// Monic polynomial with the given roots; the roots here are either real or
/// exact conjugate pairs, so the coefficients come out real.
fn poly(roots: &[Complex64]) -> Vec<f64> {
    let mut coeffs = vec![Complex64::new(1.0, 0.0)];
    for r in roots {
        let mut next = vec![Complex64::new(0.0, 0.0); coeffs.len() + 1];
        for (i, c) in coeffs.iter().enumerate() {
            next[i] += c;
            next[i + 1] -= c * r;
        }
        coeffs = next;
    }
    coeffs.iter().map(|c| c.re).collect()
}

/// `scipy.signal._filter_design._single_zpksos` — note that the numerator and
/// denominator are *right*-aligned inside the six-element row.
fn single_zpksos(zeros: &[Complex64], poles: &[Complex64]) -> [f64; 6] {
    let b = poly(zeros);
    let a = poly(poles);
    let mut row = [0.0; 6];
    row[3 - b.len()..3].copy_from_slice(&b);
    row[6 - a.len()..6].copy_from_slice(&a);
    row
}

/// Index of the pole "closest to the unit circle", SciPy's digital `idx_worst`.
fn idx_worst(poles: &[Complex64]) -> usize {
    poles
        .iter()
        .enumerate()
        .min_by(|(_, a), (_, b)| {
            (1.0 - a.norm())
                .abs()
                .partial_cmp(&(1.0 - b.norm()).abs())
                .expect("pole magnitudes are finite")
        })
        .map(|(i, _)| i)
        .expect("pole list is empty")
}

/// `_nearest_real_complex_idx(from, to, "any" | "real")` restricted to the
/// real-zero case this module produces.
fn nearest_zero_idx(zeros: &[Complex64], to: Complex64, real_only: bool) -> usize {
    let mut order: Vec<usize> = (0..zeros.len()).collect();
    order.sort_by(|&i, &j| {
        (zeros[i] - to)
            .norm()
            .partial_cmp(&(zeros[j] - to).norm())
            .expect("zero distances are finite")
    });
    match real_only {
        false => order[0],
        true => *order
            .iter()
            .find(|&&i| is_real(&zeros[i]))
            .expect("no real zero remains"),
    }
}

/// `zpk2sos(..., pairing="nearest")` for systems whose zeros are all real —
/// every low/high-pass Butterworth after the bilinear map.
fn zpk2sos_real_zeros(poles: &[Complex64], zeros: &[Complex64], gain: f64) -> Vec<[f64; 6]> {
    let mut p = poles.to_vec();
    let mut z = zeros.to_vec();
    let n_sections = (p.len().max(z.len()) + 1) / 2;
    if p.len() % 2 == 1 {
        p.push(Complex64::new(0.0, 0.0));
        z.push(Complex64::new(0.0, 0.0));
    }

    let mut sos = vec![[0.0; 6]; n_sections];
    for si in (0..n_sections).rev() {
        let p1 = p.remove(idx_worst(&p));

        if is_real(&p1) && !p.iter().any(is_real) {
            let z1 = z.remove(nearest_zero_idx(&z, p1, true));
            sos[si] = single_zpksos(&[z1, Complex64::new(0.0, 0.0)],
                                    &[p1, Complex64::new(0.0, 0.0)]);
            continue;
        }

        let p2 = if is_real(&p1) {
            let real_idx: Vec<usize> = (0..p.len()).filter(|&i| is_real(&p[i])).collect();
            let reals: Vec<Complex64> = real_idx.iter().map(|&i| p[i]).collect();
            p.remove(real_idx[idx_worst(&reals)])
        } else {
            let jdx = p
                .iter()
                .position(|q| *q == p1.conj())
                .expect("complex pole without its conjugate");
            p.remove(jdx)
        };

        if z.is_empty() {
            sos[si] = single_zpksos(&[], &[p1, p2]);
        } else {
            let z1 = z.remove(nearest_zero_idx(&z, p1, false));
            if !is_real(&z1) {
                sos[si] = single_zpksos(&[z1, z1.conj()], &[p1, p2]);
            } else if !z.is_empty() {
                let z2 = z.remove(nearest_zero_idx(&z, p1, true));
                sos[si] = single_zpksos(&[z1, z2], &[p1, p2]);
            } else {
                sos[si] = single_zpksos(&[z1], &[p1, p2]);
            }
        }
    }

    for c in sos[0].iter_mut().take(3) {
        *c *= gain;
    }
    sos
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernels::biquad::Sos;

    #[test]
    fn lowpass_passes_dc_at_unity() {
        for order in [1usize, 2, 4] {
            let sos = butter_sos(order, 0.1, BandType::Low);
            let gain: f64 = sos.iter().map(|r| Sos::new(*r).dc_gain()).product();
            assert!((gain - 1.0).abs() < 1e-12, "order {order} DC gain {gain}");
        }
    }

    #[test]
    fn highpass_blocks_dc() {
        for order in [1usize, 2, 4] {
            let sos = butter_sos(order, 0.1, BandType::High);
            let gain: f64 = sos.iter().map(|r| Sos::new(*r).dc_gain()).product();
            assert!(gain.abs() < 1e-12, "order {order} DC gain {gain}");
        }
    }
}
