//! Second-order-section filtering matching `scipy.signal.sosfilt` /
//! `lfilter` (transposed direct form II) and `sosfilt_zi`.

/// One second-order section, `[b0, b1, b2, a0, a1, a2]` with `a0` normalized
/// to 1, plus its two delay registers so a caller can stream across blocks.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Sos {
    pub b: [f64; 3],
    pub a: [f64; 3],
    pub z: [f64; 2],
}

impl Sos {
    pub fn new(coeffs: [f64; 6]) -> Self {
        let a0 = coeffs[3];
        Self {
            b: [coeffs[0] / a0, coeffs[1] / a0, coeffs[2] / a0],
            a: [1.0, coeffs[4] / a0, coeffs[5] / a0],
            z: [0.0, 0.0],
        }
    }

    pub fn reset(&mut self) {
        self.z = [0.0, 0.0];
    }

    #[inline]
    pub fn tick(&mut self, x: f64) -> f64 {
        let y = self.b[0] * x + self.z[0];
        self.z[0] = self.b[1] * x - self.a[1] * y + self.z[1];
        self.z[1] = self.b[2] * x - self.a[2] * y;
        y
    }

    /// Steady-state step-response delay registers, the biquad closed form of
    /// `scipy.signal.lfilter_zi`.
    pub fn zi(&self) -> [f64; 2] {
        let (b0, b1, b2) = (self.b[0], self.b[1], self.b[2]);
        let (a1, a2) = (self.a[1], self.a[2]);
        let bb = [b1 - a1 * b0, b2 - a2 * b0];
        let det = 1.0 + a1 + a2;
        [
            (bb[0] + bb[1]) / det,
            ((1.0 + a1) * bb[1] - a2 * bb[0]) / det,
        ]
    }

    pub fn dc_gain(&self) -> f64 {
        (self.b[0] + self.b[1] + self.b[2]) / (self.a[0] + self.a[1] + self.a[2])
    }
}

/// Cascade of sections; owns the streaming state for every section.
#[derive(Clone, Debug)]
pub struct SosFilter {
    pub sections: Vec<Sos>,
}

impl SosFilter {
    pub fn new(sections: Vec<Sos>) -> Self {
        Self { sections }
    }

    pub fn from_flat(rows: &[[f64; 6]]) -> Self {
        Self::new(rows.iter().map(|r| Sos::new(*r)).collect())
    }

    pub fn reset(&mut self) {
        for s in &mut self.sections {
            s.reset();
        }
    }

    /// Replace the coefficients in place, keeping each section's delay
    /// registers — a live parameter edit hears the new response starting at
    /// the next sample rather than a cold-filter transient. Falls back to a
    /// fresh build when the section count itself changes (a filter order
    /// edit), which resets state same as [`Self::from_flat`] would.
    pub fn retune_flat(&mut self, rows: &[[f64; 6]]) {
        if rows.len() != self.sections.len() {
            *self = Self::from_flat(rows);
            return;
        }
        for (section, row) in self.sections.iter_mut().zip(rows.iter()) {
            let a0 = row[3];
            section.b = [row[0] / a0, row[1] / a0, row[2] / a0];
            section.a = [1.0, row[4] / a0, row[5] / a0];
        }
    }

    /// Seed every section's registers with the `sosfilt_zi` steady state
    /// scaled by `x0`, matching how `sosfiltfilt` initializes its passes.
    pub fn set_step_state(&mut self, x0: f64) {
        let mut scale = 1.0;
        for s in &mut self.sections {
            let zi = s.zi();
            let gain = s.dc_gain();
            s.z = [zi[0] * scale * x0, zi[1] * scale * x0];
            scale *= gain;
        }
    }

    #[inline]
    pub fn tick(&mut self, x: f64) -> f64 {
        let mut y = x;
        for s in &mut self.sections {
            y = s.tick(y);
        }
        y
    }

    pub fn process(&mut self, signal: &mut [f64]) {
        for v in signal.iter_mut() {
            *v = self.tick(*v);
        }
    }
}

/// `scipy.signal.sosfilt` on a fresh (zero-state) filter.
pub fn sosfilt(sections: &[[f64; 6]], signal: &[f64]) -> Vec<f64> {
    let mut f = SosFilter::from_flat(sections);
    let mut out = signal.to_vec();
    f.process(&mut out);
    out
}

/// `scipy.signal.sosfilt_zi` — per-section steady-state registers, each
/// scaled by the cumulative DC gain of the sections ahead of it.
pub fn sosfilt_zi(sections: &[[f64; 6]]) -> Vec<[f64; 2]> {
    let mut scale = 1.0;
    let mut out = Vec::with_capacity(sections.len());
    for row in sections {
        let s = Sos::new(*row);
        let zi = s.zi();
        out.push([zi[0] * scale, zi[1] * scale]);
        scale *= s.dc_gain();
    }
    out
}

/// `scipy.signal.lfilter` for arbitrary `b`/`a` (transposed direct form II).
pub fn lfilter(b: &[f64], a: &[f64], signal: &[f64]) -> Vec<f64> {
    let n = b.len().max(a.len());
    let a0 = a[0];
    let mut bb = vec![0.0; n];
    let mut aa = vec![0.0; n];
    for (i, v) in b.iter().enumerate() {
        bb[i] = v / a0;
    }
    for (i, v) in a.iter().enumerate() {
        aa[i] = v / a0;
    }

    let mut z = vec![0.0; n.saturating_sub(1)];
    let mut out = Vec::with_capacity(signal.len());
    for &x in signal {
        let y = bb[0] * x + z.first().copied().unwrap_or(0.0);
        for i in 0..z.len() {
            let next = z.get(i + 1).copied().unwrap_or(0.0);
            z[i] = bb[i + 1] * x - aa[i + 1] * y + next;
        }
        out.push(y);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn one_pole_lfilter_matches_closed_form() {
        let alpha = 0.25;
        let b = [alpha];
        let a = [1.0, -(1.0 - alpha)];
        let x = [1.0, 1.0, 1.0, 1.0];
        let y = lfilter(&b, &a, &x);
        let mut expect = 0.0;
        for (i, v) in y.iter().enumerate() {
            expect = alpha * 1.0 + (1.0 - alpha) * expect;
            assert!((v - expect).abs() < 1e-15, "sample {i}");
        }
    }

    #[test]
    fn zi_holds_dc_steady_state() {
        // A filter seeded with its own step state must pass DC unchanged.
        let sos = [[0.2, 0.4, 0.2, 1.0, -0.3, 0.1]];
        let mut f = SosFilter::from_flat(&sos);
        f.set_step_state(1.0);
        let mut sig = vec![1.0; 8];
        f.process(&mut sig);
        let gain = Sos::new(sos[0]).dc_gain();
        for v in sig {
            assert!((v - gain).abs() < 1e-12);
        }
    }
}
