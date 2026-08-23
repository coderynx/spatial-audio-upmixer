mod sends {
    use upmixer_dsp_core::kernels::biquad::{sos_magnitude, sosfilt};
    use upmixer_dsp_core::kernels::butter::{butter_sos, BandType};
    use upmixer_dsp_core::routing::sends::*;

    #[test]
    fn elevation_eq_attenuates_low_frequency_content() {
        let sr = 48_000;
        let low: Vec<f64> = (0..4800)
            .map(|i| (2.0 * std::f64::consts::PI * 50.0 * i as f64 / sr as f64).sin())
            .collect();
        let out = elevation_eq(&low, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.0);
        let before: f64 = low[2400..].iter().map(|v| v * v).sum();
        let after: f64 = out[2400..].iter().map(|v| v * v).sum();
        assert!(after < before * 0.5, "{after} vs {before}");
    }

    #[test]
    fn unity_band_gain_is_the_pre_band_output_bit_for_bit() {
        let sr = 48_000;
        let signal: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.11).sin()).collect();
        let with_band = elevation_eq(&signal, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.0);

        let nyq = sr as f64 / 2.0;
        let low = sosfilt(&butter_sos(1, 150.0 / nyq, BandType::Low), &signal);
        let bass: Vec<f64> = signal
            .iter()
            .zip(low.iter())
            .map(|(x, l)| x - l * (1.0 - 0.15))
            .collect();
        let high = sosfilt(&butter_sos(2, 3000.0 / nyq, BandType::High), &bass);
        for (i, (got, (x, hp))) in with_band.iter().zip(bass.iter().zip(high.iter())).enumerate() {
            assert_eq!(*got, x + hp * 0.5, "sample {i}");
        }
    }

    #[test]
    fn the_directional_band_peaks_at_its_centre_gain() {
        let sr = 48_000;
        let sos = directional_band_sos(8000.0, sr, 1.6);
        let nyq = sr as f64 / 2.0;

        assert!((sos_magnitude(&sos, 8000.0 / nyq) - 1.6).abs() < 1e-9);
        // +4.1 dB at centre must not read as a broadband brightness change.
        assert!((sos_magnitude(&sos, 1000.0 / nyq) - 1.0).abs() < 0.05);
        assert!((sos_magnitude(&sos, 20000.0 / nyq) - 1.0).abs() < 0.05);
    }

    #[test]
    fn elevation_response_matches_the_time_domain_chain() {
        let sr = 48_000;
        let freqs = [50.0, 100.0, 500.0, 2000.0, 4000.0, 16000.0];
        for band_gain in [1.0, 1.6] {
            let want = elevation_response(&freqs, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, band_gain);
            for (hz, want) in freqs.iter().zip(want.iter()) {
                let tone: Vec<f64> = (0..48_000)
                    .map(|i| (2.0 * std::f64::consts::PI * hz * i as f64 / sr as f64).sin())
                    .collect();
                let out = elevation_eq(&tone, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, band_gain);
                let tail = &out[24_000..];
                let amplitude =
                    (2.0 * tail.iter().map(|v| v * v).sum::<f64>() / tail.len() as f64).sqrt();
                assert!((amplitude - want).abs() < 1e-9, "{hz} Hz: {amplitude} vs {want}");
            }
        }
    }

    #[test]
    fn band_gain_lifts_energy_at_the_centre_only() {
        let sr = 48_000;
        let tone = |hz: f64| -> Vec<f64> {
            (0..48_000)
                .map(|i| (2.0 * std::f64::consts::PI * hz * i as f64 / sr as f64).sin())
                .collect()
        };
        for (hz, want) in [(8000.0, 1.6), (1000.0, 1.0)] {
            let x = tone(hz);
            let flat = elevation_eq(&x, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.0);
            let lifted = elevation_eq(&x, sr, 150.0, 0.15, 3000.0, 1.5, 8000.0, 1.6);
            let energy = |v: &[f64]| v[24_000..].iter().map(|s| s * s).sum::<f64>().sqrt();
            let ratio = energy(&lifted) / energy(&flat);
            assert!((ratio - want).abs() < 0.05, "{hz} Hz: {ratio} vs {want}");
        }
    }
}

