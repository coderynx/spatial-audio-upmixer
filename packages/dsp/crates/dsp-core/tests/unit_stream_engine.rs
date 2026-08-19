mod engine {
    use std::sync::Arc;
    use upmixer_dsp_core::stream::engine::*;
    use upmixer_dsp_core::stream::params::EngineParams;

    fn engine(mute_lfe: bool) -> PreviewEngine {
        let params: EngineParams = serde_json::from_str(&format!(
            r#"{{
                "speakers": [
                    {{"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [1.0, 0.0]}},
                    {{"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 1.0]}},
                    {{"name": "LFE", "azimuth_rad": 0.0, "elevation_rad": 0.0,
                     "group_gain": 1.0, "muted": {mute_lfe}, "downmix": [0.0, 0.0]}}
                ],
                "lfe_index": 2,
                "shapes": ["left", "right", "mono"],
                "sends": {{"surround_bass_cutoff_hz": 250.0,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "height_directional_band_hz": 8000.0,
                          "height_directional_band_gain": 1.0,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 1.0}},
                "stems": [{{"routing": [["FL", 0.9], ["FR", 0.9], ["LFE", 1.0]], "rebalance_db": 0.0,
                           "enabled": true, "eq_fir": [], "route_scale": 1.0}}],
                "master": {{}},
                "output_mode": "native",
                "bypass_mastering": true,
                "soft_limit_threshold": 0.0
            }}"#
        ))
        .expect("engine parameters");

        let tone: Vec<f32> = (0..4096)
            .map(|i| (0.4 * (2.0 * std::f64::consts::PI * 60.0 * i as f64 / 48_000.0).sin()) as f32)
            .collect();
        PreviewEngine::new(48_000, params, vec![Arc::new(StemSource { left: tone.clone(), right: tone })])
    }

    fn probe_engine(mute_fl: bool) -> PreviewEngine {
        let params: EngineParams = serde_json::from_str(&format!(
            r#"{{
                "speakers": [
                    {{"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0,
                     "group_gain": 1.0, "muted": {mute_fl}, "downmix": [1.0, 0.0]}},
                    {{"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 1.0]}},
                    {{"name": "SL", "azimuth_rad": 1.9, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.3, 0.0]}},
                    {{"name": "SR", "azimuth_rad": -1.9, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 0.3]}},
                    {{"name": "LFE", "azimuth_rad": 0.0, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 0.0]}}
                ],
                "lfe_index": 4,
                "shapes": ["left", "right", "left", "right", "mono"],
                "sends": {{"surround_bass_cutoff_hz": 250.0,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "height_directional_band_hz": 8000.0,
                          "height_directional_band_gain": 1.0,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 1.0}},
                "stems": [{{"routing": [["FL", 0.7], ["FR", 0.7], ["SL", 0.5], ["SR", 0.5], ["LFE", 0.5]],
                           "rebalance_db": 0.0, "enabled": true, "eq_fir": [], "route_scale": 1.0}}],
                "master": {{"bass": {{"sub_gain_db": 0.0, "mid_gain_db": 0.0, "unify_hz": 120.0,
                            "punch": 0.0, "excite": false, "lfe_gain_db": 0.0,
                            "sub_cutoff_hz": 60.0, "mid_cutoff_hz": 200.0,
                            "excite_blend": 0.0, "excite_drive": 0.0,
                            "punch_fast_ms": 5.0, "punch_slow_ms": 50.0, "punch_max_db": 0.0,
                            "decorrelate": 0.0, "decorr_low_hz": 60.0, "decorr_high_hz": 200.0,
                            "decorr_sections": 1, "decorr_max_delay_ms": 5.0,
                            "decorr_fast_ms": 5.0, "decorr_slow_ms": 50.0}},
                          "lf_targets": [[0, 0.5], [1, 0.5]]}},
                "output_mode": "native",
                "soft_limit_threshold": 0.0
            }}"#,
        ))
        .expect("engine parameters");

        let tone: Vec<f32> = (0..8192)
            .map(|i| (0.4 * (2.0 * std::f64::consts::PI * 80.0 * i as f64 / 48_000.0).sin()) as f32)
            .collect();
        PreviewEngine::new(48_000, params, vec![Arc::new(StemSource { left: tone.clone(), right: tone })])
    }

    /// Speaker mute is a monitor control, so it has to be silent *and* inert:
    /// carrying it as a routing gain took the channel out of the shared bass
    /// pool and the linked compressor's detector, so muting `FL` quietly
    /// changed every other speaker's low end.
    #[test]
    fn muting_a_speaker_silences_it_without_touching_any_other_channel() {
        let mut muted_out = vec![0.0; 5 * 8192];
        probe_engine(true).render(&mut muted_out, 8192);
        let mut open_out = vec![0.0; 5 * 8192];
        probe_engine(false).render(&mut open_out, 8192);

        assert!(
            muted_out[0..8192].iter().all(|v| *v == 0.0),
            "muted FL should stay silent through the LF fold",
        );
        for channel in 1..5 {
            let span = channel * 8192..(channel + 1) * 8192;
            assert_eq!(
                muted_out[span.clone()],
                open_out[span],
                "channel {channel} changed when FL was muted",
            );
        }
    }

    #[test]
    fn muting_the_lfe_speaker_silences_its_bus() {
        let mut muted = engine(true);
        let mut out = vec![0.0; 3 * 4096];
        muted.render(&mut out, 4096);
        let lfe = &out[2 * 4096..3 * 4096];
        assert!(lfe.iter().all(|v| *v == 0.0), "muted LFE bus should be silent");

        let mut unmuted = engine(false);
        let mut out = vec![0.0; 3 * 4096];
        unmuted.render(&mut out, 4096);
        let lfe = &out[2 * 4096..3 * 4096];
        assert!(lfe.iter().any(|v| v.abs() > 1e-6), "unmuted LFE bus should carry signal");
    }
}

