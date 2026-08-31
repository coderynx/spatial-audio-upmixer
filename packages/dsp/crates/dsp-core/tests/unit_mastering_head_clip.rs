//! The chain's two ends: the subsonic/DC head and the pre-limiter clipper.
//!
//! Both are judged by what they buy the limiter — recovered headroom on the
//! head, a shorter gain-reduction duty on the clipper — rather than by a
//! stored waveform, since neither has a Python original to be pinned against.

use std::f64::consts::PI;

use upmixer_dsp_core::loudness::{measure_loudness_stats, measure_true_peak, true_peak_channel};
use upmixer_dsp_core::mastering::clip::{soft_clip, ClipCurve, ClipParams};
use upmixer_dsp_core::mastering::head::{chain_head, HeadParams};
use upmixer_dsp_core::mastering::limiter::{lookahead_limit, LimiterParams};

const SR: u32 = 48_000;

fn tone(freq: f64, n: usize, amplitude: f64) -> Vec<f64> {
    (0..n)
        .map(|i| amplitude * (2.0 * PI * freq * i as f64 / SR as f64).sin())
        .collect()
}

fn mix(a: &[f64], b: &[f64]) -> Vec<f64> {
    a.iter().zip(b.iter()).map(|(x, y)| x + y).collect()
}

/// Mean and RMS past the filters' own settling time.
fn mean(x: &[f64]) -> f64 {
    x[SR as usize / 2..].iter().sum::<f64>() / (x.len() - SR as usize / 2) as f64
}

fn rms(x: &[f64]) -> f64 {
    let tail = &x[SR as usize / 2..];
    (tail.iter().map(|v| v * v).sum::<f64>() / tail.len() as f64).sqrt()
}

/// Amplitude at one frequency, correlated over `signal`. Every fixture below
/// analyses a whole number of 5 Hz periods, so each frequency of interest —
/// harmonic or folded — lands on an exact bin.
fn bin_level(signal: &[f64], freq: f64) -> f64 {
    let (mut re, mut im) = (0.0, 0.0);
    for (i, v) in signal.iter().enumerate() {
        let phase = 2.0 * PI * freq * i as f64 / SR as f64;
        re += v * phase.cos();
        im += v * phase.sin();
    }
    2.0 * (re * re + im * im).sqrt() / signal.len() as f64
}

fn limiter() -> LimiterParams {
    LimiterParams {
        ceiling_dbtp: -1.0,
        lookahead_ms: 5.0,
        release_ms: 50.0,
        safety_margin_db: 0.1,
    }
}

mod head {
    use super::*;

    fn params() -> HeadParams {
        HeadParams { cutoff_hz: 20.0 }
    }

    #[test]
    fn dc_offset_goes_from_every_channel_including_lfe() {
        let n = SR as usize;
        let offset: Vec<f64> = tone(200.0, n, 0.3).iter().map(|v| v + 0.25).collect();
        let mut bed = vec![offset.clone(), offset.clone()];
        chain_head(&mut bed, Some(1), SR, &params());
        assert!(mean(&bed[0]).abs() < 1e-3, "mains DC {}", mean(&bed[0]));
        assert!(mean(&bed[1]).abs() < 1e-3, "LFE DC {}", mean(&bed[1]));
        assert!(mean(&offset) > 0.24, "the fixture should carry an offset");
    }

    /// Past the filter's settling time, a whole number of 5 Hz periods.
    const WINDOW: std::ops::Range<usize> = 24_000..33_600;

    #[test]
    fn the_audible_band_survives_and_rumble_does_not() {
        let n = 33_600;
        let music = tone(1000.0, n, 0.4);
        let rumble = tone(15.0, n, 0.4);
        let mut bed = vec![mix(&music, &rumble)];
        chain_head(&mut bed, None, SR, &params());
        let out = &bed[0][WINDOW];
        // 1 kHz is two decades above the corner and passes untouched; 15 Hz
        // sits below it and comes back at 12 dB/oct.
        assert!(
            (bin_level(out, 1000.0) - 0.4).abs() < 4e-3,
            "{}",
            bin_level(out, 1000.0)
        );
        assert!(
            bin_level(out, 15.0) < 0.6 * 0.4,
            "rumble left: {}",
            bin_level(out, 15.0)
        );
    }

