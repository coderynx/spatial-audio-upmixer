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

mod transient {
    use upmixer_dsp_core::routing::transient::*;

    const SR: u32 = 48_000;

    /// Quarter notes at 120 BPM, so a hit's own contribution to the 250 ms
    /// reference has decayed before the next one lands.
    const HIT_SPACING: usize = 24_000;

    /// A percussive hit train over a quiet steady bed: the hits must come out
    /// quieter relative to the bed than they went in.
    ///
    /// Three properties are load-bearing, and the detector scores nothing
    /// without all of them. The hits are 30 ms decaying strikes rather than
    /// sample-wide spikes, since a sub-millisecond click never moves the
    /// 1.5 ms attack envelope far; they sit ~30 dB over the bed, because the
    /// score is a ratio against the running mean and a hit 13 dB up cannot
    /// reach `DUCK_THRESHOLD_RATIO`; and they are spaced `HIT_SPACING`, since
    /// hits closer together than the reference envelope's own 250 ms hold
    /// that reference up and cap the ratio around 6.6 whatever their level.
    /// Measured on real stems, percussive onsets reach 16-45x the running
    /// mean — a fixture that cannot get there is testing nothing.
    fn hit_train_over_bed(n: usize) -> Vec<f64> {
        (0..n)
            .map(|i| {
                let t = i as f64 / SR as f64;
                let bed = 0.03 * (2.0 * std::f64::consts::PI * 220.0 * t).sin();
                let phase = i % HIT_SPACING;
                let hit = if phase < 1_440 {
                    0.9 * (-(phase as f64) / 240.0).exp()
                        * (2.0 * std::f64::consts::PI * 1_800.0 * t).sin()
                } else {
                    0.0
                };
                bed + hit
            })
            .collect()
    }

    #[test]
    fn zero_depth_is_the_input_bit_for_bit() {
        let x = hit_train_over_bed(48_000);
        let (l, r) = transient_duck(&x, &x, SR, 0.0);
        assert_eq!(l, x);
        assert_eq!(r, x);
    }

    #[test]
    fn transients_are_attenuated_and_sustain_is_not() {
        let x = hit_train_over_bed(96_000);
        let (out, _) = transient_duck(&x, &x, SR, 0.7);

        // A window over a hit's body, and one deep in the sustain between hits.
        let energy = |v: &[f64], from: usize, to: usize| -> f64 {
            v[from..to].iter().map(|s| s * s).sum::<f64>()
        };
        let hit = energy(&out, 24_000, 25_440) / energy(&x, 24_000, 25_440);
        let sustain = energy(&out, 36_000, 40_000) / energy(&x, 36_000, 40_000);

        // Stated as the change in the hit-to-sustain ratio, which is the
        // separation this stage exists to produce. An absolute figure over
        // the hit window would mostly measure where the window was drawn:
        // the envelope needs its 1.5 ms attack before the gain is down, and
        // the crossover's group delay spreads the onset further still.
        let separation_db = 10.0 * (hit / sustain).log10();
        let sustain_db = 10.0 * sustain.log10();
        assert!(separation_db < -2.5, "onset only {separation_db} dB below sustain");
        assert!(sustain_db > -0.5, "sustain moved {sustain_db} dB");
    }

    #[test]
    fn gain_never_leaves_the_depth_bound() {
        let x = hit_train_over_bed(48_000);
        let depth = 0.6;
        let mut ducker = TransientDucker::new(SR, depth);
        for (l, r) in x.iter().zip(x.iter()) {
            let g = ducker.tick(*l, *r);
            assert!((1.0 - depth - 1e-12..=1.0).contains(&g), "gain {g}");
        }
    }

    /// Full depth must attenuate, never annihilate. A band that reaches
    /// exactly zero stops contributing at all and leaves the bands beside it
    /// sounding alone, which is heard as the send changing colour rather than
    /// level — the defect `DUCK_MIN_GAIN` exists to prevent.
    #[test]
    fn full_depth_floors_the_gain_instead_of_nulling_the_band() {
        let x = hit_train_over_bed(96_000);
        let mut ducker = TransientDucker::new(SR, 1.0);
        let gains: Vec<f64> = x.iter().map(|s| ducker.tick(*s, *s)).collect();
        // From the second hit on: at the very first sample the reference
        // envelope is still zero, so the ratio is unbounded and every
        // stimulus saturates. That start-up sample would make this pass
        // without the signal ever qualifying.
        let lowest = gains[HIT_SPACING..].iter().cloned().fold(1.0_f64, f64::min);
        assert!(lowest >= DUCK_MIN_GAIN - 1e-12, "gain reached {lowest}");
        assert!(lowest <= DUCK_MIN_GAIN + 1e-9, "floor never exercised: {lowest}");

        // And the whole send keeps every band alive through the onset.
        let (out, _) = transient_duck(&x, &x, SR, 1.0);
        let window = |v: &[f64]| {
            v[HIT_SPACING..HIT_SPACING + 1_440].iter().map(|s| s * s).sum::<f64>()
        };
        let kept = 10.0 * (window(&out) / window(&x)).log10();
        assert!(kept > -20.1, "onset lost {kept} dB, below the -20 dB floor");
    }

