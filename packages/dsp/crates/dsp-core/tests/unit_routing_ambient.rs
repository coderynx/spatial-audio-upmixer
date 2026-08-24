mod common;

mod ambient {
    use super::common;
    use upmixer_dsp_core::kernels::rng::next_unit;
    use upmixer_dsp_core::routing::ambient::*;

    const SR: u32 = 48_000;
    const N: usize = 48_000;

    fn noise(seed: u64, n: usize) -> Vec<f64> {
        let mut state = seed;
        (0..n).map(|_| next_unit(&mut state) * 2.0 - 1.0).collect()
    }

    fn tone(freq: f64, n: usize) -> Vec<f64> {
        (0..n)
            .map(|i| {
                let t = i as f64 / SR as f64;
                0.5 * (2.0 * std::f64::consts::PI * freq * t).sin()
            })
            .collect()
    }

    /// Ambient rear+height pairs over the whole signal, in `block` steps.
    fn split_all(left: &[f64], right: &[f64], block: usize) -> [Vec<f64>; 4] {
        let mut split = AmbientSplit::new(SR);
        let mut out: [Vec<f64>; 4] = Default::default();
        let mut start = 0;
        while start < left.len() {
            let len = block.min(left.len() - start);
            let piece = split.advance(0, left, right, start, len);
            out[0].extend_from_slice(piece.rear[0]);
            out[1].extend_from_slice(piece.rear[1]);
            out[2].extend_from_slice(piece.height[0]);
            out[3].extend_from_slice(piece.height[1]);
            start += len;
        }
        out
    }

    /// Mean power over the settled part, past the overlap-add ramp-in.
    fn power(signal: &[f64]) -> f64 {
        let settled = &signal[AMBIENT_FFT_SIZE..];
        settled.iter().map(|v| v * v).sum::<f64>() / settled.len() as f64
    }

    #[test]
    fn a_perfectly_correlated_pair_stays_direct() {
        let mono = tone(440.0, N);
        let split = split_all(&mono, &mono, 512);
        let source: f64 = mono[AMBIENT_FFT_SIZE..]
            .iter()
            .map(|v| v * v)
            .sum::<f64>()
            / (N - AMBIENT_FFT_SIZE) as f64;
        let ambient = power(&split[0]) + power(&split[2]);
        assert!(
            ambient / source < 1e-6,
            "correlated input leaked {:.4} of its power",
            ambient / source
        );
    }

    #[test]
    fn an_uncorrelated_pair_reaches_the_ambient_send() {
        let left = noise(1, N);
        let right = noise(2, N);
        let split = split_all(&left, &right, 512);
        let source: f64 = left[AMBIENT_FFT_SIZE..]
            .iter()
            .map(|v| v * v)
            .sum::<f64>()
            / (N - AMBIENT_FFT_SIZE) as f64;
        let ambient = power(&split[0]) + power(&split[2]);
        assert!(
            ambient / source > 0.6,
            "uncorrelated input kept only {:.3} of its power",
            ambient / source
        );
    }

    #[test]
    fn a_hard_panned_primary_is_not_mistaken_for_ambient() {
        let left = tone(440.0, N);
        let right = vec![0.0; N];
        let split = split_all(&left, &right, 512);
        let source: f64 = left[AMBIENT_FFT_SIZE..]
            .iter()
            .map(|v| v * v)
            .sum::<f64>()
            / (N - AMBIENT_FFT_SIZE) as f64;
        let ambient = power(&split[0]) + power(&split[2]);
        assert!(
            ambient / source < 1e-3,
            "a hard-panned tone sent {:.5} of its power to the ambient bus",
            ambient / source
        );
    }

    #[test]
    fn a_phase_shifted_primary_stays_direct() {
        let left = tone(880.0, N);
        let mut right = vec![0.0; N];
        right[16..].copy_from_slice(&left[..N - 16]);
        let split = split_all(&left, &right, 512);
        let ambient = power(&split[0]) + power(&split[2]);
        assert!(ambient / power(&left) < 1e-4, "delayed primary leaked {ambient:.5}");
    }

    #[test]
    fn the_tilt_pair_is_power_complementary() {
        let left = noise(3, N);
        let right = noise(4, N);
        let split = split_all(&left, &right, 512);
        // The matched first-order pair preserves the untilted ambient power.
        for (rear, height) in [(&split[0], &split[2]), (&split[1], &split[3])] {
            let summed: Vec<f64> = rear.iter().zip(height).map(|(a, b)| a + b).collect();
            let sum_power = power(&summed);
            let split_power = power(rear) + power(height);
            let ratio = sum_power / split_power;
            assert!(
                (ratio - 1.0).abs() < 0.02,
                "tilt pair is not power complementary: {ratio:.4}"
            );
        }
    }

