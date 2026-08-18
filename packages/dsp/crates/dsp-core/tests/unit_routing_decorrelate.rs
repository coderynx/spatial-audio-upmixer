mod decorrelate {
    use upmixer_dsp_core::kernels::rng::next_unit;
    use upmixer_dsp_core::routing::decorrelate::*;
    use upmixer_dsp_core::kernels::fft::RealFft;

    const SR: u32 = 48_000;
    const NFFT: usize = 1 << 16;

    fn default_pair() -> (VelvetFir, VelvetFir) {
        velvet_pair_default(SR)
    }

    fn impulse_response(fir: &VelvetFir) -> Vec<f64> {
        let mut h = vec![0.0; fir.span() + 1];
        for &(delay, gain) in fir.taps() {
            h[delay] += gain;
        }
        h
    }

    /// Per-bin magnitude in dB above 200 Hz, relative to the mean power.
    fn deviation_db(h: &[f64]) -> Vec<f64> {
        let spectrum = RealFft::new(NFFT).rfft(h);
        let bin_hz = SR as f64 / NFFT as f64;
        let power: Vec<f64> = spectrum
            .iter()
            .enumerate()
            .filter(|(i, _)| {
                let f = *i as f64 * bin_hz;
                (200.0..=16_000.0).contains(&f)
            })
            .map(|(_, c)| c.norm_sqr())
            .collect();
        let mean = power.iter().sum::<f64>() / power.len() as f64;
        power.iter().map(|p| 10.0 * (p / mean).log10()).collect()
    }

    /// Worst third-octave band deviation from flat, 200 Hz to 16 kHz.
    fn third_octave_worst(h: &[f64]) -> f64 {
        let spectrum = RealFft::new(NFFT).rfft(h);
        let bin_hz = SR as f64 / NFFT as f64;
        let power: Vec<f64> = spectrum.iter().map(|c| c.norm_sqr()).collect();
        let band_power = |lo: f64, hi: f64| -> Option<f64> {
            let bins: Vec<f64> = power
                .iter()
                .enumerate()
                .filter(|(i, _)| {
                    let f = *i as f64 * bin_hz;
                    f >= lo && f < hi
                })
                .map(|(_, p)| *p)
                .collect();
            (!bins.is_empty()).then(|| bins.iter().sum::<f64>() / bins.len() as f64)
        };
        let reference = band_power(200.0, 16_000.0).expect("band");
        let step = 2.0_f64.powf(1.0 / 3.0);
        let edge = 2.0_f64.powf(1.0 / 6.0);
        let mut centre = 200.0;
        let mut worst = 0.0_f64;
        while centre <= 16_000.0 {
            if let Some(p) = band_power(centre / edge, centre * edge) {
                worst = worst.max((10.0 * (p / reference).log10()).abs());
            }
            centre *= step;
        }
        worst
    }

    /// Dips below `-10 dB`, counted as crossings — a comb produces hundreds of
    /// evenly spaced ones, a velvet sequence a few dozen scattered ones.
    fn dip_count(h: &[f64]) -> usize {
        deviation_db(h)
            .windows(2)
            .filter(|w| w[0] >= -10.0 && w[1] < -10.0)
            .count()
    }

    fn energy(x: &[f64]) -> f64 {
        x.iter().map(|v| v * v).sum()
    }

    fn noise(n: usize, seed: u64) -> Vec<f64> {
        let mut state = seed;
        (0..n).map(|_| next_unit(&mut state) * 2.0 - 1.0).collect()
    }

    #[test]
    fn the_same_seed_builds_the_same_pair() {
        assert_eq!(default_pair(), default_pair());
        let (other, _) = velvet_pair(SR, VELVET_LENGTH_MS, VELVET_TAPS_PER_SIDE, 7, 1.0);
        assert_ne!(other, default_pair().0);
    }

    #[test]
    fn the_pair_is_sparse_and_spans_the_requested_length() {
        let (left, right) = default_pair();
        let n = (SR as f64 * VELVET_LENGTH_MS / 1000.0).round() as usize;
        for side in [&left, &right] {
            assert_eq!(side.taps().len(), VELVET_TAPS_PER_SIDE);
            assert!(side.span() < n, "span {} exceeds {n}", side.span());
            assert!(side.taps().windows(2).all(|w| w[0].0 < w[1].0), "taps not ascending");
        }
        // Both sides reach into the last cell pair, so neither is a short
        // cluster the other has to compensate for.
        assert!(left.span() > n * 9 / 10 && right.span() > n * 9 / 10);
    }

    #[test]
    fn the_sides_share_no_tap_and_carry_unit_energy() {
        let (left, right) = default_pair();
        for side in [&left, &right] {
            let e: f64 = side.taps().iter().map(|(_, g)| g * g).sum();
            assert!((e - 1.0).abs() < 1e-12, "energy {e}");
        }
        for &(p, _) in left.taps() {
            assert!(right.taps().iter().all(|(q, _)| *q != p), "shared tap at {p}");
        }
    }

    /// The property that must never regress: a mono fold-down of the pair
    /// keeps the full power sum, because the two sides cannot cancel.
    #[test]
    fn a_mono_fold_down_of_the_pair_is_the_power_sum() {
        let (left, right) = default_pair();
        let x = noise(48_000, 11);
        let sum: Vec<f64> = left
            .process(&x)
            .iter()
            .zip(right.process(&x).iter())
            .map(|(a, b)| a + b)
            .collect();
        let ratio = 10.0 * (energy(&sum[1440..]) / (2.0 * energy(&x[1440..]))).log10();
        assert!(ratio.abs() < 0.1, "fold-down lost {ratio} dB against the power sum");
    }

    #[test]
    fn neither_side_nor_their_sum_carries_a_comb() {
        let (left, right) = default_pair();
        let hl = impulse_response(&left);
        let hr = impulse_response(&right);
        let sum: Vec<f64> = hl.iter().zip(hr.iter()).map(|(a, b)| a + b).collect();

        for (name, h) in [("left", &hl), ("right", &hr), ("sum", &sum)] {
            // 3.5 dB, not the 1.5 dB the plan guessed at: a sparse FIR sits on
            // a Rayleigh magnitude floor no tap count or length improves. The
            // dip count is the metric that separates this from a comb.
            let worst = third_octave_worst(h);
            assert!(worst < 3.5, "{name} third-octave deviation {worst} dB");
            let dips = dip_count(h);
            assert!(dips < 120, "{name} has {dips} dips, comb-like");
        }

        // The single-delay blend this replaces, measured the same way.
        let delay = (SR as f64 * 0.031) as usize;
        let mut comb = vec![0.0; delay + 1];
        comb[0] = 0.45;
        comb[delay] = 0.55;
        assert!(dip_count(&comb) > 400, "the comb baseline stopped combing");
    }

    #[test]
    fn the_pair_decorrelates_white_noise() {
        let (left, right) = default_pair();
        let x = noise(192_000, 3);
        let a = left.process(&x);
        let b = right.process(&x);
        let cross: f64 = a[1440..].iter().zip(b[1440..].iter()).map(|(p, q)| p * q).sum();
        let corr = cross / (energy(&a[1440..]) * energy(&b[1440..])).sqrt();
        assert!(corr.abs() < 0.4, "interchannel correlation {corr}");

        // Each side on its own holds the input level.
        for (name, y) in [("left", &a), ("right", &b)] {
            let db = 10.0 * (energy(&y[1440..]) / energy(&x[1440..])).log10();
            assert!(db.abs() < 0.2, "{name} moved the level by {db} dB");
        }
    }

    #[test]
    fn the_streaming_form_matches_the_offline_one_across_blocks() {
        let (left, _) = default_pair();
        let x = noise(4096, 5);
        let want = left.process(&x);

        // Ragged block sizes on purpose: one shorter than the internal chunk,
        // one longer, so neither divides it.
        for sizes in [[128, 128], [333, 999]] {
            let mut line = VelvetLine::new(&left);
            let mut got: Vec<f64> = Vec::with_capacity(x.len());
            let mut rest = &x[..];
            let mut size = sizes.iter().cycle();
            while !rest.is_empty() {
                let n = (*size.next().expect("size")).min(rest.len());
                let mut block = rest[..n].to_vec();
                line.process(&mut block);
                got.extend(block);
                rest = &rest[n..];
            }
            for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
                assert!((a - b).abs() < 1e-12, "sample {i}: {a} vs {b}");
            }
        }

        let mut line = VelvetLine::new(&left);
        let mut impulse = vec![1.0, 0.0];
        line.process(&mut impulse);
        assert_eq!(impulse[0], left.taps()[0].1 * f64::from(left.taps()[0].0 == 0));
    }

    #[test]
    fn the_wet_fraction_trades_flatness_for_correlation() {
        let x = noise(96_000, 9);
        let mut previous = 0.0;
        for wet in [0.25, 0.5, 0.75] {
            let (left, right) = velvet_pair(SR, VELVET_LENGTH_MS, VELVET_TAPS_PER_SIDE, VELVET_SEED, wet);
            let a = left.process(&x);
            let b = right.process(&x);
            let cross: f64 = a[1440..].iter().zip(b[1440..].iter()).map(|(p, q)| p * q).sum();
            let corr = cross / (energy(&a[1440..]) * energy(&b[1440..])).sqrt();
            // Correlation is 1 - wet by construction: only the dry taps overlap.
            assert!((corr - (1.0 - wet)).abs() < 0.05, "wet {wet} gave correlation {corr}");
            assert!(corr < 1.0 - previous + 1e-9);
            previous = wet;
            let e: f64 = left.taps().iter().map(|(_, g)| g * g).sum();
            assert!((e - 1.0).abs() < 1e-12, "wet {wet} lost energy: {e}");
        }

        // Fully dry degenerates to an impulse rather than to silence.
        let (dry, _) = velvet_pair(SR, VELVET_LENGTH_MS, VELVET_TAPS_PER_SIDE, VELVET_SEED, 0.0);
        assert_eq!(dry.taps(), &[(0, 1.0)]);
    }
}
