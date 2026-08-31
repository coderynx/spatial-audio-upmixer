mod spectrum {
    use upmixer_dsp_core::match_reference::spectrum::*;

    fn gate() -> GateParams {
        GateParams {
            absolute_db: -70.0,
            relative_offset_db: -10.0,
            epsilon: 1e-20,
        }
    }

    #[test]
    fn zero_weight_channels_are_excluded() {
        let loud: Vec<f64> = (0..4096).map(|i| (i as f64 * 0.3).sin()).collect();
        let quiet: Vec<f64> = loud.iter().map(|v| v * 0.001).collect();
        let (_, only_loud) =
            weighted_power_spectrum(&[&loud, &quiet], &[1.0, 0.0], 48_000, 1024, &gate());
        let (_, both) = weighted_power_spectrum(&[&loud], &[1.0], 48_000, 1024, &gate());
        for (a, b) in only_loud.iter().zip(both.iter()) {
            assert!((a - b).abs() < 1e-15);
        }
    }

    #[test]
    fn dc_bin_is_stripped() {
        let signal: Vec<f64> = (0..4096).map(|i| 1.0 + (i as f64 * 0.1).sin()).collect();
        let (freqs, power) = weighted_power_spectrum(&[&signal], &[1.0], 48_000, 1024, &gate());
        assert_eq!(freqs.len(), power.len());
        assert!(freqs[0] > 0.0, "first bin should not be DC");
    }
}

mod curve {
    use upmixer_dsp_core::match_reference::curve::*;

    fn taper() -> TaperBand {
        TaperBand {
            low_start: 20.0,
            low_end: 25.0,
            high_start: 18000.0,
            high_end: 20000.0,
        }
    }

    #[test]
    fn log_grid_is_uniform_in_octaves() {
        let grid = log_grid(20_000.0, 20.0, 1.0 / 24.0);
        let steps: Vec<f64> = grid.windows(2).map(|w| (w[1] / w[0]).log2()).collect();
        for s in &steps {
            assert!((s - steps[0]).abs() < 1e-12);
        }
        assert!((grid[0] - 20.0).abs() < 1e-12);
        assert!((grid[grid.len() - 1] - 20_000.0).abs() < 1e-9);
    }

    #[test]
    fn smoothing_preserves_a_constant() {
        let values = vec![3.0; 200];
        for v in smooth_log_grid(&values, 1.0 / 3.0, 1.0 / 24.0) {
            assert!((v - 3.0).abs() < 1e-12);
        }
    }

    #[test]
    fn smoothing_actually_smooths_at_the_requested_width() {
        let mut spike = vec![0.0; 201];
        spike[100] = 1.0;
        let out = smooth_log_grid(&spike, 1.0 / 3.0, 1.0 / 24.0);
        // A real 1/3-octave kernel spans 8 bins of sigma, so the spike must
        // spread far beyond the near-identity 3-tap kernel of the old bug.
        assert!(out[100] < 0.06, "peak {} is too sharp", out[100]);
        assert!(out[92] > 0.01, "kernel is too narrow");
    }

    #[test]
    fn band_edge_taper_zeroes_the_extremes() {
        let freqs = [10.0, 25.0, 1000.0, 19_000.0, 21_000.0];
        let out = band_edge_taper(&[1.0; 5], &freqs, &taper());
        assert_eq!(out[0], 0.0);
        assert!((out[2] - 1.0).abs() < 1e-12);
        assert!(out[3] > 0.0 && out[3] < 1.0);
        assert_eq!(out[4], 0.0);
    }

    #[test]
    fn soft_clamp_bounds_magnitude_and_keeps_small_values_exact() {
        let out = soft_clamp(&[0.5, -0.5, 20.0, -20.0], 6.0, 2.0);
        assert_eq!(out[0], 0.5);
        assert_eq!(out[1], -0.5);
        assert!(out[2] <= 6.0 && out[2] > 4.0);
        assert!((out[2] + out[3]).abs() < 1e-15);
    }

    #[test]
    fn confidence_taper_silences_correction_where_the_reference_is_empty() {
        let correction = [6.0, 6.0];
        let ref_db = [0.0, -100.0];
        let out = confidence_taper(&correction, &ref_db, 40.0);
        assert!((out[0] - 6.0).abs() < 1e-12);
        assert_eq!(out[1], 0.0);
    }

    fn realize(smooth_oct: f64, low_hz: f64, high_hz: f64) -> RealizeParams {
        RealizeParams {
            strength: 1.0,
            max_correction_db: 24.0,
            clamp_knee_db: 2.0,
            smooth_oct,
            grid_step_oct: 1.0 / 24.0,
            low_hz,
            high_hz,
            mask_ease_oct: 0.5,
            bass_clamp_hz: 120.0,
            bass_clamp_db: 2.0,
        }
    }

    #[test]
    fn range_mask_is_unity_inside_and_eases_to_zero_outside() {
        let freqs = [
            75.0, 150.0, 212.13, 300.0, 3000.0, 8000.0, 11_313.7, 16_000.0,
        ];
        let mask = range_mask(&freqs, 300.0, 8000.0, 0.5);
        assert_eq!(mask[0], 0.0);
        assert_eq!(mask[1], 0.0);
        assert!((mask[2] - 0.0).abs() < 1e-9, "ease starts at unity gain 0");
        assert!((mask[3] - 1.0).abs() < 1e-12);
        assert!((mask[4] - 1.0).abs() < 1e-12);
        assert!((mask[5] - 1.0).abs() < 1e-12);
        assert!((mask[6] - 0.0).abs() < 1e-9, "ease ends at unity gain 0");
        assert_eq!(mask[7], 0.0);
        // Monotone through the ease, and smooth: a raised cosine is half-way
        // at the mid-point of the ramp.
        let quarter = range_mask(&[300.0 / 2f64.powf(0.25)], 300.0, 0.0, 0.5);
        assert!((quarter[0] - 0.5).abs() < 1e-12);
    }

    #[test]
    fn a_masked_curve_is_flat_outside_the_range() {
        let grid = log_grid(20_000.0, 20.0, 1.0 / 24.0);
        let flat = vec![6.0; grid.len()];
        let out = realize_curve(&grid, &flat, &realize(1.0 / 3.0, 300.0, 0.0));
        for (f, g) in grid.iter().zip(out.iter()) {
            if *f < 150.0 {
                assert_eq!(*g, 0.0, "{f} Hz should be untouched");
            }
            if *f > 320.0 {
                assert!((g - 6.0).abs() < 1e-9, "{f} Hz should carry the curve");
            }
        }
    }

    #[test]
    fn smoothing_bandwidth_decides_whether_a_notch_survives() {
        let grid = log_grid(20_000.0, 20.0, 1.0 / 24.0);
        let mid = grid.len() / 2;
        let mut notch = vec![0.0; grid.len()];
        notch[mid] = -12.0;
        let fine = realize_curve(&grid, &notch, &realize(1.0 / 12.0, 0.0, 0.0));
        let coarse = realize_curve(&grid, &notch, &realize(1.0, 0.0, 0.0));
        assert!(fine[mid] < -2.0, "1/12 oct erased the notch: {}", fine[mid]);
        assert!(coarse[mid] > -0.3, "1 oct kept the notch: {}", coarse[mid]);
        assert!(fine[mid] < coarse[mid] * 5.0);
    }
}
