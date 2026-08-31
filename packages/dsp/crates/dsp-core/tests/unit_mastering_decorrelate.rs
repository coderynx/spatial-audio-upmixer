mod decorrelate {
    use upmixer_dsp_core::kernels::biquad::SosFilter;
    use upmixer_dsp_core::kernels::butter::butter_bandpass_sos;
    use upmixer_dsp_core::kernels::rng::next_unit;
    use upmixer_dsp_core::mastering::bass::BassParams;
    use upmixer_dsp_core::mastering::decorrelate::*;

    fn params() -> BassParams {
        BassParams {
            sub_gain_db: 0.0,
            mid_gain_db: 0.0,
            unify_hz: None,
            punch: 0.0,
            excite: false,
            lfe_gain_db: 0.0,
            sub_cutoff_hz: 80.0,
            mid_cutoff_hz: 200.0,
            excite_blend: 0.15,
            excite_drive: 3.0,
            punch_fast_ms: 10.0,
            punch_slow_ms: 120.0,
            punch_max_db: 6.0,
            decorrelate: 1.0,
            decorr_low_hz: 100.0,
            decorr_high_hz: 300.0,
            decorr_sections: 32,
            decorr_max_delay_ms: 30.0,
            decorr_fast_ms: 30.0,
            decorr_slow_ms: 300.0,
        }
    }
    fn noise(n: usize, seed: u64) -> Vec<f64> {
        let mut state = seed;
        (0..n).map(|_| next_unit(&mut state) * 2.0 - 1.0).collect()
    }

    fn energy(x: &[f64]) -> f64 {
        x[4800..].iter().map(|v| v * v).sum()
    }

    /// What `bass_control` does around the cascade: zero-phase band, then the
    /// blend. Kept here so the tests exercise the same pairing the callers do.
    fn decorrelate(channel: usize, sr: u32, p: &BassParams, x: &[f64]) -> Vec<f64> {
        let sos = band_sos(sr, p).expect("band");
        let band = upmixer_dsp_core::mastering::bass::zero_phase(&sos, x);
        let mut out = x.to_vec();
        Decorrelator::new(channel, sr, p).run(&mut out, &band);
        out
    }

    #[test]
    fn amount_zero_leaves_no_band_to_process() {
        let p = BassParams {
            decorrelate: 0.0,
            ..params()
        };
        assert!(band_sos(48_000, &p).is_none());
    }

    #[test]
    fn the_band_stays_above_the_mono_crossover() {
        // A unify crossover past the top of the band leaves no band at all.
        let p = BassParams {
            unify_hz: Some(400.0),
            ..params()
        };
        assert!(band_sos(48_000, &p).is_none());

        // Below it, the band starts at the crossover, never under it.
        let p = BassParams {
            unify_hz: Some(120.0),
            ..params()
        };
        let rows = cascade_rows(0, 48_000, &p);
        assert!(band_sos(48_000, &p).is_some());
        assert_eq!(rows.len(), p.decorr_sections);
    }

    #[test]
    fn group_delay_budget_bounds_the_pole_radius() {
        let rows = cascade_rows(3, 48_000, &params());
        assert_eq!(rows.len(), params().decorr_sections);
        for row in &rows {
            // a2 = r², and the row must be a true allpass: b reversed == a.
            let r = row[5].sqrt();
            assert!(
                (POLE_R_MIN..=POLE_R_MAX).contains(&r),
                "radius {r} out of range"
            );
            assert!((row[0] - row[5]).abs() < 1e-12);
            assert!((row[1] - row[4]).abs() < 1e-12);
            assert!((row[2] - 1.0).abs() < 1e-12 && (row[3] - 1.0).abs() < 1e-12);
        }
    }

    #[test]
    fn poles_land_inside_the_band_at_constant_erb_density() {
        let p = params();
        let rows = cascade_rows(1, 48_000, &p);
        let mut rates: Vec<f64> = rows
            .iter()
            .map(|row| {
                let r = row[5].sqrt();
                let theta = (-row[4] / (2.0 * r)).clamp(-1.0, 1.0).acos();
                erb_rate(theta / std::f64::consts::PI * 24_000.0)
            })
            .collect();
        rates.sort_by(|a, b| a.partial_cmp(b).unwrap());

        let (lo, hi) = (erb_rate(p.decorr_low_hz), erb_rate(p.decorr_high_hz));
        assert!(rates[0] >= lo - 1e-9 && *rates.last().unwrap() <= hi + 1e-9);
        // One pole per equal ERB slice, so no gap can exceed two slices.
        let slice = (hi - lo) / rows.len() as f64;
        for pair in rates.windows(2) {
            assert!(
                pair[1] - pair[0] < 2.0 * slice,
                "ERB gap {}",
                pair[1] - pair[0]
            );
        }
    }

    #[test]
    fn the_cascade_is_allpass_so_it_moves_no_energy_of_its_own() {
        let sr = 48_000;
        let x = noise(24_000, 7);
        let mut only_allpass = SosFilter::from_flat(&cascade_rows(0, sr, &params()));
        let y: Vec<f64> = x.iter().map(|v| only_allpass.tick(*v)).collect();
        let (ex, ey) = (energy(&x), energy(&y));
        assert!((ey / ex - 1.0).abs() < 0.02, "energy moved: {ex} -> {ey}");
    }

    /// Noise already confined to the band, so total energy is in-band energy
    /// and no measuring filter is needed — band-passing the result to measure
    /// it would double-filter and confound the very thing under test.
    fn band_limited_noise(n: usize, seed: u64, sr: u32) -> Vec<f64> {
        let x = noise(n, seed);
        let sos = butter_bandpass_sos(4, 120.0 / (sr as f64 / 2.0), 280.0 / (sr as f64 / 2.0));
        upmixer_dsp_core::mastering::bass::zero_phase(sos.as_slice(), &x)
    }

    /// Two channels must not end up with the same cascade: independent random
    /// draws converge on one average response over a band this narrow, so the
    /// separation comes from `DELAY_STAGGER` placing them at different group
    /// delays.
    #[test]
    fn neighbouring_channels_get_different_cascades() {
        let sr = 48_000;
        let p = params();
        let a = cascade_rows(0, sr, &p);
        let b = cascade_rows(1, sr, &p);
        assert_ne!(a, b);
        // The radii differ systematically, not just by the random jitter.
        let mean_r = |rows: &[[f64; 6]]| -> f64 {
            rows.iter().map(|r| r[5].sqrt()).sum::<f64>() / rows.len() as f64
        };
        assert!(
            (mean_r(&a) - mean_r(&b)).abs() > 0.01,
            "{} vs {}",
            mean_r(&a),
            mean_r(&b)
        );
    }

    #[test]
    fn the_band_keeps_its_energy_at_every_depth() {
        // Constant-power is what makes this hold at partial depth. A linear
        // blend averages (1-w)² + w² instead — 2.4 dB down at w = 0.7, since
        // the band and its rotated copy are mutually decorrelated.
        let sr = 48_000;
        let n = 192_000;
        let settle = 24_000;
        let x = band_limited_noise(n, 21, sr);
        let energy_of = |v: &[f64]| -> f64 { v[settle..].iter().map(|s| s * s).sum() };

        for depth in [0.25, 0.5, 0.7, 1.0] {
            let p = BassParams {
                decorrelate: depth,
                ..params()
            };
            let out = decorrelate(0, sr, &p, &x);
            let db = 10.0 * (energy_of(&out) / energy_of(&x)).log10();
            assert!(db.abs() < 0.6, "depth {depth} moved the band by {db} dB");
        }
    }

    #[test]
    fn channels_diverge_without_any_one_of_them_moving() {
        let sr = 48_000;
        let n = 192_000;
        let settle = 24_000;
        let x = band_limited_noise(n, 5, sr);
        let p = params();
        let left = decorrelate(0, sr, &p, &x);
        let right = decorrelate(1, sr, &p, &x);
        let energy_of = |v: &[f64]| -> f64 { v[settle..].iter().map(|s| s * s).sum() };

        // Independent seeds must actually produce different signals.
        let diff: f64 = left[settle..]
            .iter()
            .zip(right[settle..].iter())
            .map(|(a, b)| (a - b).powi(2))
            .sum();
        assert!(
            diff > energy_of(&x) * 0.1,
            "channels stayed correlated: {diff}"
        );

        // Each channel on its own holds its level — a speaker level is a
        // speaker level, decorrelation must not become a gain change.
        for (name, ch) in [("left", &left), ("right", &right)] {
            let db = 10.0 * (energy_of(ch) / energy_of(&x)).log10();
            assert!(db.abs() < 0.6, "{name} level moved by {db} dB");
        }

        // The coherent sum drops — that reduction is the enveloping effect.
        let summed: f64 = (settle..n).map(|i| (left[i] + right[i]).powi(2)).sum();
        assert!(
            summed < 4.0 * energy_of(&x) * 0.75,
            "sum did not decorrelate: {summed}"
        );
    }

    #[test]
    fn a_transient_passes_through_far_more_intact_than_sustain() {
        let sr = 48_000;
        let n = 24_000;
        let p = params();
        // An impulse train in the band: onsets with silence between them.
        let mut clicks = vec![0.0; n];
        for i in (2400..n).step_by(6000) {
            clicks[i] = 1.0;
        }
        let hit = decorrelate(0, sr, &p, &clicks);

        let sustained: Vec<f64> = (0..n)
            .map(|i| 0.3 * (2.0 * std::f64::consts::PI * 200.0 * i as f64 / sr as f64).sin())
            .collect();
        let smeared = decorrelate(0, sr, &p, &sustained);

        let change = |a: &[f64], b: &[f64]| -> f64 {
            let num: f64 = a[2400..]
                .iter()
                .zip(b[2400..].iter())
                .map(|(x, y)| (x - y).powi(2))
                .sum();
            num / a[2400..].iter().map(|v| v * v).sum::<f64>().max(1e-20)
        };
        assert!(
            change(&clicks, &hit) < change(&sustained, &smeared),
            "transients moved as much as sustain: {} vs {}",
            change(&clicks, &hit),
            change(&sustained, &smeared)
        );
    }

    #[test]
    fn content_outside_the_band_is_left_alone() {
        let sr = 48_000;
        let n = 24_000;
        let high: Vec<f64> = (0..n)
            .map(|i| 0.5 * (2.0 * std::f64::consts::PI * 4000.0 * i as f64 / sr as f64).sin())
            .collect();
        let out = decorrelate(0, sr, &params(), &high);

        // Bounded by the band-pass stopband, not zero: a 4th-order pass is far
        // down at 4 kHz, and the stage can only move what it captures.
        let moved: f64 = (4800..n).map(|i| (out[i] - high[i]).powi(2)).sum();
        assert!(moved < energy(&high) * 1e-4, "out-of-band moved: {moved}");
    }

    #[test]
    fn the_cascade_carries_across_block_boundaries() {
        let sr = 48_000;
        let p = params();
        let x = noise(4096, 3);
        let sos = band_sos(sr, &p).expect("band");
        let band = upmixer_dsp_core::mastering::bass::zero_phase(sos.as_slice(), &x);

        let mut whole = x.clone();
        Decorrelator::new(2, sr, &p).run(&mut whole, &band);

        let mut blocked = x.clone();
        let mut d = Decorrelator::new(2, sr, &p);
        for (chunk, b) in blocked.chunks_mut(128).zip(band.chunks(128)) {
            d.run(chunk, b);
        }
        assert_eq!(whole, blocked);
    }
}