mod master_meters {
    use std::sync::Arc;
    use upmixer_dsp_core::loudness::measure_loudness_stats;
    use upmixer_dsp_core::stream::engine::*;
    use upmixer_dsp_core::stream::params::EngineParams;

    /// A hot stereo programme through a compressor and a limiter, so both
    /// gain-reduction taps have something to report.
    fn engine(frames: usize, level: f64) -> PreviewEngine {
        let params: EngineParams = serde_json::from_str(
            r#"{
                "speakers": [
                    {"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [1.0, 0.0]},
                    {"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 1.0]},
                    {"name": "LFE", "azimuth_rad": 0.0, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 0.0]}
                ],
                "lfe_index": 2,
                "shapes": ["left", "right", "mono"],
                "sends": {"surround_bass_cutoff_hz": 250.0,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "height_directional_band_hz": 8000.0,
                          "height_directional_band_gain": 1.0,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 3.0},
                "stems": [{"routing": [["FL", 1.0], ["FR", 1.0], ["LFE", 1.0]],
                           "rebalance_db": 0.0, "enabled": true, "eq_fir": [], "route_scale": 1.0}],
                "master": {"compressor": {"threshold_db": -30.0, "ratio": 4.0, "attack_ms": 5.0,
                                          "release_ms": 80.0, "knee_db": 6.0, "makeup_db": 12.0,
                                          "sidechain_hpf_hz": null},
                           "limiter": {"ceiling_dbtp": -1.0, "lookahead_ms": 1.5,
                                       "release_ms": 50.0, "safety_margin_db": 0.1}},
                "output_mode": "native",
                "meter_weights": [1.0, 1.0, 0.0],
                "soft_limit_threshold": 0.0
            }"#,
        )
        .expect("engine parameters");

        // Two tones: the 220 Hz one drives the mains, the 60 Hz one survives
        // the LFE bus's 120 Hz low-pass so the LFE curve has a peak of its own.
        let tone: Vec<f32> = (0..frames)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                let half = level * 0.5;
                (half * (2.0 * std::f64::consts::PI * 220.0 * t).sin()
                    + half * (2.0 * std::f64::consts::PI * 60.0 * t).sin()) as f32
            })
            .collect();
        PreviewEngine::new(
            48_000,
            params,
            vec![Arc::new(StemSource { left: tone.clone(), right: tone })],
        )
    }

    fn render(engine: &mut PreviewEngine, frames: usize) -> Vec<Vec<f64>> {
        let block = 128;
        let mut out = vec![0.0; 3 * block];
        let mut collected = vec![Vec::new(); 3];
        let mut done = 0;
        while done < frames {
            let written = engine.render(&mut out, block);
            if written == 0 {
                break;
            }
            for (channel, sink) in collected.iter_mut().enumerate() {
                sink.extend_from_slice(&out[channel * block..channel * block + written]);
            }
            done += written;
        }
        collected
    }

    /// The live windows read the programme that was actually emitted: after a
    /// whole render their short-term reading is the offline kit's last 3 s.
    #[test]
    fn the_loudness_windows_read_the_emitted_programme() {
        let mut engine = engine(240_000, 0.2);
        let out = render(&mut engine, 240_000);
        let tail = out[0].len().saturating_sub(3 * 48_000);
        let want = measure_loudness_stats(
            &[(1.0, &out[0][tail..]), (1.0, &out[1][tail..])],
            48_000,
        );
        let got = engine.meters().master.short_term_lkfs;
        assert!(
            (got - want.max_short_term_lkfs).abs() < 0.1,
            "short-term {got} vs offline {}",
            want.max_short_term_lkfs,
        );
    }

    /// Both gain-reduction taps report on a programme hot enough to work
    /// them, and a quiet one leaves every stage at rest.
    #[test]
    fn the_gain_reduction_taps_follow_the_stages() {
        let mut hot = engine(96_000, 0.9);
        render(&mut hot, 96_000);
        let master = hot.meters().master;
        assert!(master.comp_gr_db > 1.0, "compressor GR {}", master.comp_gr_db);
        assert!(master.limiter_gr_db > 0.0, "limiter GR {}", master.limiter_gr_db);
        assert!(master.limiter_lfe_gr_db > 0.0, "LFE GR {}", master.limiter_lfe_gr_db);

        let mut quiet = engine(96_000, 0.002);
        render(&mut quiet, 96_000);
        let master = quiet.meters().master;
        assert_eq!(master.comp_gr_db, 0.0);
        assert_eq!(master.limiter_gr_db, 0.0);
        assert_eq!(master.limiter_lfe_gr_db, 0.0);
        assert!(master.momentary_lkfs < -40.0, "momentary {}", master.momentary_lkfs);
    }
}

