use super::*;

fn policy() -> StemCleanupPolicy {
    StemCleanupPolicy {
        relative_energy_floor: 1e-8,
        relative_leakage_floor: 0.05,
        coherence_floor: 0.8,
        dominance_ratio: 4.0,
        transfer_cap: 0.25,
    }
}

fn run(
    parent: &[Vec<f64>; 2],
    a: &[Vec<f64>; 2],
    b: &[Vec<f64>; 2],
    chunks: &[usize],
) -> [Vec<f64>; 4] {
    let mut cleanup = StemCleanup::new(48_000, policy()).unwrap();
    let mut out = std::array::from_fn(|_| Vec::new());
    let mut start = 0;
    for &size in chunks {
        let end = (start + size).min(parent[0].len());
        if start == end {
            break;
        }
        let (oa, ob) = cleanup
            .process(
                StereoBlock {
                    left: &parent[0][start..end],
                    right: &parent[1][start..end],
                },
                StereoBlock {
                    left: &a[0][start..end],
                    right: &a[1][start..end],
                },
                StereoBlock {
                    left: &b[0][start..end],
                    right: &b[1][start..end],
                },
            )
            .unwrap();
        out[0].extend(oa.left);
        out[1].extend(oa.right);
        out[2].extend(ob.left);
        out[3].extend(ob.right);
        start = end;
    }
    if start < parent[0].len() {
        let (oa, ob) = cleanup
            .process(
                StereoBlock {
                    left: &parent[0][start..],
                    right: &parent[1][start..],
                },
                StereoBlock {
                    left: &a[0][start..],
                    right: &a[1][start..],
                },
                StereoBlock {
                    left: &b[0][start..],
                    right: &b[1][start..],
                },
            )
            .unwrap();
        out[0].extend(oa.left);
        out[1].extend(oa.right);
        out[2].extend(ob.left);
        out[3].extend(ob.right);
    }
    let (oa, ob) = cleanup.flush().unwrap();
    out[0].extend(oa.left);
    out[1].extend(oa.right);
    out[2].extend(ob.left);
    out[3].extend(ob.right);
    out
}

fn assert_complementary(parent: &[Vec<f64>; 2], out: &[Vec<f64>; 4]) {
    for side in 0..2 {
        for (index, expected) in parent[side].iter().enumerate() {
            let actual = out[side][STEM_CLEANUP_LATENCY + index]
                + out[side + 2][STEM_CLEANUP_LATENCY + index];
            assert!(
                (actual - expected).abs() < 1e-12,
                "{side}:{index}: {actual} != {expected}"
            );
        }
    }
}

#[test]
fn silence_exact_inputs_and_hard_pans_stay_complementary() {
    let n = 3_000;
    let mut a = [vec![0.0; n], vec![0.0; n]];
    let mut b = [vec![0.0; n], vec![0.0; n]];
    for index in 0..n {
        a[0][index] = (index as f64 * 0.03).sin();
        b[1][index] = (index as f64 * 0.07).cos() * 0.25;
    }
    let parent = [
        a[0].iter().zip(&b[0]).map(|(x, y)| x + y).collect(),
        a[1].iter().zip(&b[1]).map(|(x, y)| x + y).collect(),
    ];
    let out = run(&parent, &a, &b, &[73, 511, 911]);
    assert_complementary(&parent, &out);
    assert!(out.iter().flatten().all(|sample| sample.is_finite()));
}

