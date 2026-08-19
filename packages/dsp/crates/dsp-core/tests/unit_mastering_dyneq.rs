//! The linked dynamic EQ.
//!
//! Two things are being established here. The easy one is that the bands do
//! what a dynamic EQ is supposed to do — act only above their threshold, only
//! on their own frequency, and identically on every channel. The hard one is
//! the case that closed mixing phase 13: a broadband event whose bands decay
//! at different rates, where independent detectors morph the timbre while it
//! rings. `crash` below is that fixture, and its rejection criteria are stated
//! with it.

use std::f64::consts::PI;

use upmixer_dsp_core::kernels::biquad::{bandpass_sos, peaking_sos, sosfilt};
use upmixer_dsp_core::mastering::dyneq::{dynamic_eq, BandParams};

const SR: u32 = 48_000;

fn tone(freq: f64, n: usize, amplitude: f64) -> Vec<f64> {
    (0..n)
        .map(|i| amplitude * (2.0 * PI * freq * i as f64 / SR as f64).sin())
        .collect()
}

fn band() -> BandParams {
    BandParams {
        freq_hz: 3800.0,
        q: 2.0,
        threshold_db: -30.0,
        ratio: 4.0,
        attack_ms: 10.0,
        release_ms: 150.0,
    }
}

/// RMS of `signal` through a band-pass at `freq`.
fn band_rms(signal: &[f64], freq: f64, q: f64) -> f64 {
    let filtered = sosfilt(&[bandpass_sos(freq / (SR as f64 / 2.0), q)], signal);
    (filtered.iter().map(|v| v * v).sum::<f64>() / filtered.len() as f64).sqrt()
}

fn db(ratio: f64) -> f64 {
    20.0 * ratio.max(1e-30).log10()
}

#[test]
fn a_band_below_its_threshold_returns_the_input_sample_for_sample() {
    // The bell's design at unity gain is exactly `b == a`, so the section is
    // the identity rather than approximately transparent.
    let quiet = tone(3800.0, SR as usize, 0.001);
    let mut bed = vec![quiet.clone(), quiet.clone()];
    let cuts = dynamic_eq(&mut bed, None, SR, &[band()]);
    assert_eq!(bed[0], quiet, "an inert band must not touch the samples");
    assert_eq!(bed[1], quiet);
    assert_eq!(cuts, vec![0.0], "nothing should have been cut");
}

#[test]
fn a_ratio_of_one_leaves_the_stage_absent() {
    let hot = tone(3800.0, SR as usize, 0.5);
    let mut bed = vec![hot.clone()];
    let cuts = dynamic_eq(&mut bed, None, SR, &[BandParams { ratio: 1.0, ..band() }]);
    assert_eq!(bed[0], hot);
    assert!(cuts.is_empty(), "an inert band should not even be built");
}

#[test]
fn a_triggered_band_cuts_its_own_frequency_and_leaves_the_rest() {
    let n = 2 * SR as usize;
    let programme: Vec<f64> = tone(3800.0, n, 0.5)
        .iter()
        .zip(tone(300.0, n, 0.5).iter())
        .map(|(a, b)| a + b)
        .collect();

    let mut bed = vec![programme.clone()];
    let cuts = dynamic_eq(&mut bed, None, SR, &[band()]);

    let in_band = db(band_rms(&bed[0], 3800.0, 4.0) / band_rms(&programme, 3800.0, 4.0));
    let out_of_band = db(band_rms(&bed[0], 300.0, 4.0) / band_rms(&programme, 300.0, 4.0));
    println!("in band {in_band:.2} dB, an octave and a half down {out_of_band:.2} dB");
    assert!(in_band < -3.0, "the band should have been cut: {in_band} dB");
    assert!(out_of_band.abs() < 0.2, "300 Hz moved {out_of_band} dB");
    assert!(cuts[0] > 3.0, "reported cut {} dB", cuts[0]);
}

#[test]
fn a_deeper_ratio_cuts_harder() {
    let hot = tone(3800.0, SR as usize, 0.5);
    let depth = |ratio: f64| {
        let mut bed = vec![hot.clone()];
        dynamic_eq(&mut bed, None, SR, &[BandParams { ratio, ..band() }])[0]
    };
    let (gentle, hard) = (depth(2.0), depth(8.0));
    println!("ratio 2: {gentle:.2} dB, ratio 8: {hard:.2} dB");
    assert!(hard > gentle + 3.0, "{hard} vs {gentle}");
}

#[test]
fn attack_sets_how_fast_the_cut_arrives() {
    // A step into the band: the fast attack has to be further into its cut
    // than the slow one a short way past the onset.
    let n = SR as usize / 2;
    let step: Vec<f64> = tone(3800.0, n, 0.5)
        .iter()
        .enumerate()
        .map(|(i, v)| if i < n / 2 { v * 0.002 } else { *v })
        .collect();
    let at_onset = |attack_ms: f64| {
        let mut bed = vec![step.clone()];
        dynamic_eq(&mut bed, None, SR, &[BandParams { attack_ms, ..band() }]);
        let window = n / 2..n / 2 + SR as usize / 200;
        band_rms(&bed[0][window.clone()].to_vec(), 3800.0, 4.0)
    };
    let (fast, slow) = (at_onset(1.0), at_onset(200.0));
    println!("5 ms after the step: fast {:.4}, slow {:.4}", fast, slow);
    assert!(fast < slow * 0.9, "a 1 ms attack should be ahead of a 200 ms one");
}