mod measure {
    use upmixer_dsp_core::stream::engine::PreviewEngine;
    use upmixer_dsp_core::stream::measure::*;
    use upmixer_dsp_core::stream::engine::StemSource;
    use upmixer_dsp_core::stream::params::EngineParams;
    use std::sync::Arc;

    fn engine(frames: usize) -> PreviewEngine {
        let params: EngineParams = serde_json::from_str(
            r#"{
                "speakers": [
                    {"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [1.0, 0.0]},
                    {"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 1.0]}
                ],
                "lfe_index": null,
                "shapes": ["left", "right"],
                "sends": {"surround_bass_cutoff_hz": 250.0,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "height_directional_band_hz": 8000.0,
                          "height_directional_band_gain": 1.0,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 0.316},
                "stems": [{"routing": [["FL", 0.9], ["FR", 0.9]], "rebalance_db": 0.0,
                           "enabled": true, "eq_fir": [], "route_scale": 1.0}],
                "master": {"lf_targets": [[0, 0.5], [1, 0.5]]},
                "output_mode": "stereo",
                "soft_limit_threshold": 0.0
            }"#,
        )
        .expect("engine parameters");

        let tone: Vec<f32> = (0..frames)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                (0.4 * (2.0 * std::f64::consts::PI * 220.0 * t).sin()
                    + 0.1 * (2.0 * std::f64::consts::PI * 3300.0 * t).sin()) as f32
            })
            .collect();
        PreviewEngine::new(
            48_000,
            params,
            vec![Arc::new(StemSource { left: tone.clone(), right: tone })],
        )
    }

    #[test]
    fn slicing_a_measurement_matches_the_blocking_one() {
        let mut reference = engine(120_000);
        let want = reference.measure(&[1.0, 1.0]);

        for slice in [128usize, 1024, 9000] {
            let live = engine(120_000);
            let mut pass = MeasurementPass::new(&live, &[1.0, 1.0]);
            let mut result = None;
            let mut guard = 0;
            while result.is_none() {
                result = pass.advance(slice);
                guard += 1;
                assert!(guard < 100_000, "slice {slice} never finished");
            }
            let (lkfs, dbtp) = result.expect("measured");
            assert!((lkfs - want.0).abs() < 1e-9, "slice {slice}: {lkfs} vs {want:?}");
            assert!((dbtp - want.1).abs() < 1e-9, "slice {slice}: {dbtp} vs {want:?}");
        }
    }

    #[test]
    fn measuring_leaves_the_live_transport_alone() {
        let mut live = engine(48_000);
        let mut out = vec![0.0; 2 * 4096];
        live.render(&mut out, 4096);
        let before = live.position();

        let mut pass = MeasurementPass::new(&live, &[1.0, 1.0]);
        while pass.advance(4096).is_none() {}

        assert_eq!(live.position(), before);
        let mut next = vec![0.0; 2 * 4096];
        assert_eq!(live.render(&mut next, 4096), 4096);
    }

    #[test]
    fn progress_climbs_to_one() {
        let live = engine(48_000);
        let mut pass = MeasurementPass::new(&live, &[1.0, 1.0]);
        assert_eq!(pass.progress(), 0.0);
        pass.advance(4096);
        let partial = pass.progress();
        assert!(partial > 0.0 && partial < 1.0, "{partial}");
        while pass.advance(4096).is_none() {}
        assert_eq!(pass.progress(), 1.0);
    }

    fn run(pass: &mut MeasurementPass, slice: usize) -> (f64, f64) {
        let mut result = None;
        let mut guard = 0;
        while result.is_none() {
            result = pass.advance(slice);
            guard += 1;
            assert!(guard < 100_000, "never finished");
        }
        result.expect("measured")
    }

    #[test]
    fn an_excerpt_plan_spanning_the_whole_programme_matches_the_blocking_measurement() {
        let mut reference = engine(120_000);
        let want = reference.measure(&[1.0, 1.0]);

        let live = engine(120_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 1, 120_000, 0);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1e-9, "{lkfs} vs {want:?}");
        assert!((dbtp - want.1).abs() < 1e-9, "{dbtp} vs {want:?}");
    }

    #[test]
    fn a_sparse_excerpt_plan_lands_close_to_the_whole_programme_measurement() {
        let mut reference = engine(480_000);
        let want = reference.measure(&[1.0, 1.0]);

        let live = engine(480_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 5, 20_000, 2_000);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1.0, "{lkfs} vs {want:?}");
        assert!((dbtp - want.1).abs() < 1.0, "{dbtp} vs {want:?}");
    }

    #[test]
    fn excerpt_progress_climbs_to_one() {
        let live = engine(480_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 5, 20_000, 2_000);
        assert_eq!(pass.progress(), 0.0);
        pass.advance(1024);
        let partial = pass.progress();
        assert!(partial > 0.0 && partial < 1.0, "{partial}");
        run(&mut pass, 1024);
        assert_eq!(pass.progress(), 1.0);
    }

    /// A native bed with a height pair, so the measurement programme is the
    /// 5.1 re-render rather than the delivered channels.
    fn immersive_engine(frames: usize) -> PreviewEngine {
        let params: EngineParams = serde_json::from_str(
            r#"{
                "speakers": [
                    {"name": "FL", "azimuth_rad": 0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [1.0, 0.0]},
                    {"name": "FR", "azimuth_rad": -0.5236, "elevation_rad": 0.0, "group_gain": 1.0,
                     "downmix": [0.0, 1.0]},
                    {"name": "TFL", "azimuth_rad": 0.7854, "elevation_rad": 0.7854,
                     "group_gain": 1.0, "downmix": [0.7071067811865476, 0.0]},
                    {"name": "TFR", "azimuth_rad": -0.7854, "elevation_rad": 0.7854,
                     "group_gain": 1.0, "downmix": [0.0, 0.7071067811865476]}
                ],
                "lfe_index": null,
                "shapes": ["left", "right", "height_left", "height_right"],
                "sends": {"surround_bass_cutoff_hz": 250.0,
                          "height_low_rolloff_hz": 150.0, "height_low_rolloff_gain": 0.15,
                          "height_crossover_hz": 3000.0, "height_high_shelf_gain": 1.5,
                          "height_directional_band_hz": 8000.0,
                          "height_directional_band_gain": 1.0,
                          "lfe_cutoff_hz": 120.0, "lfe_filter_order": 4, "lfe_gain": 0.316},
                "stems": [{"routing": [["FL", 0.7], ["FR", 0.7], ["TFL", 0.6], ["TFR", 0.6]],
                           "rebalance_db": 0.0, "enabled": true, "eq_fir": [],
                           "route_scale": 1.0}],
                "master": {"lf_targets": [[0, 0.5], [1, 0.5]]},
                "output_mode": "native",
                "soft_limit_threshold": 0.0
            }"#,
        )
        .expect("engine parameters");

        let tone: Vec<f32> = (0..frames)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                (0.4 * (2.0 * std::f64::consts::PI * 220.0 * t).sin()
                    + 0.1 * (2.0 * std::f64::consts::PI * 3300.0 * t).sin()) as f32
            })
            .collect();
        PreviewEngine::new(
            48_000,
            params,
            vec![Arc::new(StemSource { left: tone.clone(), right: tone })],
        )
    }

    #[test]
    fn an_immersive_bed_measures_its_five_one_re_render() {
        let mut reference = immersive_engine(120_000);
        assert!(reference.measurement_fold().is_some(), "a height pair must fold");
        let want = reference.measure(&[]);

        let live = immersive_engine(120_000);
        let mut pass = MeasurementPass::new(&live, &[]);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1e-9, "sliced fold {lkfs} vs blocking {want:?}");
        assert!((dbtp - want.1).abs() < 1e-9, "sliced peak {dbtp} vs blocking {want:?}");

        // The heights sum into the fronts, so the folded programme is louder
        // than the same bed measured as four unity-weighted channels.
        let mut unfolded = engine(120_000);
        let flat = unfolded.measure(&[1.0, 1.0]);
        assert!(lkfs > -70.0 && (lkfs - flat.0).abs() > 0.1, "fold changed nothing: {lkfs}");
    }

    #[test]
    fn a_short_programme_falls_back_to_a_single_excerpt() {
        let mut reference = engine(48_000);
        let want = reference.measure(&[1.0, 1.0]);

        let live = engine(48_000);
        let mut pass = MeasurementPass::new_excerpts(&live, &[1.0, 1.0], 5, 20_000, 2_000);
        let (lkfs, dbtp) = run(&mut pass, 1024);
        assert!((lkfs - want.0).abs() < 1e-9, "{lkfs} vs {want:?}");
        assert!((dbtp - want.1).abs() < 1e-9, "{dbtp} vs {want:?}");
    }
}

