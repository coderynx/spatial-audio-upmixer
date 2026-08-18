mod state {
    use upmixer_dsp_core::stream::state::*;
    use upmixer_dsp_core::kernels::biquad::lfilter;

    #[test]
    fn one_pole_matches_the_offline_lfilter() {
        let x: Vec<f64> = (0..2000).map(|i| (i as f64 * 0.03).sin()).collect();
        let sr = 48_000.0;
        let ms = 20.0;
        let mut p = OnePole::new(ms, sr);
        let got: Vec<f64> = x.iter().map(|v| p.tick(*v)).collect();

        let alpha = 1.0 - (-(1.0 / sr) / (ms / 1000.0)).exp();
        let want = lfilter(&[alpha], &[1.0, -(1.0 - alpha)], &x);
        for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
            assert!((a - b).abs() < 1e-13, "sample {i}: {a} vs {b}");
        }
    }

}

mod meters {
    use upmixer_dsp_core::stream::meters::*;

    #[test]
    fn a_full_scale_square_reads_unity_rms_and_peak() {
        let level = Level::measure(&[1.0, -1.0, 1.0, -1.0]);
        assert!((level.rms - 1.0).abs() < 1e-15);
        assert!((level.peak - 1.0).abs() < 1e-15);
    }

    #[test]
    fn gain_scales_the_f32_measurement() {
        let quiet = Level::measure_f32(&[0.5, -0.5], 1.0);
        let loud = Level::measure_f32(&[0.5, -0.5], 2.0);
        assert!((loud.rms - quiet.rms * 2.0).abs() < 1e-12);
        assert!((loud.peak - 1.0).abs() < 1e-12);
    }

    #[test]
    fn silence_reads_zero_rather_than_nan() {
        let level = Level::measure(&[]);
        assert_eq!(level.rms, 0.0);
        assert_eq!(level.peak, 0.0);
    }

    #[test]
    fn the_flat_block_is_stems_then_channels_then_output() {
        let meters = Meters {
            stems: vec![[Level { rms: 0.1, peak: 0.2 }, Level { rms: 0.15, peak: 0.25 }]],
            channels: vec![Level { rms: 0.3, peak: 0.4 }],
            output: [Level { rms: 0.5, peak: 0.6 }, Level { rms: 0.7, peak: 0.8 }],
        };
        let mut out = vec![0.0_f32; meters.len()];
        assert_eq!(meters.write(&mut out), 10);
        assert_eq!(out, vec![0.1, 0.2, 0.15, 0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8]);
    }
}

mod output {
    use upmixer_dsp_core::spatial::voicing::VoicingParams;
    use upmixer_dsp_core::stream::output::*;
    use upmixer_dsp_core::spatial::voicing::apply_voicing;

    #[test]
    fn streaming_voicing_matches_the_offline_chain() {
        let sr = 48_000;
        let p = VoicingParams {
            crossfeed_amount: 0.25,
            crossfeed_cutoff_hz: 700.0,
            bass_shelf_hz: 120.0,
            bass_shelf_gain_db: 2.0,
            air_shelf_hz: 9000.0,
            air_shelf_gain_db: 1.5,
            presence_hz: 3000.0,
            presence_gain_db: 1.0,
            presence_q: 1.2,
            stereo_widen: 0.3,
        };
        let left: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.05).sin() * 0.4).collect();
        let right: Vec<f64> = (0..4800).map(|i| (i as f64 * 0.07).cos() * 0.4).collect();

        let (want_l, want_r) = apply_voicing(&left, &right, sr, &p);
        let mut streaming = StreamingVoicing::new(sr, p);
        for (i, (l, r)) in left.iter().zip(right.iter()).enumerate() {
            let (got_l, got_r) = streaming.tick(*l, *r);
            assert!((got_l - want_l[i]).abs() < 1e-12, "left sample {i}");
            assert!((got_r - want_r[i]).abs() < 1e-12, "right sample {i}");
        }
    }
}

mod conv {
    use upmixer_dsp_core::stream::conv::*;
    use upmixer_dsp_core::kernels::fft::fftconvolve;

    fn kernel(n: usize) -> Vec<f64> {
        (0..n).map(|i| (i as f64 * 0.37).sin() / (1.0 + i as f64)).collect()
    }

    #[test]
    fn streaming_in_blocks_matches_the_offline_convolution() {
        let signal: Vec<f64> = (0..5000).map(|i| (i as f64 * 0.021).sin()).collect();
        let k = kernel(257);

        let mut offline = fftconvolve(&signal, &k);
        offline.truncate(signal.len());

        for block_size in [1usize, 128, 333, 1024] {
            let mut conv = StreamingConvolver::new(k.clone());
            let mut got = Vec::with_capacity(signal.len());
            for chunk in signal.chunks(block_size) {
                got.extend(conv.process(chunk));
            }
            assert_eq!(got.len(), offline.len());
            for (i, (a, b)) in got.iter().zip(offline.iter()).enumerate() {
                assert!(
                    (a - b).abs() < 1e-9,
                    "block {block_size}, sample {i}: {a} vs {b}"
                );
            }
        }
    }