#[test]
fn every_channel_rides_the_same_curve() {
    // Two channels carrying the same programme at different levels: one
    // shared time-varying filter leaves their ratio exactly where it was,
    // whatever the detector did. This is the imaging argument, as a test.
    let n = SR as usize;
    let programme = tone(3800.0, n, 0.5);
    let scaled: Vec<f64> = programme.iter().map(|v| v * 0.37).collect();
    let mut bed = vec![programme, scaled];
    dynamic_eq(&mut bed, None, SR, &[band()]);
    for i in 0..n {
        assert!(
            (bed[1][i] - bed[0][i] * 0.37).abs() < 1e-15,
            "channel ratio moved at sample {i}: {} vs {}",
            bed[1][i],
            bed[0][i] * 0.37
        );
    }
}

#[test]
fn lfe_is_left_alone_and_stays_out_of_the_detector() {
    let n = SR as usize;
    let hot = tone(3800.0, n, 0.6);
    let quiet = tone(3800.0, n, 0.002);

    let mut with_lfe = vec![quiet.clone(), hot.clone()];
    let cuts = dynamic_eq(&mut with_lfe, Some(1), SR, &[band()]);
    assert_eq!(with_lfe[1], hot, "LFE must come back untouched");
    assert_eq!(cuts, vec![0.0], "a loud LFE must not drive the mains' band");
    assert_eq!(with_lfe[0], quiet);
}

/// A broadband strike whose bands decay at different rates — the shape that
/// broke mixing phase 13, where three independent detectors followed three
/// decays and the timbre morphed through the tail. Synthetic: partials on an
/// irregular grid, each with a decay constant that shortens with frequency,
/// the way a real cymbal's does.
fn crash(n: usize) -> Vec<f64> {
    let mut out = vec![0.0; n];
    for k in 0..64 {
        let freq = 300.0 * 1.065_f64.powi(k);
        if freq > 16_000.0 {
            break;
        }
        // Hotter around the band under test, so the strike actually triggers.
        let weight = if (2_600.0..5_200.0).contains(&freq) { 1.8 } else { 0.7 };
        let amplitude = weight / (1.0 + freq / 900.0).sqrt();
        let tau = SR as f64 * 1.2 / (1.0 + freq / 400.0);
        let phase = (k as f64 * 2.399_963_2) % (2.0 * PI);
        for (i, v) in out.iter_mut().enumerate() {
            *v += amplitude
                * (-(i as f64) / tau).exp()
                * (2.0 * PI * freq * i as f64 / SR as f64 + phase).sin();
        }
    }
    let peak = out.iter().fold(0.0_f64, |m, v| m.max(v.abs()));
    out.iter().map(|v| v * 0.7 / peak).collect()
}

/// Spectral tilt over time: high-band level over low-band level, per window.
fn tilt_trajectory(signal: &[f64]) -> Vec<f64> {
    let nyq = SR as f64 / 2.0;
    let high = sosfilt(&[bandpass_sos(3800.0 / nyq, 1.0)], signal);
    let low = sosfilt(&[bandpass_sos(600.0 / nyq, 1.0)], signal);
    let window = 1024;
    (0..signal.len() / window)
        .map(|w| {
            let range = w * window..(w + 1) * window;
            let energy = |x: &[f64]| {
                (x[range.clone()].iter().map(|v| v * v).sum::<f64>() / window as f64).sqrt()
            };
            db(energy(&high) / energy(&low).max(1e-12))
        })
        .collect()
}

fn std_dev(values: &[f64]) -> f64 {
    let mean = values.iter().sum::<f64>() / values.len() as f64;
    (values.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / values.len() as f64).sqrt()
}

/// The phase-13 gate, restated for a single bell.
///
/// A dynamic band is *supposed* to move the tilt — that is the cut. What it
/// must not do is move it more than the cut it applied, or move anything
/// outside its own band, which is what a diverging multiband split does. Both
/// are asserted; the comparison against a static dip of the same average
/// depth is printed for the phase report.
#[test]
fn a_decaying_broadband_strike_is_not_retimbred() {
    let n = 2 * SR as usize;
    let strike = crash(n);

    let mut dynamic = vec![strike.clone()];
    let cuts = dynamic_eq(&mut dynamic, None, SR, &[band()]);

    // The static dip of equal average depth: the same total attenuation the
    // dynamic band applied over the band, held constant.
    let average_cut = db(band_rms(&dynamic[0], 3800.0, 2.0) / band_rms(&strike, 3800.0, 2.0));
    let statik = sosfilt(
        &[peaking_sos(3800.0 / (SR as f64 / 2.0), 2.0, 10.0_f64.powf(average_cut / 20.0))],
        &strike,
    );

    let reference = tilt_trajectory(&strike);
    let error = |processed: &[f64]| -> Vec<f64> {
        tilt_trajectory(processed)
            .iter()
            .zip(reference.iter())
            .map(|(a, b)| a - b)
            .collect()
    };
    let dynamic_swing = std_dev(&error(&dynamic[0]));
    let static_swing = std_dev(&error(&statik));

    let out_of_band = db(band_rms(&dynamic[0], 600.0, 2.0) / band_rms(&strike, 600.0, 2.0));
    println!(
        "peak cut {:.2} dB, average {:.2} dB\n\
         tilt swing: dynamic {:.2} dB, static dip {:.2} dB\n\
         600 Hz moved {:.3} dB",
        cuts[0], average_cut, dynamic_swing, static_swing, out_of_band
    );

    assert!(cuts[0] > 2.0, "the strike should trigger the band: {} dB", cuts[0]);
    // The stage cannot move the timbre by more than the cut it applied.
    assert!(
        dynamic_swing < cuts[0],
        "tilt swing {dynamic_swing} dB exceeds the {} dB cut that caused it",
        cuts[0]
    );
    // And it cannot move what it never targeted — the phase-13 defect.
    assert!(out_of_band.abs() < 0.25, "600 Hz moved {out_of_band} dB");
}