mod routing {
    use upmixer_dsp_core::kernels::butter::{butter_sos, linkwitz_riley_lowpass_sos, BandType};
    use upmixer_dsp_core::routing::decorrelate::{velvet_pair_seeded, VELVET_SEED, VELVET_SEED_HEIGHT};
    use upmixer_dsp_core::stream::params::SendParams;
    use upmixer_dsp_core::stream::routing::*;

    fn send_params() -> SendParams {
        SendParams {
            surround_bass_cutoff_hz: 250.0,
            height_low_rolloff_hz: 150.0,
            height_low_rolloff_gain: 0.15,
            height_crossover_hz: 3000.0,
            height_high_shelf_gain: 1.5,
            height_directional_band_hz: 8000.0,
            height_directional_band_gain: 1.0,
            stem_transient_duck: 0.0,
            lfe_cutoff_hz: 120.0,
            lfe_filter_order: 4,
            lfe_gain: 0.31622776601683794,
        }
    }

    /// Run a signal through both channels of a route in uneven blocks and
    /// return the four shaped sends, concatenated.
    fn blocked(state: &mut StemRouteState, signal: &[f64]) -> [Vec<f64>; 4] {
        let mut out: [Vec<f64>; 4] = Default::default();
        let mut rest = signal;
        for size in [333usize, 999, 128].iter().cycle() {
            if rest.is_empty() {
                break;
            }
            let n = (*size).min(rest.len());
            state.process(&rest[..n], &rest[..n], true, true);
            for (index, side) in out.iter_mut().enumerate() {
                side.extend_from_slice(state.send(index));
            }
            rest = &rest[n..];
        }
        out
    }