    /// The worklet renders at 128 frames, but a seek runs its pre-roll at
    /// 4096 and `measure` at 8192; the delay line has to survive the switch.
    #[test]
    fn a_hop_change_mid_stream_stays_exact() {
        let signal: Vec<f64> = (0..12_000).map(|i| (i as f64 * 0.013).cos()).collect();
        let k = kernel(2049);
        let mut offline = fftconvolve(&signal, &k);
        offline.truncate(signal.len());

        let mut conv = StreamingConvolver::new(k);
        let mut got = Vec::with_capacity(signal.len());
        let mut at = 0;
        for hop in [4096usize, 128, 128, 512, 128] {
            let stop = (at + hop).min(signal.len());
            got.extend(conv.process(&signal[at..stop]));
            at = stop;
        }
        got.extend(conv.process(&signal[at..]));

        for (i, (a, b)) in got.iter().zip(offline.iter()).enumerate() {
            assert!((a - b).abs() < 1e-9, "sample {i}: {a} vs {b}");
        }
    }

    #[test]
    fn an_impulse_streams_out_the_whole_kernel() {
        let k = kernel(64);
        let mut conv = StreamingConvolver::new(k.clone());
        let mut impulse = vec![0.0; 256];
        impulse[0] = 1.0;
        let mut got = Vec::new();
        for chunk in impulse.chunks(64) {
            got.extend(conv.process(chunk));
        }
        for (i, &h) in k.iter().enumerate() {
            assert!((got[i] - h).abs() < 1e-12, "tap {i}");
        }
    }

    #[test]
    fn resetting_clears_the_tail() {
        let mut conv = StreamingConvolver::new(kernel(32));
        conv.process(&vec![1.0; 64]);
        conv.reset();
        let out = conv.process(&vec![0.0; 64]);
        assert!(out.iter().all(|v| *v == 0.0));
    }
}

mod band {
    use upmixer_dsp_core::stream::band::*;
    use upmixer_dsp_core::kernels::butter::{butter_bandpass_sos, butter_sos, BandType};
    use upmixer_dsp_core::kernels::filtfilt::sosfiltfilt;

    fn signal(n: usize) -> Vec<f64> {
        (0..n)
            .map(|i| {
                let t = i as f64 / 48_000.0;
                0.7 + (2.0 * std::f64::consts::PI * 180.0 * t).sin()
                    + 0.4 * (2.0 * std::f64::consts::PI * 2200.0 * t).sin()
            })
            .collect()
    }

    /// Drive the band the way the engine does: a growing source queue, one
    /// render quantum of output at a time.
    fn rolled(sections: Vec<[f64; 6]>, ahead: usize, chunk: usize, x: &[f64], block: usize) -> Vec<f64> {
        let total = x.len();
        let mut band = RollingBand::new(sections, ahead, chunk, vec![0], 0);
        let mut out = Vec::with_capacity(total);
        let mut start = 0;
        while start < total {
            let end = (start + block).min(total);
            let filled = (end + ahead + 2 * chunk).min(total);
            let source = vec![x[..filled].to_vec()];
            band.advance(&source, 0, total, start, end);
            out.extend_from_slice(band.band(0, start, end));
            start = end;
        }
        out
    }

    #[test]
    fn it_reproduces_the_offline_zero_phase_pass() {
        let x = signal(48_000);
        let sections = butter_bandpass_sos(4, 100.0 / 24_000.0, 300.0 / 24_000.0);
        let offline = sosfiltfilt(&sections, &x).expect("signal is long enough");
        let got = rolled(sections, 14_400, 4_800, &x, 128);

        assert_eq!(got.len(), offline.len());
        for (i, (a, b)) in got.iter().zip(offline.iter()).enumerate() {
            assert!((a - b).abs() < 1e-9, "sample {i}: {a} vs {b}");
        }
    }

    #[test]
    fn the_result_does_not_depend_on_the_block_size() {
        let x = signal(48_000);
        let sections = butter_sos(2, 120.0 / 24_000.0, BandType::Low);
        let small = rolled(sections.clone(), 4_800, 2_048, &x, 128);
        let large = rolled(sections, 4_800, 2_048, &x, 4_096);
        for (i, (a, b)) in small.iter().zip(large.iter()).enumerate() {
            assert!((a - b).abs() < 1e-12, "sample {i}: {a} vs {b}");
        }
    }

    /// The point of the slicing: no single call may carry a whole warm-up.
    #[test]
    fn the_warm_up_is_spread_across_the_calls_that_consume_a_chunk() {
        let (ahead, chunk, block) = (14_400usize, 4_800usize, 128usize);
        let x = signal(48_000);
        let sections = butter_bandpass_sos(4, 100.0 / 24_000.0, 300.0 / 24_000.0);
        let mut band = RollingBand::new(sections, ahead, chunk, vec![0], 0);

        // Past the cold start, which pays for the first chunk up front.
        let owed = block * band.per_frame();
        let mut start = 0;
        let mut worst = 0;
        while start + block <= x.len() {
            let end = start + block;
            let filled = (end + ahead + 2 * chunk).min(x.len());
            let source = vec![x[..filled].to_vec()];
            let before = (band.chunk_start, band.cursor);
            band.advance(&source, 0, x.len(), start, end);
            if start > 0 {
                // A call that lands on a chunk boundary finishes the one in
                // flight and opens the next, so it can owe two slices.
                let done = match (before.1, band.cursor) {
                    (Some(was), Some(now)) if now <= was => was - now,
                    (Some(was), _) => (was - before.0) + owed,
                    _ => owed,
                };
                worst = worst.max(done);
            }
            start = end;
        }
        assert!(worst <= 2 * owed, "a call did {worst} samples of warm-up, budget {}", 2 * owed);
    }
}
