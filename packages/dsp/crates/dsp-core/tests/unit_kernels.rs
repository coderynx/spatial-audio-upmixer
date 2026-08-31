mod rng {
    use upmixer_dsp_core::kernels::rng::*;

    #[test]
    fn draws_are_in_range_and_reproducible() {
        let mut a = 12345;
        let mut b = 12345;
        for _ in 0..1000 {
            let x = next_unit(&mut a);
            assert!((0.0..1.0).contains(&x));
            assert_eq!(x, next_unit(&mut b));
        }
        assert!(next_sign(&mut a).abs() == 1.0);
    }
}

mod sum {
    use upmixer_dsp_core::kernels::sum::*;

    #[test]
    fn pairwise_beats_naive_on_a_long_constant_run() {
        let values = vec![0.1_f64; 1_000_000];
        let exact = 100_000.0_f64;
        let naive = values.iter().fold(0.0, |a: f64, v| a + v);
        assert!((pairwise_sum(&values) - exact).abs() <= (naive - exact).abs());
    }

    #[test]
    fn rms_of_unit_dc_is_one() {
        assert!((rms(&vec![1.0; 1000]) - 1.0).abs() < 1e-15);
    }
}

mod upfirdn {
    use upmixer_dsp_core::kernels::upfirdn::*;

    #[test]
    fn impulse_reproduces_the_kernel() {
        let fir = [0.5, 1.0, -0.25];
        let out = upfirdn_up(&fir, &[1.0], 4);
        assert_eq!(out.len(), 3);
        for (a, b) in out.iter().zip(fir.iter()) {
            assert!((a - b).abs() < 1e-15);
        }
    }

    #[test]
    fn output_length_matches_scipy_formula() {
        let fir = vec![0.0; 48];
        let x = vec![0.0; 100];
        assert_eq!(upfirdn_up(&fir, &x, 4).len(), 99 * 4 + 48);
    }
}

mod biquad {
    use upmixer_dsp_core::kernels::biquad::*;

    #[test]
    fn one_pole_lfilter_matches_closed_form() {
        let alpha = 0.25;
        let b = [alpha];
        let a = [1.0, -(1.0 - alpha)];
        let x = [1.0, 1.0, 1.0, 1.0];
        let y = lfilter(&b, &a, &x);
        let mut expect = 0.0;
        for (i, v) in y.iter().enumerate() {
            expect = alpha * 1.0 + (1.0 - alpha) * expect;
            assert!((v - expect).abs() < 1e-15, "sample {i}");
        }
    }

    #[test]
    fn zi_holds_dc_steady_state() {
        // A filter seeded with its own step state must pass DC unchanged.
        let sos = [[0.2, 0.4, 0.2, 1.0, -0.3, 0.1]];
        let mut f = SosFilter::from_flat(&sos);
        f.set_step_state(1.0);
        let mut sig = vec![1.0; 8];
        f.process(&mut sig);
        let gain = Sos::new(sos[0]).dc_gain();
        for v in sig {
            assert!((v - gain).abs() < 1e-12);
        }
    }

    #[test]
    fn tiny_delay_registers_flush_to_zero() {
        let mut section = Sos::new([1.0, 0.0, 0.0, 1.0, 0.0, 0.0]);
        section.z = [1e-21, -1e-21];
        section.tick(0.0);
        assert_eq!(section.z, [0.0, 0.0]);
    }
}

mod butter {
    use upmixer_dsp_core::kernels::biquad::Sos;
    use upmixer_dsp_core::kernels::butter::*;

    #[test]
    fn lowpass_passes_dc_at_unity() {
        for order in [1usize, 2, 4] {
            let sos = butter_sos(order, 0.1, BandType::Low);
            let gain: f64 = sos.iter().map(|r| Sos::new(*r).dc_gain()).product();
            assert!((gain - 1.0).abs() < 1e-12, "order {order} DC gain {gain}");
        }
    }

    /// The Linkwitz-Riley signature: −6 dB at cutoff where Butterworth is −3.
    #[test]
    fn linkwitz_riley_is_half_amplitude_at_cutoff() {
        use upmixer_dsp_core::kernels::biquad::sosfilt;

        let (sr, cutoff) = (48_000.0f64, 120.0f64);
        let n = sr as usize;
        let sine: Vec<f64> = (0..n)
            .map(|i| (2.0 * std::f64::consts::PI * cutoff * i as f64 / sr).sin())
            .collect();
        let rms = |v: &[f64]| (v.iter().map(|x| x * x).sum::<f64>() / v.len() as f64).sqrt();

        for order in [2usize, 4, 8] {
            let lr = sosfilt(
                &linkwitz_riley_lowpass_sos(order, cutoff / (sr / 2.0)),
                &sine,
            );
            let bw = sosfilt(
                &butter_sos(order, cutoff / (sr / 2.0), BandType::Low),
                &sine,
            );
            let settled = n / 2;
            assert!((rms(&lr[settled..]) / rms(&sine[settled..]) - 0.5).abs() < 1e-3);
            assert!(
                (rms(&bw[settled..]) / rms(&sine[settled..]) - std::f64::consts::FRAC_1_SQRT_2)
                    .abs()
                    < 1e-3
            );
        }
    }