    #[test]
    fn lfe_keeps_its_sub_content_where_the_mains_lose_it() {
        let n = 33_600;
        let sub = tone(10.0, n, 0.5);
        let mut bed = vec![sub.clone(), sub.clone()];
        chain_head(&mut bed, Some(1), SR, &params());
        assert!(bin_level(&bed[1][WINDOW], 10.0) > 0.85 * 0.5, "LFE 10 Hz");
        assert!(bin_level(&bed[0][WINDOW], 10.0) < 0.3 * 0.5, "mains 10 Hz");
    }

    #[test]
    fn removing_rumble_gives_the_limiter_back_its_headroom() {
        let n = 4 * SR as usize;
        let programme = mix(&tone(440.0, n, 0.75), &tone(15.0, n, 0.5));

        let mut with_rumble = vec![programme.clone()];
        let unfiltered = lookahead_limit(&mut with_rumble, None, SR, &limiter());

        let mut filtered = vec![programme.clone()];
        chain_head(&mut filtered, None, SR, &params());
        let head_first = lookahead_limit(&mut filtered, None, SR, &limiter());

        assert!(
            head_first.max_gr_db < unfiltered.max_gr_db - 1.0,
            "GR peak {} vs {}",
            head_first.max_gr_db,
            unfiltered.max_gr_db
        );
        assert!(
            head_first.duty < unfiltered.duty,
            "duty {} vs {}",
            head_first.duty,
            unfiltered.duty
        );
        // The headroom is real, not just quieter: the audible tone comes
        // through the head stage at the level it went in.
        assert!(rms(&filtered[0]) > rms(&with_rumble[0]));
    }
}

mod clip {
    use super::*;

    fn params() -> ClipParams {
        ClipParams {
            ceiling_dbtp: -1.0,
            clip_db: 1.0,
            knee: 1.0,
        }
    }

    fn ceiling(p: &ClipParams) -> f64 {
        10.0_f64.powf(p.ceiling_dbtp / 20.0)
    }

    #[test]
    fn below_the_knee_the_curve_is_the_identity() {
        let curve = ClipCurve::new(&params());
        let threshold = ceiling(&params()) * 10.0_f64.powf(-1.0 / 20.0);
        for x in [-threshold, -0.3, 0.0, 0.5, threshold] {
            assert!((curve.shape(x) - x).abs() < 1e-15, "{x}");
        }
    }

    #[test]
    fn nothing_leaves_the_curve_above_the_ceiling() {
        for knee in [0.0, 0.5, 1.0] {
            let p = ClipParams { knee, ..params() };
            let curve = ClipCurve::new(&p);
            for i in 0..400 {
                let x = i as f64 * 0.05;
                assert!(
                    curve.shape(x).abs() <= ceiling(&p) + 1e-12,
                    "knee {knee} at {x}"
                );
                assert!(
                    (curve.shape(-x) + curve.shape(x)).abs() < 1e-15,
                    "odd symmetry"
                );
            }
        }
    }

    #[test]
    fn the_full_knee_curve_has_no_corner() {
        let curve = ClipCurve::new(&params());
        let threshold = ceiling(&params()) * 10.0_f64.powf(-1.0 / 20.0);
        let h = 1e-6;
        let below = (curve.shape(threshold) - curve.shape(threshold - h)) / h;
        let above = (curve.shape(threshold + h) - curve.shape(threshold)) / h;
        assert!((below - 1.0).abs() < 1e-4, "slope below: {below}");
        assert!((above - 1.0).abs() < 1e-4, "slope above: {above}");
    }

    #[test]
    fn lfe_is_left_alone() {
        let hot = tone(60.0, 4800, 1.4);
        let mut bed = vec![hot.clone(), hot.clone()];
        soft_clip(&mut bed, Some(1), &params());
        assert_eq!(bed[1], hot);
        assert!(true_peak_channel(&bed[0]) < true_peak_channel(&hot));
    }

