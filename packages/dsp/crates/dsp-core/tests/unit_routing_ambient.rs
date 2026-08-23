mod ambient {
    use upmixer_dsp_core::kernels::rng::next_unit;
    use upmixer_dsp_core::routing::ambient::*;

    const SR: u32 = 48_000;
    const N: usize = 48_000;

    fn noise(seed: u64, n: usize) -> Vec<f32> {
        let mut state = seed;
        (0..n).map(|_| (next_unit(&mut state) * 2.0 - 1.0) as f32).collect()
    }

    fn tone(freq: f64, n: usize) -> Vec<f32> {
        (0..n)
            .map(|i| {
                let t = i as f64 / SR as f64;
                (0.5 * (2.0 * std::f64::consts::PI * freq * t).sin()) as f32
            })
            .collect()
    }

    /// Ambient rear+height pairs over the whole signal, in `block` steps.
    fn split_all(left: &[f32], right: &[f32], block: usize) -> [Vec<f64>; 4] {
        let mut split = AmbientSplit::new(SR);
        let mut out: [Vec<f64>; 4] = Default::default();
        let mut start = 0;
        while start < left.len() {
            let len = block.min(left.len() - start);
            let piece = split.advance(left, right, start, len);
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
    fn a_perfectly_correlated_pair_yields_only_the_mask_floor() {
        let mono = tone(440.0, N);
        let split = split_all(&mono, &mono, 512);
        let source: f64 = mono[AMBIENT_FFT_SIZE..]
            .iter()
            .map(|v| (*v as f64) * (*v as f64))
            .sum::<f64>()
            / (N - AMBIENT_FFT_SIZE) as f64;
        let ambient = power(&split[0]) + power(&split[2]);
        // The floor is an amplitude gain, so the power it leaves is its square.
        assert!(
            ambient / source < 2.0 * AMBIENCE_FLOOR * AMBIENCE_FLOOR,
            "correlated input leaked {:.4} of its power",
            ambient / source
        );
    }

    #[test]
    fn an_uncorrelated_pair_passes_through_near_the_mask_ceiling() {
        let left = noise(1, N);
        let right = noise(2, N);
        let split = split_all(&left, &right, 512);
        let source: f64 = left[AMBIENT_FFT_SIZE..]
            .iter()
            .map(|v| (*v as f64) * (*v as f64))
            .sum::<f64>()
            / (N - AMBIENT_FFT_SIZE) as f64;
        let ambient = power(&split[0]) + power(&split[2]);
        assert!(
            ambient / source > 0.7,
            "uncorrelated input kept only {:.3} of its power",
            ambient / source
        );
    }

    #[test]
    fn a_hard_panned_primary_is_not_mistaken_for_ambient() {
        let left = tone(440.0, N);
        let right = vec![0.0f32; N];
        let split = split_all(&left, &right, 512);
        let source: f64 = left[AMBIENT_FFT_SIZE..]
            .iter()
            .map(|v| (*v as f64) * (*v as f64))
            .sum::<f64>()
            / (N - AMBIENT_FFT_SIZE) as f64;
        // Coherence alone reads this as ambience; the equal-energy guard is
        // the only thing that keeps it out of the surrounds.
        let ambient = power(&split[0]) + power(&split[2]);
        assert!(
            ambient / source < 1e-3,
            "a hard-panned tone sent {:.5} of its power to the ambient bus",
            ambient / source
        );
    }

    #[test]
    fn the_tilt_pair_is_power_complementary() {
        let left = noise(3, N);
        let right = noise(4, N);
        let split = split_all(&left, &right, 512);
        // rear + height is the untilted ambient run through a first-order
        // allpass, so their power sums back to it band by band.
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
        let mut left = vec![0.0f32; N];
        let mut right = vec![0.0f32; N];
        let note = tone(660.0, N);
        for i in 0..N {
            let t = i as f64 / SR as f64;
            let note_gain = if t < 0.5 { 1.0 } else { 0.0 };
            let tail_gain = 0.2 * (-3.0 * t).exp();
            left[i] = (note[i] as f64 * note_gain + tail_l[i] as f64 * tail_gain) as f32;
            right[i] = (note[i] as f64 * note_gain + tail_r[i] as f64 * tail_gain) as f32;
        }
        let split = split_all(&left, &right, 512);
        let window = |signal: &[f64], from: usize, to: usize| {
            signal[from..to].iter().map(|v| v * v).sum::<f64>() / (to - from) as f64
        };
        let source = |signal: &[f32], from: usize, to: usize| {
            signal[from..to]
                .iter()
                .map(|v| (*v as f64) * (*v as f64))
                .sum::<f64>()
                / (to - from) as f64
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

    #[test]
    fn a_reset_split_repeats_itself() {
        let left = noise(7, 8192);
        let right = noise(8, 8192);
        let mut split = AmbientSplit::new(SR);
        let first: Vec<f64> = split.advance(&left, &right, 0, 4096).rear[0].to_vec();
        split.reset();
        let second: Vec<f64> = split.advance(&left, &right, 0, 4096).rear[0].to_vec();
        assert_eq!(first, second);
    }
}