    #[test]
    fn highpass_blocks_dc() {
        for order in [1usize, 2, 4] {
            let sos = butter_sos(order, 0.1, BandType::High);
            let gain: f64 = sos.iter().map(|r| Sos::new(*r).dc_gain()).product();
            assert!(gain.abs() < 1e-12, "order {order} DC gain {gain}");
        }
    }
}

mod filtfilt {
    use upmixer_dsp_core::kernels::butter::{butter_sos, BandType};
    use upmixer_dsp_core::kernels::filtfilt::*;

    #[test]
    fn padlen_shrinks_for_first_order_sections() {
        let second = butter_sos(2, 0.1, BandType::Low);
        assert_eq!(default_padlen(&second), 9);
        let first = butter_sos(1, 0.1, BandType::Low);
        assert_eq!(default_padlen(&first), 6);
    }

    #[test]
    fn odd_ext_reflects_antisymmetrically() {
        let x = [1.0, 2.0, 4.0, 8.0];
        let e = odd_ext(&x, 2);
        assert_eq!(
            e,
            vec![
                2.0 * 1.0 - 4.0,
                2.0 * 1.0 - 2.0,
                1.0,
                2.0,
                4.0,
                8.0,
                2.0 * 8.0 - 4.0,
                2.0 * 8.0 - 2.0
            ]
        );
    }

    #[test]
    fn zero_phase_pass_preserves_dc() {
        let sos = butter_sos(2, 0.1, BandType::Low);
        let x = vec![1.0; 256];
        let y = sosfiltfilt(&sos, &x).expect("signal is longer than padlen");
        for v in y {
            assert!((v - 1.0).abs() < 1e-10);
        }
    }

    #[test]
    fn short_signals_report_no_result() {
        let sos = butter_sos(2, 0.1, BandType::Low);
        assert!(sosfiltfilt(&sos, &vec![1.0; 9]).is_none());
    }
}

mod minfilter {
    use upmixer_dsp_core::kernels::minfilter::*;

    fn brute(values: &[f64], size: usize, mode: BorderMode) -> Vec<f64> {
        let left = size / 2;
        let right = size - left - 1;
        let n = values.len();
        (0..n)
            .map(|i| {
                let mut m = f64::INFINITY;
                for d in 0..size {
                    let idx = i as i64 - left as i64 + d as i64;
                    let v = if idx < 0 || idx >= n as i64 {
                        match mode {
                            BorderMode::Nearest => {
                                if idx < 0 {
                                    values[0]
                                } else {
                                    values[n - 1]
                                }
                            }
                            BorderMode::Reflect => values[reflect_index(idx, n)],
                        }
                    } else {
                        values[idx as usize]
                    };
                    m = m.min(v);
                }
                let _ = right;
                m
            })
            .collect()
    }

    #[test]
    fn matches_brute_force_for_both_modes() {
        let values: Vec<f64> = (0..64).map(|i| ((i * 37) % 23) as f64 * 0.1).collect();
        for size in [3usize, 5, 9, 17] {
            for mode in [BorderMode::Reflect, BorderMode::Nearest] {
                let got = minimum_filter1d(&values, size, mode);
                let want = brute(&values, size, mode);
                assert_eq!(got.len(), want.len());
                for (i, (a, b)) in got.iter().zip(want.iter()).enumerate() {
                    assert!((a - b).abs() < 1e-15, "size {size} idx {i}: {a} vs {b}");
                }
            }
        }
    }

    #[test]
    fn sliding_min_tracks_trailing_window() {
        let values = [5.0, 3.0, 8.0, 1.0, 9.0, 2.0];
        let mut s = SlidingMin::new(3);
        let got: Vec<f64> = values.iter().map(|&v| s.push(v)).collect();
        assert_eq!(got, vec![5.0, 3.0, 3.0, 1.0, 1.0, 1.0]);
    }
}

mod stft {
    use upmixer_dsp_core::kernels::stft::*;

    #[test]
    fn hann_periodic_starts_at_zero_and_is_not_symmetric() {
        let w = hann_periodic(8);
        assert!(w[0].abs() < 1e-15);
        // The periodic form omits the closing zero, unlike the symmetric one.
        assert!(w[7] > 0.0);
    }

    #[test]
    fn a_full_scale_tone_lands_on_its_own_bin_at_unit_amplitude() {
        let n = 1024;
        let sr = 48_000;
        let bin = 64;
        let freq = sr as f64 * bin as f64 / n as f64;
        let signal: Vec<f64> = (0..n * 4)
            .map(|i| (2.0 * std::f64::consts::PI * freq * i as f64 / sr as f64).sin())
            .collect();
        let fp = frame_power(&signal, n);
        // "spectrum" scaling puts a unit-amplitude sine at 0.5 per side-bin.
        let amplitude = fp.at(bin, 1).sqrt();
        assert!((amplitude - 0.5).abs() < 1e-3, "amplitude {amplitude}");
    }

