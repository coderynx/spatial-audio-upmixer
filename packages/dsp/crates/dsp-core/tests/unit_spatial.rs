mod xtc {
    use upmixer_dsp_core::spatial::xtc::*;

    #[test]
    fn an_identity_matrix_passes_both_ears_through() {
        let filters = XtcFilterSet {
            taps: [
                [vec![1.0, 0.0], vec![0.0, 0.0]],
                [vec![0.0, 0.0], vec![1.0, 0.0]],
            ],
        };
        let l = [1.0, 2.0, 3.0];
        let r = [-1.0, -2.0, -3.0];
        let (sl, sr) = apply_xtc(&l, &r, &filters);
        assert_eq!(sl, l.to_vec());
        assert_eq!(sr, r.to_vec());
    }

    #[test]
    fn a_swap_matrix_exchanges_the_ears() {
        let filters = XtcFilterSet {
            taps: [[vec![0.0], vec![1.0]], [vec![1.0], vec![0.0]]],
        };
        let (sl, sr) = apply_xtc(&[1.0, 2.0], &[3.0, 4.0], &filters);
        assert_eq!(sl, vec![3.0, 4.0]);
        assert_eq!(sr, vec![1.0, 2.0]);
    }
}

mod voicing {
    use upmixer_dsp_core::spatial::voicing::*;

    #[test]
    fn all_zero_params_are_an_exact_bypass() {
        let l: Vec<f64> = (0..512).map(|i| (i as f64 * 0.1).sin()).collect();
        let r: Vec<f64> = (0..512).map(|i| (i as f64 * 0.13).cos()).collect();
        let p = VoicingParams {
            crossfeed_cutoff_hz: 700.0,
            presence_q: 1.0,
            ..Default::default()
        };
        let (out_l, out_r) = apply_voicing(&l, &r, 48_000, &p);
        assert_eq!(out_l, l);
        assert_eq!(out_r, r);
    }

    #[test]
    fn widen_preserves_the_mid_and_scales_the_side() {
        let (l, r) = widen(&[1.0], &[-1.0], 1.0);
        // Pure side content doubles at amount 1.0.
        assert!((l[0] - 2.0).abs() < 1e-15);
        assert!((r[0] + 2.0).abs() < 1e-15);
        let (l, r) = widen(&[1.0], &[1.0], 1.0);
        assert!((l[0] - 1.0).abs() < 1e-15 && (r[0] - 1.0).abs() < 1e-15);
    }

    #[test]
    fn crossfeed_moves_low_frequency_content_across() {
        let n = 4800;
        let sr = 48_000;
        let tone: Vec<f64> = (0..n)
            .map(|i| (2.0 * std::f64::consts::PI * 100.0 * i as f64 / sr as f64).sin())
            .collect();
        let silence = vec![0.0; n];
        let (_, out_r) = crossfeed(&tone, &silence, sr, 0.3, 700.0);
        let energy: f64 = out_r[2400..].iter().map(|v| v * v).sum();
        assert!(
            energy > 1.0,
            "expected bleed into the silent channel, got {energy}"
        );
    }
}

mod downmix {
    use std::f64::consts::SQRT_2;
    use upmixer_dsp_core::spatial::downmix::*;

    #[test]
    fn back_channels_fold_into_the_matching_side() {
        let sl = [1.0, 1.0];
        let bl = [1.0, 1.0];
        let (left, _) = itu_downmix_stereo(
            &[(DownmixRole::Sl, &sl), (DownmixRole::Bl, &bl)],
            ITU_CENTER_COEFF,
            ITU_CENTER_COEFF,
        );
        let want = ITU_CENTER_COEFF + ITU_CENTER_COEFF * ITU_CENTER_COEFF;
        assert!((left[0] - want).abs() < 1e-15);
    }

    #[test]
    fn centre_splits_equally_and_uses_the_exact_coefficient() {
        let c = [1.0];
        let (left, right) = itu_downmix_stereo(&[(DownmixRole::C, &c)], 0.0, 0.0);
        assert_eq!(left, right);
        assert!((left[0] - 1.0 / SQRT_2).abs() < 1e-16);
    }