    #[test]
    fn the_split_is_independent_of_the_block_size() {
        let left = noise(5, N);
        let right = noise(6, N);
        let reference = split_all(&left, &right, N);
        for block in [128, 512, 4096] {
            let got = split_all(&left, &right, block);
            for (signal, want) in got.iter().zip(reference.iter()) {
                let worst = signal
                    .iter()
                    .zip(want)
                    .map(|(a, b)| (a - b).abs())
                    .fold(0.0_f64, f64::max);
                assert!(worst < 1e-9, "block {block} diverged by {worst:e}");
            }
        }
    }

    #[test]
    fn a_decaying_tail_reaches_the_send_and_the_note_under_it_does_not() {
        // What the stage exists for: a centred note with a decorrelated tail
        // behind it, the shape a separated stem actually has.
        let tail_l = noise(9, N);
        let tail_r = noise(10, N);
        let mut left = vec![0.0; N];
        let mut right = vec![0.0; N];
        let note = tone(660.0, N);
        for i in 0..N {
            let t = i as f64 / SR as f64;
            let note_gain = if t < 0.5 { 1.0 } else { 0.0 };
            let tail_gain = 0.2 * (-3.0 * t).exp();
            left[i] = note[i] * note_gain + tail_l[i] * tail_gain;
            right[i] = note[i] * note_gain + tail_r[i] * tail_gain;
        }
        let split = split_all(&left, &right, 512);
        let window = |signal: &[f64], from: usize, to: usize| {
            signal[from..to].iter().map(|v| v * v).sum::<f64>() / (to - from) as f64
        };
        let source = |signal: &[f64], from: usize, to: usize| {
            signal[from..to].iter().map(|v| v * v).sum::<f64>() / (to - from) as f64
        };
        let (note_from, note_to) = (SR as usize / 4, SR as usize / 2);
        let (tail_from, tail_to) = (SR as usize * 3 / 4, N);
        let under_note = (window(&split[0], note_from, note_to)
            + window(&split[2], note_from, note_to))
            / source(&left, note_from, note_to);
        let under_tail = (window(&split[0], tail_from, tail_to)
            + window(&split[2], tail_from, tail_to))
            / source(&left, tail_from, tail_to);
        assert!(
            under_tail > 20.0 * under_note,
            "tail {under_tail:.4} vs note {under_note:.4} — the split does not separate them"
        );
    }

    /// Broad-band matrix regularization must not create musical-noise motion.
    #[test]
    fn the_matrix_does_not_perforate_the_spectrum() {
        // Dense spectrum, so every bin has something to measure: a shared
        // (coherent) noise bed under an independent (diffuse) one.
        let common = noise(21, N);
        let (diffuse_l, diffuse_r) = (noise(22, N), noise(23, N));
        let left: Vec<f64> = (0..N).map(|i| common[i] + 0.5 * diffuse_l[i]).collect();
        let right: Vec<f64> = (0..N).map(|i| common[i] + 0.5 * diffuse_r[i]).collect();

        let split = split_all(&left, &right, 512);
        let ambient_l: Vec<f64> = split[0].iter().zip(&split[2]).map(|(a, b)| a + b).collect();
        let ambient_r: Vec<f64> = split[1].iter().zip(&split[3]).map(|(a, b)| a + b).collect();

        let (roughness, movement) = gain_statistics([&left, &right], [&ambient_l, &ambient_r]);
        assert!(roughness < 2.5, "matrix jumps {:.2} dB between neighbouring bands", roughness);
        assert!(movement < 2.0, "mask moves {:.2} dB over time, per bin", movement);
    }

    #[test]
    fn subtracting_the_ambient_send_keeps_the_direct_path_smooth() {
        let common = noise(31, N);
        let (diffuse_l, diffuse_r) = (noise(32, N), noise(33, N));
        let left: Vec<f64> = (0..N).map(|i| common[i] + 0.5 * diffuse_l[i]).collect();
        let right: Vec<f64> = (0..N).map(|i| common[i] + 0.5 * diffuse_r[i]).collect();
        let split = split_all(&left, &right, 512);
        let direct_l: Vec<f64> = left
            .iter()
            .zip(&split[0])
            .zip(&split[2])
            .map(|((source, rear), height)| source - rear - height)
            .collect();
        let direct_r: Vec<f64> = right
            .iter()
            .zip(&split[1])
            .zip(&split[3])
            .map(|((source, rear), height)| source - rear - height)
            .collect();

        let (roughness, movement) = gain_statistics([&left, &right], [&direct_l, &direct_r]);
        assert!(roughness < 1.5, "direct path jumps {:.2} dB between bins", roughness);
        assert!(movement < 3.0, "direct path moves {:.2} dB over time", movement);
    }