    #[test]
    fn short_signals_produce_no_frames_rather_than_raising() {
        let fp = frame_power(&[1.0, 2.0], 8);
        assert_eq!(fp.n_frames, 1, "nperseg is capped to the signal length");
    }
}

mod fft {
    use upmixer_dsp_core::kernels::fft::*;

    #[test]
    fn roundtrip_is_identity() {
        let fft = RealFft::new(64);
        let signal: Vec<f64> = (0..64).map(|i| (i as f64 * 0.37).sin()).collect();
        let back = fft.irfft(&fft.rfft(&signal));
        for (a, b) in signal.iter().zip(back.iter()) {
            assert!((a - b).abs() < 1e-13);
        }
    }

    #[test]
    fn fftconvolve_matches_direct_convolution() {
        let a: Vec<f64> = (0..200).map(|i| (i as f64 * 0.11).sin()).collect();
        let b: Vec<f64> = (0..64).map(|i| (i as f64 * 0.31).cos()).collect();
        let got = fftconvolve(&a, &b);
        let mut want = vec![0.0; a.len() + b.len() - 1];
        for (i, x) in a.iter().enumerate() {
            for (j, h) in b.iter().enumerate() {
                want[i + j] += x * h;
            }
        }
        assert_eq!(got.len(), want.len());
        for (i, (g, w)) in got.iter().zip(want.iter()).enumerate() {
            assert!((g - w).abs() < 1e-10, "tap {i}: {g} vs {w}");
        }
    }

    #[test]
    fn overlap_save_path_matches_the_single_transform_path() {
        // A kernel long enough that the output runs more than one block past
        // the end of the signal — the case that first broke this path.
        let signal: Vec<f64> = (0..20_000).map(|i| (i as f64 * 0.017).sin()).collect();
        let kernel: Vec<f64> = (0..2049)
            .map(|i| (i as f64 * 0.09).cos() / (1.0 + i as f64))
            .collect();
        let got = fftconvolve(&signal, &kernel);

        let out_len = signal.len() + kernel.len() - 1;
        let n = next_fast_len(out_len);
        let fft = RealFft::new(n);
        let sa = fft.rfft(&signal);
        let sb = fft.rfft(&kernel);
        let prod: Vec<_> = sa.iter().zip(sb.iter()).map(|(x, y)| x * y).collect();
        let mut want = fft.irfft(&prod);
        want.truncate(out_len);

        assert_eq!(got.len(), want.len());
        for (i, (g, w)) in got.iter().zip(want.iter()).enumerate() {
            assert!((g - w).abs() < 1e-10, "sample {i}: {g} vs {w}");
        }
    }

    #[test]
    fn next_fast_len_only_uses_small_radices() {
        for n in [7usize, 100, 1000, 1023, 4097] {
            let mut m = next_fast_len(n);
            assert!(m >= n);
            for p in [2usize, 3, 5] {
                while m % p == 0 {
                    m /= p;
                }
            }
            assert_eq!(m, 1, "next_fast_len({n}) is not 5-smooth");
        }
    }
}

mod fir_design {
    use upmixer_dsp_core::kernels::fft::RealFft;
    use upmixer_dsp_core::kernels::fir_design::*;

    #[test]
    fn hamming_is_symmetric_with_the_standard_endpoints() {
        let w = hamming(9);
        assert!((w[0] - 0.08).abs() < 1e-12);
        assert!((w[8] - 0.08).abs() < 1e-12);
        assert!((w[4] - 1.0).abs() < 1e-12);
        for i in 0..9 {
            assert!((w[i] - w[8 - i]).abs() < 1e-15);
        }
    }

    #[test]
    fn flat_response_designs_a_delta() {
        let taps = firwin2(65, &[0.0, 0.5, 1.0], &[1.0, 1.0, 1.0]);
        assert!((taps[32] - 1.0).abs() < 1e-6, "center tap {}", taps[32]);
        for (i, t) in taps.iter().enumerate() {
            if i != 32 {
                assert!(t.abs() < 1e-6, "tap {i} = {t}");
            }
        }
    }

    #[test]
    fn minimum_phase_preserves_magnitude_and_is_causal() {
        let linear = firwin2(255, &[0.0, 0.3, 0.6, 1.0], &[1.0, 1.5, 0.7, 1.0]);
        let minphase = minimum_phase(&linear);
        assert_eq!(minphase.len(), linear.len());

        // Energy is concentrated at the front for a minimum-phase filter.
        let head: f64 = minphase[..64].iter().map(|v| v * v).sum();
        let tail: f64 = minphase[64..].iter().map(|v| v * v).sum();
        assert!(head > tail * 10.0, "head {head} tail {tail}");

        let fft = RealFft::new(2048);
        let a: Vec<f64> = fft.rfft(&linear).iter().map(|c| c.norm()).collect();
        let b: Vec<f64> = fft.rfft(&minphase).iter().map(|c| c.norm()).collect();
        for (i, (x, y)) in a.iter().zip(b.iter()).enumerate() {
            assert!((x - y).abs() < 5e-3, "bin {i}: {x} vs {y}");
        }
    }
}
