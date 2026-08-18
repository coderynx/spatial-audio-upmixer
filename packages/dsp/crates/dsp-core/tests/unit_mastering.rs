mod eq {
    use upmixer_dsp_core::mastering::eq::*;

    #[test]
    fn breakpoints_extend_to_dc_and_nyquist() {
        let (f, g) = normalize_breakpoints(&[(1000.0, 6.0), (20000.0, 6.0)], 48_000);
        assert_eq!(f[0], 0.0);
        assert_eq!(*f.last().unwrap(), 1.0);
        // The prepended DC point repeats the first gain, and the whole curve
        // is +6 dB, so every gain is the same linear value.
        for v in &g {
            assert!((v - 10.0_f64.powf(0.3)).abs() < 1e-12);
        }
    }

    #[test]
    fn duplicate_frequencies_collapse() {
        let (f, _) = normalize_breakpoints(&[(20.0, 0.0), (20.0, 1.0), (20000.0, 0.0)], 48_000);
        assert_eq!(f.len(), 4, "expected DC, 20 Hz, 20 kHz, Nyquist");
    }

    #[test]
    fn a_flat_curve_leaves_the_signal_alone() {
        let ir = build_fir(&[(20.0, 0.0), (20000.0, 0.0)], 48_000, 1023);
        let signal: Vec<f64> = (0..8192).map(|i| (i as f64 * 0.05).sin() * 0.5).collect();
        let out = apply_fir(&signal, &ir, 1.0);
        // Only compare past the filter's own ramp-in.
        for i in 1200..signal.len() {
            assert!((out[i] - signal[i]).abs() < 2e-3, "sample {i}");
        }
    }

    #[test]
    fn zero_strength_is_a_bypass() {
        let mut bed = vec![vec![1.0; 64], vec![2.0; 64]];
        let before = bed.clone();
        spectral_shape(&mut bed, Some(1), &[0.5, 0.5], 0.0);
        assert_eq!(bed, before);
    }
}

mod compressor {
    use upmixer_dsp_core::mastering::compressor::*;

    fn params() -> CompParams {
        CompParams {
            threshold_db: -18.0,
            ratio: 2.0,
            attack_ms: 20.0,
            release_ms: 200.0,
            knee_db: 6.0,
            makeup_db: 0.0,
            sidechain_hpf_hz: None,
        }
    }

    #[test]
    fn gain_computer_is_continuous_across_the_knee() {
        let p = params();
        let lo = p.threshold_db - p.knee_db / 2.0;
        let hi = p.threshold_db + p.knee_db / 2.0;
        assert!(gain_reduction_db(lo, &p).abs() < 1e-12);
        let inside = gain_reduction_db(hi - 1e-9, &p);
        let outside = gain_reduction_db(hi + 1e-9, &p);
        assert!((inside - outside).abs() < 1e-6, "{inside} vs {outside}");
    }

    #[test]
    fn a_sub_threshold_signal_is_untouched() {
        let quiet = vec![0.001; 4800];
        let mut bed = vec![quiet.clone(), quiet.clone()];
        bus_compress(&mut bed, None, 48_000, &params());
        for ch in &bed {
            for v in ch {
                assert!((v - 0.001).abs() < 1e-9);
            }
        }
    }

    #[test]
    fn lfe_bypasses_the_stage() {
        let loud = vec![0.9; 4800];
        let mut bed = vec![loud.clone(), loud.clone()];
        bus_compress(&mut bed, Some(1), 48_000, &params());
        assert_eq!(bed[1], loud);
        assert!(bed[0].last().unwrap() < &0.9);
    }

    #[test]
    fn the_sidechain_high_pass_keeps_bass_out_of_the_detector() {
        let sr = 48_000;
        let n = 24_000;
        let signal: Vec<f64> = (0..n)
            .map(|i| {
                let t = i as f64 / sr as f64;
                0.9 * (2.0 * std::f64::consts::PI * 40.0 * t).sin()
                    + 0.05 * (2.0 * std::f64::consts::PI * 2000.0 * t).sin()
            })
            .collect();

        let mut full_band = vec![signal.clone(), signal.clone()];
        let wide = bus_compress(&mut full_band, None, sr, &params());

        let mut filtered = vec![signal.clone(), signal.clone()];
        let p = CompParams { sidechain_hpf_hz: Some(100.0), ..params() };
        let narrow = bus_compress(&mut filtered, None, sr, &p);

        assert!(
            narrow.avg_gr_db < wide.avg_gr_db,
            "{} vs {}",
            narrow.avg_gr_db,
            wide.avg_gr_db
        );
        // The detector is filtered; the signal the gain multiplies is not.
        let ratio = filtered[0][n - 1] / signal[n - 1];
        assert!(ratio.is_finite() && ratio > 0.0, "output lost its low end");
    }