    #[test]
    fn surround_sends_match_the_offline_highpass_and_velvet_pair() {
        use upmixer_dsp_core::kernels::biquad::sosfilt;

        let sr = 48_000;
        let signal: Vec<f64> = (0..9600).map(|i| (i as f64 * 0.04).sin()).collect();
        let p = send_params();

        // Blocked in ragged sizes: the shaped sends must not depend on how
        // the render callback happens to chop the stream up.
        let mut state = StemRouteState::new(sr, &p, &[]);
        let got = blocked(&mut state, &signal);

        let hp = butter_sos(2, p.surround_bass_cutoff_hz / (sr as f64 / 2.0), BandType::High);
        let shaped = sosfilt(&hp, &signal);
        let (left, right) = velvet_pair_seeded(sr, VELVET_SEED);

        for (index, fir) in [(3, left), (4, right)] {
            let want = fir.process(&shaped);
            for (i, (a, b)) in got[index - 3].iter().zip(want.iter()).enumerate() {
                assert!((a - b).abs() < 1e-12, "shape {index} sample {i}: {a} vs {b}");
            }
        }
    }

    #[test]
    fn height_sends_match_the_offline_elevation_eq_and_velvet_pair() {
        use upmixer_dsp_core::routing::sends::elevation_eq;

        let sr = 48_000;
        let signal: Vec<f64> = (0..9600).map(|i| (i as f64 * 0.07).sin()).collect();

        // The default skips the band section, a lifted band runs it.
        for band_gain in [1.0, 1.6] {
            let p = SendParams { height_directional_band_gain: band_gain, ..send_params() };

            let mut state = StemRouteState::new(sr, &p, &[]);
            let got = blocked(&mut state, &signal);

            let shaped = elevation_eq(
                &signal, sr, p.height_low_rolloff_hz, p.height_low_rolloff_gain,
                p.height_crossover_hz, p.height_high_shelf_gain,
                p.height_directional_band_hz, p.height_directional_band_gain,
            );
            let (left, right) = velvet_pair_seeded(sr, VELVET_SEED_HEIGHT);

            for (index, fir) in [(5, left), (6, right)] {
                let want = fir.process(&shaped);
                for (i, (a, b)) in got[index - 3].iter().zip(want.iter()).enumerate() {
                    assert!((a - b).abs() < 1e-12, "band {band_gain} shape {index} sample {i}: {a} vs {b}");
                }
            }
        }
    }

