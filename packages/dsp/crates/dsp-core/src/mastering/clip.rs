//! Pre-limiter soft clipper.
//!
//! One memoryless transfer curve, the same parameters on every non-LFE
//! channel — which is the only sense in which a nonlinearity can be linked.
//! It does not commute with the LF sum, which is why it sits after bass
//! management and directly before the limiter (see
//! `docs/contracts/preview_export_parity.md` §1).

#[derive(Clone, Copy, Debug, PartialEq, serde::Deserialize)]
pub struct ClipParams {
    /// The limiter's ceiling, in dBTP. The curve's asymptote.
    pub ceiling_dbtp: f64,
    /// How far below the ceiling the knee sits, in dB.
    pub clip_db: f64,
    /// 0.0 is a hard clip at the ceiling, 1.0 the full tanh curve.
    pub knee: f64,
}

/// The transfer curve, as `(threshold, ceiling, knee)` resolves it.
pub struct ClipCurve {
    threshold: f64,
    ceiling: f64,
    knee: f64,
}

impl ClipCurve {
    pub fn new(p: &ClipParams) -> Self {
        let ceiling = 10.0_f64.powf(p.ceiling_dbtp / 20.0);
        Self {
            threshold: ceiling * 10.0_f64.powf(-p.clip_db.max(0.0) / 20.0),
            ceiling,
            knee: p.knee.clamp(0.0, 1.0),
        }
    }

    /// Above the threshold, blend the hard clip toward a tanh whose slope at
    /// the threshold is exactly 1, so the full-knee curve has no corner.
    #[inline]
    pub fn shape(&self, x: f64) -> f64 {
        let level = x.abs();
        if level <= self.threshold {
            return x;
        }
        let span = self.ceiling - self.threshold;
        let hard = level.min(self.ceiling);
        if span <= 0.0 {
            return x.signum() * hard;
        }
        let soft = self.threshold + span * ((level - self.threshold) / span).tanh();
        x.signum() * (self.knee * soft + (1.0 - self.knee) * hard)
    }

    pub fn apply(&self, block: &mut [f64]) {
        for v in block.iter_mut() {
            *v = self.shape(*v);
        }
    }
}

/// Soft-clip every channel except LFE in place.
pub fn soft_clip(bed: &mut super::Bed, lfe: Option<usize>, p: &ClipParams) {
    let curve = ClipCurve::new(p);
    for i in super::non_lfe(bed.len(), lfe) {
        curve.apply(&mut bed[i]);
    }
}