#[test]
fn duplicated_mono_correlated_leakage_transfers_and_unrelated_tones_do_not() {
    let n = 4_096;
    let tone = |frequency: f64, index: usize| {
        (2.0 * std::f64::consts::PI * frequency * index as f64 / 48_000.0).sin()
    };
    let a = [
        (0..n).map(|i| tone(468.75, i)).collect::<Vec<_>>(),
        (0..n).map(|i| tone(468.75, i)).collect::<Vec<_>>(),
    ];
    let b = [
        (0..n).map(|i| tone(468.75, i) * 0.3).collect::<Vec<_>>(),
        (0..n).map(|i| tone(468.75, i) * 0.3).collect::<Vec<_>>(),
    ];
    let parent = [
        a[0].iter().zip(&b[0]).map(|(x, y)| x + y).collect(),
        a[1].iter().zip(&b[1]).map(|(x, y)| x + y).collect(),
    ];
    let leaked = run(&parent, &a, &b, &[1_024, 1_024, 1_024, 1_024]);
    assert_complementary(&parent, &leaked);
    let changed = leaked[2][STEM_CLEANUP_LATENCY..]
        .iter()
        .zip(&b[0])
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f64::max);
    assert!(changed > 1e-5);

    let unrelated = [
        (0..n).map(|i| tone(1_171.875, i) * 0.2).collect::<Vec<_>>(),
        (0..n).map(|i| tone(1_171.875, i) * 0.2).collect::<Vec<_>>(),
    ];
    let parent = [
        a[0].iter().zip(&unrelated[0]).map(|(x, y)| x + y).collect(),
        a[1].iter().zip(&unrelated[1]).map(|(x, y)| x + y).collect(),
    ];
    let out = run(&parent, &a, &unrelated, &[2_000, 2_096]);
    let delta = out[2][STEM_CLEANUP_LATENCY..]
        .iter()
        .zip(&unrelated[0])
        .map(|(x, y)| (x - y).abs())
        .fold(0.0, f64::max);
    assert!(delta < 0.01, "unrelated tone transfer was {delta}");
}

#[test]
fn impulses_low_energy_partitioning_and_scratch_are_safe() {
    let n = 5_000;
    let mut a = [vec![0.0; n], vec![0.0; n]];
    a[0][1_237] = 1.0;
    a[1][1_237] = 1.0;
    let b = [vec![1e-16; n], vec![1e-16; n]];
    let parent = [
        a[0].iter().zip(&b[0]).map(|(x, y)| x + y).collect(),
        a[1].iter().zip(&b[1]).map(|(x, y)| x + y).collect(),
    ];
    let one = run(&parent, &a, &b, &[n]);
    let split = run(&parent, &a, &b, &[17, 389, 2_047, 991]);
    assert_eq!(one, split);
    assert_complementary(&parent, &one);

    let mut cleanup = StemCleanup::new(44_100, policy()).unwrap();
    let scratch = cleanup.scratch_samples();
    for _ in 0..200 {
        cleanup
            .process(
                StereoBlock {
                    left: &a[0],
                    right: &a[1],
                },
                StereoBlock {
                    left: &a[0],
                    right: &a[1],
                },
                StereoBlock {
                    left: &b[0],
                    right: &b[1],
                },
            )
            .unwrap();
    }
    assert_eq!(cleanup.scratch_samples(), scratch);
}

#[test]
fn validates_shapes_nonfinite_rates_and_flush() {
    assert!(StemCleanup::new(8_000, policy()).is_ok());
    assert!(StemCleanup::new(44_100, policy()).is_ok());
    assert!(StemCleanup::new(96_000, policy()).is_ok());
    assert!(matches!(
        StemCleanup::new(7_999, policy()),
        Err(StemCleanupError::UnsupportedSampleRate)
    ));
    assert!(matches!(
        StemCleanup::new(
            48_000,
            StemCleanupPolicy {
                transfer_cap: 2.0,
                ..policy()
            }
        ),
        Err(StemCleanupError::InvalidPolicy)
    ));
    let mut cleanup = StemCleanup::new(48_000, policy()).unwrap();
    let finite = [0.0; 2];
    assert_eq!(
        cleanup.process(
            StereoBlock {
                left: &finite,
                right: &finite[..1]
            },
            StereoBlock {
                left: &finite,
                right: &finite
            },
            StereoBlock {
                left: &finite,
                right: &finite
            },
        ),
        Err(StemCleanupError::LengthMismatch)
    );
    let nan = [f64::NAN];
    assert_eq!(
        cleanup.process(
            StereoBlock {
                left: &nan,
                right: &nan
            },
            StereoBlock {
                left: &nan,
                right: &nan
            },
            StereoBlock {
                left: &nan,
                right: &nan
            },
        ),
        Err(StemCleanupError::NonFinite)
    );
    cleanup.flush().unwrap();
    assert_eq!(cleanup.flush(), Err(StemCleanupError::AlreadyFlushed));
}