    /// A transient train over a bed: the clipper takes the peaks, so the
    /// limiter that follows holds gain reduction over fewer samples and gives
    /// away less of the body to reach the same ceiling.
    #[test]
    fn shaving_transients_shortens_the_limiter_duty_and_keeps_the_loudness() {
        let n = 6 * SR as usize;
        let bed = tone(220.0, n, 0.25);
        let mut programme = bed.clone();
        for hit in 0..24 {
            let at = hit * SR as usize / 4;
            for i in 0..600 {
                let decay = (-(i as f64) / 60.0).exp();
                programme[at + i] += 1.1 * decay * (2.0 * PI * 1800.0 * i as f64 / SR as f64).sin();
            }
        }

        let psr = |channel: &[f64]| {
            let stats = measure_loudness_stats(&[(1.0, channel)], SR);
            measure_true_peak(&[channel]) - stats.max_short_term_lkfs
        };

        let mut limited = vec![programme.clone()];
        let alone = lookahead_limit(&mut limited, None, SR, &limiter());

        let mut clipped = vec![programme.clone()];
        soft_clip(&mut clipped, None, &params());
        let after_clip = lookahead_limit(&mut clipped, None, SR, &limiter());

        let short_term =
            |channel: &[f64]| measure_loudness_stats(&[(1.0, channel)], SR).max_short_term_lkfs;
        println!(
            "limiter alone: duty {:.3} GR {:.2} dB  short-term {:.2} LKFS  PSR {:.2} dB",
            alone.duty,
            alone.max_gr_db,
            short_term(&limited[0]),
            psr(&limited[0])
        );
        println!(
            "clip + limiter: duty {:.3} GR {:.2} dB  short-term {:.2} LKFS  PSR {:.2} dB",
            after_clip.duty,
            after_clip.max_gr_db,
            short_term(&clipped[0]),
            psr(&clipped[0])
        );

        assert!(
            after_clip.duty < 0.75 * alone.duty,
            "duty {} vs {}",
            after_clip.duty,
            alone.duty
        );
        assert!(
            after_clip.max_gr_db < alone.max_gr_db - 1.0,
            "GR peak {} vs {}",
            after_clip.max_gr_db,
            alone.max_gr_db
        );
        // Both sides are pinned at the same ceiling, so the loudness the
        // limiter no longer has to give away shows up here. PSR is scale
        // invariant and stays put — see the phase report.
        assert!(
            short_term(&clipped[0]) > short_term(&limited[0]),
            "short-term {} vs {}",
            short_term(&clipped[0]),
            short_term(&limited[0])
        );
    }

    /// Worst folded odd partial, relative to the fundamental, in dBc.
    fn aliasing_dbc(drive_db: f64) -> f64 {
        let f0 = 5310.0;
        let threshold = ceiling(&params()) * 10.0_f64.powf(-params().clip_db / 20.0);
        let mut bed = vec![tone(f0, 9600, threshold * 10.0_f64.powf(drive_db / 20.0))];
        soft_clip(&mut bed, None, &params());
        let fundamental = bin_level(&bed[0], f0);

        let nyquist = SR as f64 / 2.0;
        let mut worst: f64 = 0.0;
        for k in (3..60).step_by(2) {
            let raw = k as f64 * f0;
            if raw < nyquist {
                continue;
            }
            let folded = (raw - SR as f64 * (raw / SR as f64).round()).abs();
            worst = worst.max(bin_level(&bed[0], folded) / fundamental);
        }
        20.0 * worst.log10()
    }

    /// No oversampling in v1, so odd harmonics past Nyquist fold back. A
    /// 5.3 kHz sine driven into the knee is the worst case a clipper has, and
    /// the bound below is a regression guard on that worst case, not a
    /// claim of inaudibility — the measured curve is in the phase report.
    #[test]
    fn aliasing_from_the_fold_stays_below_the_programme() {
        for drive_db in [0.5, 1.0, 2.0, 3.0, 6.0] {
            println!(
                "drive +{drive_db} dB over the knee: {:.1} dBc",
                aliasing_dbc(drive_db)
            );
        }
        assert!(
            aliasing_dbc(0.5) < -55.0,
            "at +0.5 dB: {:.1} dBc",
            aliasing_dbc(0.5)
        );
        assert!(
            aliasing_dbc(6.0) < -28.0,
            "at +6 dB: {:.1} dBc",
            aliasing_dbc(6.0)
        );
    }
}
