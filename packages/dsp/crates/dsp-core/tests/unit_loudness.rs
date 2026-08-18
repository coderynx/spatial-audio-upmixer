mod loudness {
    use upmixer_dsp_core::loudness::*;

    #[test]
    fn k_weighting_is_exact_at_48k() {
        let sos = k_weighting_sos(48_000);
        assert_eq!(sos[0], K_STAGE1_48K);
        assert_eq!(sos[1], K_STAGE2_48K);
    }

    #[test]
    fn retargeting_48k_onto_itself_is_a_fixed_point() {
        for section in [K_STAGE1_48K, K_STAGE2_48K] {
            let back = retarget_biquad(section, 48_000);
            for (a, b) in back.iter().zip(section.iter()) {
                assert!((a - b).abs() < 1e-12, "{a} vs {b}");
            }
        }
    }

    #[test]
    fn silence_reports_the_absolute_gate() {
        let silence = vec![0.0; 48_000];
        assert_eq!(measure_integrated_loudness(&[(1.0, &silence)], 48_000), ABS_GATE);
    }

    #[test]
    fn true_peak_never_reads_below_the_sample_peak() {
        let sine: Vec<f64> = (0..4096)
            .map(|i| (i as f64 * 0.37).sin())
            .collect();
        let sample_peak = sine.iter().fold(0.0_f64, |m, v| m.max(v.abs()));
        assert!(true_peak_channel(&sine) >= sample_peak - 1e-12);
    }

    #[test]
    fn true_peak_tracks_gain_exactly() {
        let sine: Vec<f64> = (0..4096).map(|i| 0.5 * (i as f64 * 0.11).sin()).collect();
        let loud: Vec<f64> = sine.iter().map(|v| v * 2.0).collect();
        let delta = measure_true_peak(&[&loud]) - measure_true_peak(&[&sine]);
        assert!((delta - 20.0 * 2.0_f64.log10()).abs() < 1e-12, "{delta} dB");
    }
}

mod loudness_stream {
    use upmixer_dsp_core::loudness::ABS_GATE;
    use upmixer_dsp_core::loudness_stream::*;
    use upmixer_dsp_core::loudness::{measure_integrated_loudness, measure_true_peak};

    fn programme(n: usize, seed: f64) -> Vec<f64> {
        (0..n)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                let env = if i % 97_000 < 20_000 { 0.02 } else { 1.0 };
                env * (0.4 * (2.0 * std::f64::consts::PI * (110.0 + seed) * t).sin()
                    + 0.2 * (2.0 * std::f64::consts::PI * 947.0 * t + seed).sin()
                    + 0.05 * (2.0 * std::f64::consts::PI * 6100.0 * t).sin())
            })
            .collect()
    }

    /// The whole point of the streaming meters: identical numbers, delivered
    /// in slices small enough for one audio callback.
    #[test]
    fn measuring_in_slices_equals_the_offline_meters() {
        let sr = 48_000;
        let left = programme(240_000, 0.0);
        let right = programme(240_000, 1.7);
        let weights = [1.0, 1.0];

        let want_lkfs = measure_integrated_loudness(&[(1.0, &left), (1.0, &right)], sr);
        let want_dbtp = measure_true_peak(&[&left, &right]);

        for slice in [128usize, 512, 4096, 7000] {
            let mut loudness = IntegratedLoudnessMeter::new(&weights, sr);
            let mut peaks = [TruePeakMeter::new(), TruePeakMeter::new()];
            let mut at = 0;
            while at < left.len() {
                let stop = (at + slice).min(left.len());
                loudness.push(&[&left[at..stop], &right[at..stop]]);
                peaks[0].push(&left[at..stop]);
                peaks[1].push(&right[at..stop]);
                at = stop;
            }
            let got_lkfs = loudness.finish();
            let got_dbtp = true_peak_dbtp(&[peaks[0].finish(), peaks[1].finish()]);

            assert_eq!(got_lkfs, want_lkfs, "slice {slice}: LKFS");
            assert_eq!(got_dbtp, want_dbtp, "slice {slice}: dBTP");
        }
    }

    #[test]
    fn a_zero_weight_channel_is_dropped_like_the_offline_pass() {
        let sr = 48_000;
        let bed = programme(96_000, 0.0);
        let lfe = programme(96_000, 5.0);
        let want = measure_integrated_loudness(&[(1.0, &bed), (0.0, &lfe)], sr);

        let mut meter = IntegratedLoudnessMeter::new(&[1.0, 0.0], sr);
        let mut at = 0;
        while at < bed.len() {
            let stop = (at + 1024).min(bed.len());
            meter.push(&[&bed[at..stop], &lfe[at..stop]]);
            at = stop;
        }
        assert_eq!(meter.finish(), want);
    }

    #[test]
    fn silence_reports_the_absolute_gate() {
        let silence = vec![0.0; 48_000];
        let mut meter = IntegratedLoudnessMeter::new(&[1.0], 48_000);
        meter.push(&[&silence]);
        assert_eq!(meter.finish(), ABS_GATE);
    }
}