    /// Both sides take the same per-band gain, so a one-sided onset cannot
    /// move the send's image: the quiet side ducks with the loud one.
    #[test]
    fn one_sided_onset_ducks_both_sides() {
        let bed: Vec<f64> = (0..48_000)
            .map(|i| 0.03 * (2.0 * std::f64::consts::PI * 220.0 * i as f64 / SR as f64).sin())
            .collect();
        // The same strike `hit_train_over_bed` uses, on the left only. Note
        // it reaches half the score it would centred, since the detector runs
        // on the mean magnitude of both sides.
        let mut left = bed.clone();
        for (phase, s) in left.iter_mut().skip(HIT_SPACING).take(1_440).enumerate() {
            let t = (HIT_SPACING + phase) as f64 / SR as f64;
            *s += 0.9 * (-(phase as f64) / 240.0).exp()
                * (2.0 * std::f64::consts::PI * 1_800.0 * t).sin();
        }
        let (_, out_r) = transient_duck(&left, &bed, SR, 1.0);
        let (_, quiet_r) = transient_duck(&bed, &bed, SR, 1.0);
        let energy = |v: &[f64]| v[HIT_SPACING..HIT_SPACING + 1_000].iter().map(|s| s * s).sum::<f64>();
        assert!(
            energy(&out_r) < 0.5 * energy(&quiet_r),
            "right side did not follow the left's onset"
        );
    }

    /// A shared gain is a scalar on both sides, so proportional inputs stay
    /// proportional through the duck.
    #[test]
    fn proportional_sides_stay_proportional() {
        let left = hit_train_over_bed(48_000);
        let right: Vec<f64> = left.iter().map(|s| 0.5 * s).collect();
        let (out_l, out_r) = transient_duck(&left, &right, SR, 0.7);
        for i in 0..left.len() {
            assert!((out_r[i] - 0.5 * out_l[i]).abs() < 1e-12, "sample {i}");
        }
    }

    /// The regression anchor for the crossover: three bands, no gain, back to
    /// the input.
    #[test]
    fn the_bands_sum_back_to_the_input() {
        let x = hit_train_over_bed(48_000);
        let mut split = BandSplit::new(SR);
        for (i, s) in x.iter().enumerate() {
            let bands = split.tick(*s);
            let sum: f64 = bands.iter().sum();
            assert!((sum - s).abs() < 1e-12, "sample {i}: {sum} vs {s}");
        }
    }

    /// Steady content scores zero in every band, not only the one the phase 11
    /// test happened to land in.
    #[test]
    fn a_steady_tone_in_any_band_is_left_alone() {
        for hz in [60.0, 440.0, 9_000.0] {
            let tone: Vec<f64> = (0..96_000)
                .map(|i| (2.0 * std::f64::consts::PI * hz * i as f64 / SR as f64).sin())
                .collect();
            let (out, _) = transient_duck(&tone, &tone, SR, 0.8);
            for i in 48_000..96_000 {
                assert!((out[i] - tone[i]).abs() < 1e-4, "{hz} Hz, sample {i}");
            }
        }
    }

    /// The motivating case: a low-band hit must not duck the high-band wash
    /// sharing its moment.
    #[test]
    fn a_low_band_hit_leaves_the_high_band_wash_alone() {
        let n = 96_000;
        let hit: Vec<f64> = (0..n)
            .map(|i| {
                let phase = i % 24_000;
                let env = if phase < 2_400 {
                    (-(phase as f64) / 480.0).exp()
                } else {
                    0.0
                };
                0.9 * env * (2.0 * std::f64::consts::PI * 80.0 * i as f64 / SR as f64).sin()
            })
            .collect();
        let wash: Vec<f64> = (0..n)
            .map(|i| 0.2 * (2.0 * std::f64::consts::PI * 9_000.0 * i as f64 / SR as f64).sin())
            .collect();
        let mixed: Vec<f64> = hit.iter().zip(&wash).map(|(h, w)| h + w).collect();

        let (out, _) = transient_duck(&mixed, &mixed, SR, 0.7);
        // The hit's own band is gone by 2400 samples, so what is left in the
        // window right after it is the wash.
        let tail = |v: &[f64]| v[26_400..47_000].iter().map(|s| s * s).sum::<f64>();
        let kept = 10.0 * (tail(&out) / tail(&mixed)).log10();
        assert!(kept > -0.5, "wash lost {kept} dB to the hit");
    }

    /// Block-by-block ticking is the same as one pass: the streaming preview
    /// and the offline render must not diverge on render-block size.
    #[test]
    fn ticking_in_blocks_matches_one_pass() {
        let x = hit_train_over_bed(48_000);
        let (want, _) = transient_duck(&x, &x, SR, 0.5);

        let mut ducker = MultibandDucker::new(SR, 0.5);
        let mut got = Vec::with_capacity(x.len());
        let mut rest = &x[..];
        for size in [333usize, 999, 128].iter().cycle() {
            if rest.is_empty() {
                break;
            }
            let n = (*size).min(rest.len());
            for s in &rest[..n] {
                got.push(ducker.tick(*s, *s).0);
            }
            rest = &rest[n..];
        }
        assert_eq!(got, want);
    }
}
