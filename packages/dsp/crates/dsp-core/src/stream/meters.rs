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
        Self {
            rms: (sum_sq / samples.len() as f64).sqrt(),
            peak,
        }
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
        Self {
            rms: (sum_sq / samples.len() as f64).sqrt(),
            peak,
        }
    }
}

/// The mastering readouts: loudness of the delivered programme and how hard
/// each dynamics stage is working, both over the same trailing window the
/// level meters use.
#[derive(Clone, Copy, Debug, Default)]
pub struct MasterMeters {
    /// EBU Tech 3341 momentary (400 ms) loudness, LKFS.
    pub momentary_lkfs: f64,
    /// EBU Tech 3341 short-term (3 s) loudness, LKFS.
    pub short_term_lkfs: f64,
    /// Deepest bus-compressor gain reduction, dB, positive downward.
    pub comp_gr_db: f64,
    /// Deepest limiter gain reduction on the mains curve, dB.
    pub limiter_gr_db: f64,
    /// Deepest limiter gain reduction on the LFE's own curve, dB.
    pub limiter_lfe_gr_db: f64,
}

/// Floats [`MasterMeters`] writes.
const MASTER_METER_FLOATS: usize = 5;

/// Everything the UI meters, in one block the host reads after each render.
#[derive(Clone, Debug, Default)]
pub struct Meters {
    /// Per stem, post-gain and pre-routing — what a mixer strip shows.
    /// Left/right pair always: a mono source has identical channels (see
    /// `StemSource`'s decode), so the host slices to one bar itself from the
    /// project's own channel count rather than the core tracking mono/stereo.
    pub stems: Vec<[Level; 2]>,
    /// One linked stem-dynamics reduction value per stem, positive downward.
    pub stem_dynamics_gr_db: Vec<f64>,
    /// One linked tame/dynamic-EQ reduction value per stem.
    pub stem_dynamic_eq_gr_db: Vec<f64>,
    /// Per speaker of the mastered bed.
    pub channels: Vec<Level>,
    /// The collapsed pair, or the first two bed channels for native output.
    pub output: [Level; 2],
    /// Loudness and gain-reduction readouts for the master strip.
    pub master: MasterMeters,
}

impl Meters {
    /// Flatten to `[rms, peak]` pairs: stems (left, right), stem reductions, then channels,
    /// then output, then [`MasterMeters`]'s own flat floats.
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
        for value in &self.stem_dynamics_gr_db {
            if i < out.len() {
                out[i] = *value as f32;
            }
            i += 1;
        }
        for level in &self.channels {
            push(level, out, &mut i);
        }
        for level in &self.output {
            push(level, out, &mut i);
        }
        let m = &self.master;
        for value in [
            m.momentary_lkfs,
            m.short_term_lkfs,
            m.comp_gr_db,
            m.limiter_gr_db,
            m.limiter_lfe_gr_db,
        ] {
            if i < out.len() {
                out[i] = value as f32;
            }
            i += 1;
        }
        for value in &self.stem_dynamic_eq_gr_db {
            if i < out.len() { out[i] = *value as f32; }
            i += 1;
        }
        i
    }

    /// Number of floats [`write`] needs.
    pub fn len(&self) -> usize {
        2 * (self.stems.len() * 2 + self.channels.len() + 2)
            + self.stem_dynamics_gr_db.len()
            + self.stem_dynamic_eq_gr_db.len()
            + MASTER_METER_FLOATS
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }
}
