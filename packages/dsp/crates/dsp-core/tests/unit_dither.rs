//! The export tail's quantizer. There is no Python original to pin against —
//! the current writer hands float64 straight to libsndfile — so these are
//! statistical goldens: the code lattice, the error's RMS against the
//! round-to-nearest ideal, its DC term, and whether a tone below the LSB
//! survives.

use std::f64::consts::PI;

use upmixer_dsp_core::dither::{channel_seed, quantize, DitherMode};

const SR: f64 = 48_000.0;
const N: usize = 48_000;

fn tone(freq: f64, amplitude: f64) -> Vec<f64> {
    (0..N)
        .map(|i| amplitude * (2.0 * PI * freq * i as f64 / SR).sin())
        .collect()
}

fn lsb(bits: u32) -> f64 {
    2.0_f64.powi(-(bits as i32 - 1))
}

fn error(input: &[f64], output: &[f64]) -> Vec<f64> {
    input.iter().zip(output).map(|(x, y)| y - x).collect()
}

fn rms(x: &[f64]) -> f64 {
    (x.iter().map(|v| v * v).sum::<f64>() / x.len() as f64).sqrt()
}

fn mean(x: &[f64]) -> f64 {
    x.iter().sum::<f64>() / x.len() as f64
}

/// Amplitude of `signal` at `freq`, by correlation.
fn bin_level(signal: &[f64], freq: f64) -> f64 {
    let (mut re, mut im) = (0.0, 0.0);
    for (i, v) in signal.iter().enumerate() {
        let phase = 2.0 * PI * freq * i as f64 / SR;
        re += v * phase.cos();
        im += v * phase.sin();
    }
    2.0 * (re * re + im * im).sqrt() / signal.len() as f64
}

/// RMS of a 16-sample moving average — the error's low-frequency content,
/// which is what noise shaping trades away.
fn low_band_rms(x: &[f64]) -> f64 {
    let window = 16;
    let averaged: Vec<f64> = x
        .windows(window)
        .map(|w| w.iter().sum::<f64>() / window as f64)
        .collect();
    rms(&averaged)
}

fn run(input: &[f64], bits: u32, mode: DitherMode) -> Vec<f64> {
    let mut out = input.to_vec();
    quantize(&mut out, bits, mode, channel_seed(20_260_819, 0));
    out
}

#[test]
fn every_output_sample_lands_on_the_code_lattice() {
    for bits in [16_u32, 24, 32] {
        let step = lsb(bits);
        for mode in [DitherMode::Off, DitherMode::Tpdf, DitherMode::Shaped] {
            for v in run(&tone(997.0, 0.5), bits, mode) {
                let code = v / step;
                assert!(
                    (code - code.round()).abs() < 1e-9,
                    "{bits}-bit {mode:?} left {v} off the lattice"
                );
            }
        }
    }
}

#[test]
fn rounding_alone_carries_no_dc_and_the_ideal_error_power() {
    let input = tone(997.0, 0.5);
    let step = lsb(16);
    let err = error(&input, &run(&input, 16, DitherMode::Off));
    let ideal = step / 12.0_f64.sqrt();
    assert!(
        (rms(&err) / ideal - 1.0).abs() < 0.05,
        "{}",
        rms(&err) / ideal
    );
    assert!((mean(&err) / step).abs() < 0.01, "{}", mean(&err) / step);
}

#[test]
fn tpdf_error_is_the_root_three_ideal_with_no_dc_term() {
    let input = tone(997.0, 0.5);
    let step = lsb(16);
    let err = error(&input, &run(&input, 16, DitherMode::Tpdf));
    // Non-subtractive TPDF: the dither's own variance (2/12) adds to the
    // quantizer's (1/12), so the total is √3 of round-to-nearest, not √2.
    let ratio = rms(&err) / (step / 12.0_f64.sqrt());
    assert!((ratio - 3.0_f64.sqrt()).abs() < 0.05, "ratio {ratio}");
    assert!((mean(&err) / step).abs() < 0.01, "{}", mean(&err) / step);
    assert!(
        err.iter().all(|e| (e / step).abs() <= 1.5 + 1e-9),
        "TPDF error exceeded 1.5 LSB"
    );
}

/// The acceptance fixture: a tone a full 20 dB under the 16-bit LSB. Rounding
/// deletes it outright; dither carries it through as a modulated noise floor.
#[test]
fn a_tone_below_the_lsb_survives_dither_and_not_rounding() {
    let amplitude = lsb(16) * 0.1;
    let input = tone(997.0, amplitude);

    let rounded = run(&input, 16, DitherMode::Off);
    assert!(rounded.iter().all(|v| *v == 0.0), "rounding kept something");

    let dithered = run(&input, 16, DitherMode::Tpdf);
    let recovered = bin_level(&dithered, 997.0);
    assert!(
        (recovered / amplitude - 1.0).abs() < 0.25,
        "recovered {recovered:e} vs {amplitude:e}"
    );
}

/// Rounding a low-level tone folds its error into harmonics; dither does not.
#[test]
fn dither_replaces_harmonic_distortion_with_noise() {
    let input = tone(1000.0, lsb(16) * 3.0);
    let rounded_h3 = bin_level(&error(&input, &run(&input, 16, DitherMode::Off)), 3000.0);
    let dithered_h3 = bin_level(&error(&input, &run(&input, 16, DitherMode::Tpdf)), 3000.0);
    assert!(
        dithered_h3 < rounded_h3 / 10.0,
        "third harmonic {dithered_h3:e} vs {rounded_h3:e}"
    );
}

#[test]
fn shaping_trades_total_noise_for_a_quieter_low_band() {
    let input = tone(997.0, 0.5);
    let flat = error(&input, &run(&input, 16, DitherMode::Tpdf));
    let shaped = error(&input, &run(&input, 16, DitherMode::Shaped));
    assert!(rms(&shaped) > rms(&flat), "shaping should cost total power");
    assert!(
        low_band_rms(&shaped) < low_band_rms(&flat) / 2.0,
        "low band {} vs {}",
        low_band_rms(&shaped),
        low_band_rms(&flat)
    );
}

#[test]
fn full_scale_input_clamps_to_the_top_code_instead_of_wrapping() {
    let mut out = vec![1.0, -1.0, 2.0, -2.0];
    quantize(&mut out, 16, DitherMode::Tpdf, 7);
    assert_eq!(
        out,
        vec![32_767.0 / 32_768.0, -1.0, 32_767.0 / 32_768.0, -1.0]
    );
}

#[test]
fn the_same_seed_reproduces_the_stream_and_neighbouring_channels_do_not() {
    let input = tone(440.0, 0.25);
    let first = run(&input, 24, DitherMode::Tpdf);
    assert_eq!(first, run(&input, 24, DitherMode::Tpdf));

    let mut second = input.clone();
    quantize(
        &mut second,
        24,
        DitherMode::Tpdf,
        channel_seed(20_260_819, 1),
    );
    assert_ne!(first, second);
}

#[test]
fn mode_names_round_trip() {
    assert_eq!(DitherMode::parse("off"), Some(DitherMode::Off));
    assert_eq!(DitherMode::parse("tpdf"), Some(DitherMode::Tpdf));
    assert_eq!(DitherMode::parse("shaped"), Some(DitherMode::Shaped));
    assert_eq!(DitherMode::parse("triangular"), None);
}