    #[test]
    fn unity_ratio_is_a_bypass() {
        let loud = vec![0.9; 128];
        let mut bed = vec![loud.clone()];
        let p = CompParams { ratio: 1.0, ..params() };
        bus_compress(&mut bed, None, 48_000, &p);
        assert_eq!(bed[0], loud);
    }
}

mod limiter {
    use upmixer_dsp_core::mastering::limiter::*;
    use upmixer_dsp_core::loudness::measure_true_peak;

    fn params() -> LimiterParams {
        LimiterParams {
            ceiling_dbtp: -1.0,
            lookahead_ms: 5.0,
            release_ms: 50.0,
            safety_margin_db: 0.1,
        }
    }

    #[test]
    fn forward_window_min_matches_a_brute_force_reference() {
        let values: Vec<f64> = (0..200).map(|i| ((i * 17) % 13) as f64 * 0.05 + 0.1).collect();
        let window = 9;
        let got = forward_window_min(&values, window);
        for i in 0..values.len() {
            let end = (i + window).min(values.len());
            let mut want = 1.0_f64;
            for v in &values[i..end] {
                want = want.min(*v);
            }
            assert!((got[i] - want).abs() < 1e-15, "index {i}: {} vs {want}", got[i]);
        }
    }

    #[test]
    fn quiet_material_passes_through_untouched() {
        let quiet: Vec<f64> = (0..9600).map(|i| 0.05 * (i as f64 * 0.1).sin()).collect();
        let mut bed = vec![quiet.clone()];
        let gr = lookahead_limit(&mut bed, 48_000, &params());
        assert!(gr < 1e-9, "unexpected gain reduction {gr} dB");
        for (a, b) in bed[0].iter().zip(quiet.iter()) {
            assert!((a - b).abs() < 1e-12);
        }
    }

    #[test]
    fn output_respects_the_true_peak_ceiling() {
        let sr = 48_000;
        let loud: Vec<f64> = (0..sr)
            .map(|i| {
                let t = i as f64 / sr as f64;
                0.98 * (2.0 * std::f64::consts::PI * 997.0 * t).sin()
                    * (1.0 + 0.5 * (2.0 * std::f64::consts::PI * 3.0 * t).sin())
            })
            .collect();
        let mut bed = vec![loud.clone(), loud.iter().map(|v| v * 0.8).collect()];
        let gr = lookahead_limit(&mut bed, sr as u32, &params());
        assert!(gr > 0.0, "expected the limiter to engage");
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        let dbtp = measure_true_peak(&refs);
        assert!(dbtp <= -1.0 + 1e-6, "true peak {dbtp} dBTP exceeds the ceiling");
    }

    #[test]
    fn every_channel_gets_the_same_gain_curve() {
        let sr = 48_000;
        let loud: Vec<f64> = (0..sr).map(|i| 0.95 * (i as f64 * 0.05).sin()).collect();
        let quiet: Vec<f64> = loud.iter().map(|v| v * 0.25).collect();
        let mut bed = vec![loud.clone(), quiet.clone()];
        lookahead_limit(&mut bed, sr as u32, &params());
        for i in 0..loud.len() {
            if loud[i].abs() > 1e-9 {
                let ratio_a = bed[0][i] / loud[i];
                let ratio_b = bed[1][i] / quiet[i];
                assert!((ratio_a - ratio_b).abs() < 1e-12, "sample {i}");
            }
        }
    }
}

