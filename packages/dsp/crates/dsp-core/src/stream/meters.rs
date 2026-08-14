//! Level metering for the mixer strips, channel meters, and headphone bus.
//!
//! Measured at the emit position rather than the render horizon, so a meter
//! never leads the audio the listener is hearing.

/// RMS and peak over one emitted block.
#[derive(Clone, Copy, Debug, Default)]
pub struct Level {
    pub rms: f64,
    pub peak: f64,
}

impl Level {
    pub fn measure(samples: &[f64]) -> Self {
        if samples.is_empty() {
            return Self::default();
        }
        let mut sum_sq = 0.0;
        let mut peak = 0.0_f64;
        for v in samples {
            sum_sq += v * v;
            peak = peak.max(v.abs());
        }
        Self { rms: (sum_sq / samples.len() as f64).sqrt(), peak }
    }

    pub fn measure_f32(samples: &[f32], gain: f64) -> Self {
        if samples.is_empty() {
            return Self::default();
        }
        let mut sum_sq = 0.0;
        let mut peak = 0.0_f64;
        for v in samples {
            let scaled = *v as f64 * gain;
            sum_sq += scaled * scaled;
            peak = peak.max(scaled.abs());
        }
        Self { rms: (sum_sq / samples.len() as f64).sqrt(), peak }
    }
}

/// Everything the UI meters, in one block the host reads after each render.
#[derive(Clone, Debug, Default)]
pub struct Meters {
    /// Per stem, post-gain and pre-routing — what a mixer strip shows.
    /// Left/right pair always: a mono source has identical channels (see
    /// `StemSource`'s decode), so the host slices to one bar itself from the
    /// project's own channel count rather than the core tracking mono/stereo.
    pub stems: Vec<[Level; 2]>,
    /// Per speaker of the mastered bed.
    pub channels: Vec<Level>,
    /// The collapsed pair, or the first two bed channels for native output.
    pub output: [Level; 2],
}

impl Meters {
    /// Flatten to `[rms, peak]` pairs: stems (left, right), then channels,
    /// then output.
    pub fn write(&self, out: &mut [f32]) -> usize {
        let mut i = 0;
        let push = |level: &Level, out: &mut [f32], i: &mut usize| {
            if *i + 1 < out.len() {
                out[*i] = level.rms as f32;
                out[*i + 1] = level.peak as f32;
            }
            *i += 2;
        };
        for pair in &self.stems {
            push(&pair[0], out, &mut i);
            push(&pair[1], out, &mut i);
        }
        for level in &self.channels {
            push(level, out, &mut i);
        }
        for level in &self.output {
            push(level, out, &mut i);
        }
        i
    }

    /// Number of floats [`write`] needs.
    pub fn len(&self) -> usize {
        2 * (self.stems.len() * 2 + self.channels.len() + 2)
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_full_scale_square_reads_unity_rms_and_peak() {
        let level = Level::measure(&[1.0, -1.0, 1.0, -1.0]);
        assert!((level.rms - 1.0).abs() < 1e-15);
        assert!((level.peak - 1.0).abs() < 1e-15);
    }

    #[test]
    fn gain_scales_the_f32_measurement() {
        let quiet = Level::measure_f32(&[0.5, -0.5], 1.0);
        let loud = Level::measure_f32(&[0.5, -0.5], 2.0);
        assert!((loud.rms - quiet.rms * 2.0).abs() < 1e-12);
        assert!((loud.peak - 1.0).abs() < 1e-12);
    }

    #[test]
    fn silence_reads_zero_rather_than_nan() {
        let level = Level::measure(&[]);
        assert_eq!(level.rms, 0.0);
        assert_eq!(level.peak, 0.0);
    }

    #[test]
    fn the_flat_block_is_stems_then_channels_then_output() {
        let meters = Meters {
            stems: vec![[Level { rms: 0.1, peak: 0.2 }, Level { rms: 0.15, peak: 0.25 }]],
            channels: vec![Level { rms: 0.3, peak: 0.4 }],
            output: [Level { rms: 0.5, peak: 0.6 }, Level { rms: 0.7, peak: 0.8 }],
        };
        let mut out = vec![0.0_f32; meters.len()];
        assert_eq!(meters.write(&mut out), 10);
        assert_eq!(out, vec![0.1, 0.2, 0.15, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]);
    }
}