    #[test]
    fn heights_fold_onto_their_own_side_at_the_height_coefficient() {
        let tfl = [1.0];
        let tbl = [1.0];
        let inputs = [(DownmixRole::Tfl, &tfl[..]), (DownmixRole::Tbl, &tbl[..])];
        let (left, right) = itu_downmix_stereo(&inputs, 0.5, ITU_CENTER_COEFF);
        assert!((left[0] - (ITU_CENTER_COEFF + ITU_CENTER_COEFF * 0.5)).abs() < 1e-15);
        assert_eq!(right[0], 0.0);

        let mono = itu_downmix_mono(&inputs, 0.5, ITU_CENTER_COEFF);
        let want = ITU_CENTER_COEFF * ITU_CENTER_COEFF + ITU_CENTER_COEFF * 0.5;
        assert!((mono[0] - want).abs() < 1e-15);
    }

    #[test]
    fn a_zero_height_coefficient_drops_the_height_channels() {
        let tfl = [1.0];
        let fl = [1.0];
        let inputs = [(DownmixRole::Fl, &fl[..]), (DownmixRole::Tfl, &tfl[..])];
        let (left, _) = itu_downmix_stereo(&inputs, 0.7071, 0.0);
        assert_eq!(left[0], 1.0);
        assert_eq!(itu_downmix_mono(&inputs, 0.7071, 0.0)[0], ITU_CENTER_COEFF);
    }

    #[test]
    fn soft_limit_leaves_sub_threshold_samples_alone() {
        let mut signal = [0.5, -0.9, 0.94];
        let before = signal;
        soft_limit(&mut signal, 0.95);
        assert_eq!(signal, before);
    }

    #[test]
    fn soft_limit_keeps_output_under_unity_and_preserves_sign() {
        let mut signal = [5.0, -5.0];
        soft_limit(&mut signal, 0.95);
        // The tanh asymptote is exactly 1.0 and saturates there in f64.
        assert!(signal[0] > 0.95 && signal[0] <= 1.0);
        assert!((signal[0] + signal[1]).abs() < 1e-15);
    }

    #[test]
    fn downmix_lock_restores_the_input_pair_without_touching_lfe() {
        let roles = [
            Some(DownmixRole::Fl),
            Some(DownmixRole::Fr),
            Some(DownmixRole::C),
            None,
            Some(DownmixRole::Sl),
            Some(DownmixRole::Sr),
            Some(DownmixRole::Tfl),
            Some(DownmixRole::Tfr),
        ];
        let mut bed = vec![
            vec![0.2, -0.1],
            vec![-0.3, 0.4],
            vec![0.1, 0.2],
            vec![0.9, -0.8],
            vec![0.4, 0.1],
            vec![0.2, -0.2],
            vec![0.1, -0.3],
            vec![-0.1, 0.5],
        ];
        let lfe = bed[3].clone();
        let input_left = [0.7, -0.6];
        let input_right = [-0.4, 0.8];

        apply_stereo_downmix_lock(roles, &mut bed, &input_left, &input_right, 0.7071, 0.7071);
        let inputs: Vec<_> = roles
            .into_iter()
            .zip(&bed)
            .filter_map(|(role, channel)| role.map(|role| (role, channel.as_slice())))
            .collect();
        let (left, right) = itu_downmix_stereo(&inputs, 0.7071, 0.7071);

        for (actual, expected) in left.iter().zip(input_left) {
            assert!((actual - expected).abs() < 1e-12);
        }
        for (actual, expected) in right.iter().zip(input_right) {
            assert!((actual - expected).abs() < 1e-12);
        }
        assert_eq!(bed[3], lfe);
    }
}

mod ambisonics {
    use upmixer_dsp_core::spatial::ambisonics::*;

    #[test]
    fn omni_channel_is_direction_independent() {
        for (az, el) in [(0.0, 0.0), (1.2, -0.4), (-2.7, 0.9)] {
            assert!((encode_gains(az, el)[0] - 1.0).abs() < 1e-15);
        }
    }

    #[test]
    fn front_centre_puts_all_first_order_energy_in_x() {
        let g = encode_gains(0.0, 0.0);
        assert!((g[3] - 3.0_f64.sqrt()).abs() < 1e-15, "X");
        assert!(g[1].abs() < 1e-15, "Y");
        assert!(g[2].abs() < 1e-15, "Z");
    }