    /// The duck must land on the send input, before the filters and the
    /// velvet line, exactly as `StemRouter.route` orders it offline — and
    /// blocked ragged, since the preview chooses the block size.
    #[test]
    fn ducked_sends_match_the_offline_duck_then_shape_order() {
        use upmixer_dsp_core::kernels::biquad::sosfilt;
        use upmixer_dsp_core::routing::transient::transient_duck;

        let sr = 48_000;
        let signal: Vec<f64> = (0..24_000)
            .map(|i| {
                let bed = 0.2 * (i as f64 * 0.04).sin();
                bed + if i % 6_000 < 24 { 0.8 } else { 0.0 }
            })
            .collect();
        let p = SendParams { stem_transient_duck: 0.7, ..send_params() };

        let mut state = StemRouteState::new(sr, &p, &[]);
        let got = blocked(&mut state, &signal);

        let (ducked, _) = transient_duck(&signal, &signal, sr, p.stem_transient_duck);
        let hp = butter_sos(2, p.surround_bass_cutoff_hz / (sr as f64 / 2.0), BandType::High);
        let shaped = sosfilt(&hp, &ducked);
        let (left, right) = velvet_pair_seeded(sr, VELVET_SEED);

        for (index, fir) in [(0, left), (1, right)] {
            let want = fir.process(&shaped);
            for (i, (a, b)) in got[index].iter().zip(want.iter()).enumerate() {
                assert!((a - b).abs() < 1e-12, "send {index} sample {i}: {a} vs {b}");
            }
        }
    }