mod bass {
    use upmixer_dsp_core::mastering::bass::*;

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
            decorrelate: 0.0,
            decorr_low_hz: 100.0,
            decorr_high_hz: 300.0,
            decorr_sections: 32,
            decorr_max_delay_ms: 30.0,
            decorr_fast_ms: 30.0,
            decorr_slow_ms: 300.0,
        }
    }

    fn tone(freq: f64, sample_rate: u32, n: usize, amplitude: f64) -> Vec<f64> {
        (0..n)
            .map(|i| {
                amplitude * (2.0 * std::f64::consts::PI * freq * i as f64 / sample_rate as f64).sin()
            })
            .collect()
    }

    fn energy(x: &[f64]) -> f64 {
        x[2400..].iter().map(|v| v * v).sum()
    }

    /// Equal weights over `targets`, the arithmetic `bass.py` performs for a
    /// spread with no LFE send.
    fn even(targets: &[usize]) -> Vec<(usize, f64)> {
        targets.iter().map(|&i| (i, 1.0 / targets.len() as f64)).collect()
    }

    #[test]
    fn all_stages_off_is_a_bypass() {
        let mut bed = vec![vec![0.3; 512], vec![-0.2; 512]];
        let before = bed.clone();
        bass_control(&mut bed, Some(1), &[], 48_000, &params());
        assert_eq!(bed, before);
    }

    #[test]
    fn unify_preserves_the_coherent_low_end_and_spreads_it() {
        let sr = 48_000;
        let n = 9600;
        let bass = tone(40.0, sr, n, 0.5);
        // Bass in the front pair only, silence in the surrounds — a stereo
        // source as the router leaves it.
        let mut bed = vec![bass.clone(), bass.clone(), vec![0.0; n], vec![0.0; n]];
        let sum_before: Vec<f64> = (0..n).map(|i| bed.iter().map(|c| c[i]).sum()).collect();

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut bed, None, &even(&[0, 1, 2, 3]), sr, &p);

        let sum_after: Vec<f64> = (0..n).map(|i| bed.iter().map(|c| c[i]).sum()).collect();
        let residual: f64 = (2400..n).map(|i| (sum_after[i] - sum_before[i]).powi(2)).sum();
        assert!(
            residual < energy(&sum_before) * 1e-6,
            "coherent sum moved: residual {residual} of {}",
            energy(&sum_before)
        );

        // The surrounds, silent before, now carry the redistributed share.
        assert!(energy(&bed[2]) > energy(&bass) * 0.01, "surround got no low end");
        assert!(energy(&bed[0]) < energy(&bass) * 0.5, "front pair kept its low end");
    }

    #[test]
    fn split_conserves_the_low_end_through_lfe_replay_gain() {
        let sr = 48_000;
        let n = 9600;
        let bass = tone(40.0, sr, n, 0.5);
        let mut mains_only = vec![bass.clone(), bass.clone(), vec![0.0; n]];
        let mut split = mains_only.clone();

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut mains_only, Some(2), &even(&[0, 1]), sr, &p);

        // `split` at 0.5: mains share 0.5, LFE takes 0.5 scaled by the -10 dB
        // BS.775 authoring gain that playback's +10 dB undoes.
        let authoring = 0.316_227_766_016_837_94;
        let targets = vec![(0, 0.25), (1, 0.25), (2, 0.5 * authoring)];
        bass_control(&mut split, Some(2), &targets, sr, &p);

        let replay = 10.0_f64.powf(10.0 / 20.0);
        for i in 2400..n {
            let reference = mains_only[0][i] + mains_only[1][i] + mains_only[2][i] * replay;
            let played = split[0][i] + split[1][i] + split[2][i] * replay;
            assert!(
                (played - reference).abs() < 1e-9,
                "sample {i}: {played} vs {reference}"
            );
        }
    }

    #[test]
    fn an_lfe_send_leaves_the_mains_untouched() {
        let sr = 48_000;
        let n = 4800;
        let bass = tone(40.0, sr, n, 0.5);
        let mut without = vec![bass.clone(), bass.clone(), vec![0.0; n]];
        let mut with = without.clone();

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut without, Some(2), &even(&[0, 1]), sr, &p);

        let mut targets = even(&[0, 1]);
        targets.push((2, 0.25));
        bass_control(&mut with, Some(2), &targets, sr, &p);

        assert_eq!(without[0], with[0], "an LFE send moved the mains");
        assert_eq!(without[1], with[1], "an LFE send moved the mains");
        assert!(energy(&with[2]) > 0.0, "LFE got nothing");
    }

    #[test]
    fn the_exciter_stays_out_of_the_lfe() {
        let sr = 48_000;
        let n = 4800;
        let bass = tone(40.0, sr, n, 0.5);
        let mut plain = vec![bass.clone(), bass.clone(), vec![0.0; n]];
        let mut excited = plain.clone();

        let mut targets = even(&[0, 1]);
        targets.push((2, 0.25));
        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut plain, Some(2), &targets, sr, &p);
        bass_control(&mut excited, Some(2), &targets, sr, &BassParams { excite: true, ..p });

        assert_eq!(plain[2], excited[2], "harmonics reached the LFE");
        assert!(energy(&excited[0]) > energy(&plain[0]), "exciter did nothing");
    }

    #[test]
    fn unification_commutes_with_a_shared_upstream_gain() {
        // The EQ and reference-match stages apply one shared curve to every
        // bed channel; that is what lets bass control ignore them.
        let sr = 48_000;
        let n = 9600;
        let mut before = vec![tone(40.0, sr, n, 0.5), tone(55.0, sr, n, 0.4), tone(70.0, sr, n, 0.3)];
        let mut after = before.clone();
        let gain = 1.7;

        let p = BassParams { unify_hz: Some(90.0), ..params() };
        for ch in before.iter_mut() {
            for v in ch.iter_mut() {
                *v *= gain;
            }
        }
        bass_control(&mut before, None, &even(&[0, 1, 2]), sr, &p);

        bass_control(&mut after, None, &even(&[0, 1, 2]), sr, &p);
        for ch in after.iter_mut() {
            for v in ch.iter_mut() {
                *v *= gain;
            }
        }

        for (a, b) in before.iter().zip(after.iter()) {
            for (x, y) in a.iter().zip(b.iter()) {
                assert!((x - y).abs() < 1e-9, "{x} vs {y}");
            }
        }
    }

    #[test]
    fn punch_off_is_a_bypass_and_punch_up_favours_the_attack() {
        let sr = 48_000;
        let n = 24_000;
        // A 40 Hz burst that stops a third of the way through, so the shaper
        // has an attack and a decaying sustain to separate.
        let burst: Vec<f64> = tone(40.0, sr, n, 0.5)
            .iter()
            .enumerate()
            .map(|(i, v)| if i < n / 3 { *v } else { v * 0.2 })
            .collect();

        let mut flat = vec![burst.clone()];
        let p = BassParams { unify_hz: Some(90.0), ..params() };
        bass_control(&mut flat, None, &even(&[0]), sr, &p);

        let mut shaped = vec![burst.clone()];
        bass_control(&mut shaped, None, &even(&[0]), sr, &BassParams { punch: 0.5, ..p });

        let ratio = |ch: &[f64]| {
            let attack: f64 = ch[2400..n / 3].iter().map(|v| v * v).sum();
            let sustain: f64 = ch[n / 3 + 4800..].iter().map(|v| v * v).sum();
            attack / sustain.max(1e-20)
        };
        assert!(
            ratio(&shaped[0]) > ratio(&flat[0]) * 1.05,
            "{} vs {}",
            ratio(&shaped[0]),
            ratio(&flat[0])
        );

        let mut bypass = vec![burst.clone()];
        bass_control(&mut bypass, None, &even(&[0]), sr, &BassParams { punch: 0.0, ..p });
        assert_eq!(bypass[0], flat[0]);
    }

    #[test]
    fn lfe_trim_only_touches_lfe() {
        let mut bed = vec![vec![0.5; 64], vec![0.5; 64]];
        let p = BassParams { lfe_gain_db: 6.0, ..params() };
        bass_control(&mut bed, Some(1), &[], 48_000, &p);
        assert!((bed[0][0] - 0.5).abs() < 1e-12);
        assert!((bed[1][0] - 0.5 * 10.0_f64.powf(0.3)).abs() < 1e-12);
    }

    #[test]
    fn sub_boost_raises_low_frequency_energy() {
        let sr = 48_000;
        let low = tone(40.0, sr, 4800, 1.0);
        let mut bed = vec![low.clone()];
        let p = BassParams { sub_gain_db: 6.0, ..params() };
        bass_control(&mut bed, None, &[], sr, &p);
        assert!(energy(&bed[0]) > energy(&low) * 3.0);
    }
}