    fn gain_statistics(source: [&[f64]; 2], ambient: [&[f64]; 2]) -> (f64, f64) {
        use upmixer_dsp_core::kernels::fft::RealFft;
        use upmixer_dsp_core::kernels::stft::hann_periodic;

        let n = AMBIENT_FFT_SIZE;
        let hop = n / 2;
        let fft = RealFft::new(n);
        let window = hann_periodic(n);
        let bins = n / 2 + 1;
        let (lo, hi) = (bins / 32, bins * 2 / 3);
        let mut tracks: Vec<Vec<f64>> = Vec::new();
        let mut start = n;
        while start + n <= source[0].len() {
            let take = |signal: &[f64]| -> Vec<f64> {
                let framed: Vec<f64> =
                    (0..n).map(|i| signal[start + i] * window[i]).collect();
                fft.rfft(&framed).iter().map(|v| v.norm()).collect()
            };
            let source_l = take(source[0]);
            let source_r = take(source[1]);
            let ambient_l = take(ambient[0]);
            let ambient_r = take(ambient[1]);
            tracks.push(
                (lo..hi)
                    .step_by(12)
                    .map(|start| {
                        let end = (start + 12).min(hi);
                        let source_power = (start..end)
                            .map(|bin| source_l[bin].powi(2) + source_r[bin].powi(2))
                            .sum::<f64>();
                        let ambient_power = (start..end)
                            .map(|bin| ambient_l[bin].powi(2) + ambient_r[bin].powi(2))
                            .sum::<f64>();
                        10.0 * (ambient_power / source_power.max(1e-18)).max(1e-8).log10()
                    })
                    .collect(),
            );
            start += hop;
        }
        let roughness = tracks
            .iter()
            .map(|frame| {
                frame.windows(2).map(|w| (w[1] - w[0]).abs()).sum::<f64>()
                    / (frame.len() - 1) as f64
            })
            .sum::<f64>()
            / tracks.len() as f64;
        let width = tracks[0].len();
        let movement = (0..width)
            .map(|bin| {
                let column: Vec<f64> = tracks.iter().map(|frame| frame[bin]).collect();
                let mean = column.iter().sum::<f64>() / column.len() as f64;
                (column.iter().map(|v| (v - mean).powi(2)).sum::<f64>() / column.len() as f64)
                    .sqrt()
            })
            .sum::<f64>()
            / width as f64;
        (roughness, movement)
    }

    /// The same pin `packages/core/tests/test_ambient_split.py` asserts
    /// through the wheel: a wasm preview and a Python export built from
    /// different splits fail here.
    #[test]
    fn the_split_matches_the_pinned_samples() {
        const PROBES: [usize; 3] = [2048, 4096, 6144];
        const PINNED: [[f64; 3]; 4] = [
            [0.00949903053205785, -0.02137585394233137, 0.015521223284353647],
            [0.012354056080600629, -0.01844941093216676, 0.007393748905077962],
            [-0.0005359773449861317, 0.0002232876237728415, 0.00038587992947320196],
            [-0.00029197729661793563, -0.00020581934326996051, 0.0005281452483678123],
        ];
        let left = common::deterministic_signal(9600, SR, 0.0);
        let right = common::deterministic_signal(9600, SR, 1.0);
        let got = split_all(&left, &right, 512);
        // split_all's order is rear L/R then height L/R.
        for (signal, want) in [&got[0], &got[1], &got[2], &got[3]].into_iter().zip(PINNED) {
            for (probe, expected) in PROBES.into_iter().zip(want) {
                assert_eq!(signal[probe], expected, "sample {probe}");
            }
        }
    }

    #[test]
    fn a_reset_split_repeats_itself() {
        let left = noise(7, 8192);
        let right = noise(8, 8192);
        let mut split = AmbientSplit::new(SR);
        let first: Vec<f64> = split.advance(0, &left, &right, 0, 4096).rear[0].to_vec();
        split.reset();
        let second: Vec<f64> = split.advance(0, &left, &right, 0, 4096).rear[0].to_vec();
        assert_eq!(first, second);
    }
}