    /// Depth 0.0 must leave the shaped sends untouched, so every existing
    /// render is bit for bit what it was.
    #[test]
    fn zero_duck_depth_leaves_the_sends_bit_for_bit() {
        let sr = 48_000;
        let signal: Vec<f64> = (0..12_000).map(|i| (i as f64 * 0.04).sin()).collect();

        let mut off = StemRouteState::new(sr, &send_params(), &[]);
        let want = blocked(&mut off, &signal);
        let mut explicit = StemRouteState::new(
            sr,
            &SendParams { stem_transient_duck: 0.0, ..send_params() },
            &[],
        );
        let got = blocked(&mut explicit, &signal);
        assert_eq!(got, want);
    }

    /// The trace the duck display reads has to cover the block whatever the
    /// depth, and has to actually move when a transient lands.
    #[test]
    fn the_duck_trace_covers_the_block_and_dips_on_a_transient() {
        let sr = 48_000;
        // A decaying strike ~30 dB over a quiet bed, spaced past the 250 ms
        // reference — see `routing::transient`'s `hit_train_over_bed` for why
        // a weaker or closer-spaced stimulus never reaches the threshold.
        let signal: Vec<f64> = (0..48_000)
            .map(|i| {
                let t = i as f64 / sr as f64;
                let bed = 0.03 * (2.0 * std::f64::consts::PI * 220.0 * t).sin();
                let phase = i % 24_000;
                let hit = if phase < 1_440 {
                    0.9 * (-(phase as f64) / 240.0).exp()
                        * (2.0 * std::f64::consts::PI * 1_800.0 * t).sin()
                } else {
                    0.0
                };
                bed + hit
            })
            .collect();

        let mut off = StemRouteState::new(sr, &send_params(), &[]);
        off.process(&signal, &signal, true, true);
        assert_eq!(off.duck_trace(), vec![1.0; signal.len()]);

        let p = SendParams { stem_transient_duck: 0.7, ..send_params() };
        let mut on = StemRouteState::new(sr, &p, &[]);
        on.process(&signal, &signal, true, true);
        let trace = on.duck_trace();
        assert_eq!(trace.len(), signal.len());
        // Past the second hit, so the cold-start sample — where the reference
        // envelope is still zero and anything saturates — cannot carry this.
        let deepest = trace[24_000..].iter().cloned().fold(f64::INFINITY, f64::min);
        assert!(deepest < 0.5, "trace barely dipped: {deepest}");
        assert!(deepest >= 1.0 - p.stem_transient_duck - 1e-12, "trace {deepest} below depth");
    }

    /// The surround and height sends of one stem must not be copies of each
    /// other: they run different seeds, so a stem placed both around and
    /// overhead does not image as one hard phantom between the two.
    #[test]
    fn surround_and_height_sends_use_different_tap_sets() {
        let (surround, _) = velvet_pair_seeded(48_000, VELVET_SEED);
        let (height, _) = velvet_pair_seeded(48_000, VELVET_SEED_HEIGHT);
        assert_ne!(surround.taps(), height.taps());
    }

    #[test]
    fn lfe_bus_matches_the_offline_lowpass_and_gain() {
        use upmixer_dsp_core::kernels::biquad::sosfilt;

        let sr = 48_000;
        let signal: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.02).sin()).collect();
        let p = send_params();

        let mut bus = LfeBus::new(sr, &p);
        let got: Vec<f64> = signal.iter().map(|v| bus.tick(*v)).collect();

        let lp = linkwitz_riley_lowpass_sos(p.lfe_filter_order, p.lfe_cutoff_hz / (sr as f64 / 2.0));
        let want: Vec<f64> = sosfilt(&lp, &signal).iter().map(|v| v * p.lfe_gain).collect();

        for (a, b) in got.iter().zip(want.iter()) {
            assert!((a - b).abs() < 1e-12);
        }
    }
}