    #[test]
    fn directly_overhead_puts_all_first_order_energy_in_z() {
        let g = encode_gains(0.0, std::f64::consts::FRAC_PI_2);
        assert!((g[2] - 3.0_f64.sqrt()).abs() < 1e-15);
        assert!(g[1].abs() < 1e-15 && g[3].abs() < 1e-15);
    }

    #[test]
    fn left_and_right_sources_mirror_in_y() {
        let left = encode_gains(std::f64::consts::FRAC_PI_2, 0.0);
        let right = encode_gains(-std::f64::consts::FRAC_PI_2, 0.0);
        assert!((left[1] + right[1]).abs() < 1e-15);
    }

    #[test]
    fn a_unit_impulse_decode_returns_the_filter_taps() {
        let mut hoa = HoaBus::new(8);
        hoa.channels[0][0] = 1.0;
        let mut taps: Vec<[Vec<f64>; 2]> = (0..N_ACN_CHANNELS)
            .map(|_| [vec![0.0; 4], vec![0.0; 4]])
            .collect();
        taps[0][0] = vec![0.5, 0.25, 0.0, 0.0];
        taps[0][1] = vec![-0.5, 0.0, 0.125, 0.0];
        let (l, r) = decode_to_binaural(&hoa, &DecodeFilterSet { taps });
        assert!((l[0] - 0.5).abs() < 1e-15 && (l[1] - 0.25).abs() < 1e-15);
        assert!((r[0] + 0.5).abs() < 1e-15 && (r[2] - 0.125).abs() < 1e-15);
    }
}

mod fold_to_51 {
    use upmixer_dsp_core::spatial::downmix::*;

    const K: f64 = std::f64::consts::FRAC_1_SQRT_2;

    fn bed() -> Vec<Vec<f64>> {
        (0..12)
            .map(|c| (0..8).map(|i| (c * 8 + i) as f64).collect())
            .collect()
    }

    const NAMES: [&str; 12] = [
        "FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR", "TFL", "TFR", "TBL", "TBR",
    ];

    #[test]
    fn a_bed_of_five_one_or_narrower_needs_no_fold() {
        assert!(FoldTo51::new(&["FL", "FR", "C", "LFE", "SL", "SR"]).is_none());
        assert!(FoldTo51::new(&["FL", "FR"]).is_none());
        assert!(FoldTo51::new(&["FL", "FR", "C", "LFE", "SL", "SR", "BL", "BR"]).is_some());
    }

    #[test]
    fn heights_land_on_their_base_layer_and_backs_on_the_surrounds() {
        let bed = bed();
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        let mut folded = Vec::new();
        FoldTo51::new(&NAMES)
            .expect("7.1.4 folds")
            .apply(&refs, 8, &mut folded);

        assert_eq!(folded.len(), FOLD_51_CHANNELS.len());
        for i in 0..8 {
            assert!(
                (folded[0][i] - (bed[0][i] + K * bed[8][i])).abs() < 1e-12,
                "FL"
            );
            assert!(
                (folded[1][i] - (bed[1][i] + K * bed[9][i])).abs() < 1e-12,
                "FR"
            );
            assert!((folded[2][i] - bed[2][i]).abs() < 1e-12, "C");
            let sl = bed[4][i] + K * bed[6][i] + K * bed[10][i];
            assert!((folded[3][i] - sl).abs() < 1e-12, "SL");
            let sr = bed[5][i] + K * bed[7][i] + K * bed[11][i];
            assert!((folded[4][i] - sr).abs() < 1e-12, "SR");
        }
    }

    #[test]
    fn folding_block_by_block_matches_folding_the_whole_programme() {
        let bed = bed();
        let refs: Vec<&[f64]> = bed.iter().map(|c| c.as_slice()).collect();
        let fold = FoldTo51::new(&NAMES).expect("7.1.4 folds");
        let mut whole = Vec::new();
        fold.apply(&refs, 8, &mut whole);

        let mut blocked = vec![Vec::new(); FOLD_51_CHANNELS.len()];
        let mut block = Vec::new();
        for start in (0..8).step_by(3) {
            let end = (start + 3).min(8);
            let slices: Vec<&[f64]> = bed.iter().map(|c| &c[start..end]).collect();
            fold.apply(&slices, end - start, &mut block);
            for (out, part) in blocked.iter_mut().zip(block.iter()) {
                out.extend_from_slice(part);
            }
        }
        assert_eq!(blocked, whole);
    }
}
