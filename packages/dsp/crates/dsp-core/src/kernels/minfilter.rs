//! `scipy.ndimage.minimum_filter1d` plus the monotonic-deque sliding minimum
//! the streaming limiter reuses.

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BorderMode {
    /// SciPy's `"reflect"`: `d c b a | a b c d | d c b a` (edge duplicated).
    Reflect,
    /// SciPy's `"nearest"`: `a a a a | a b c d | d d d d`.
    Nearest,
}

fn pad(values: &[f64], left: usize, right: usize, mode: BorderMode) -> Vec<f64> {
    let n = values.len();
    let mut out = Vec::with_capacity(n + left + right);
    for i in 0..left {
        let d = left - i - 1;
        out.push(match mode {
            BorderMode::Nearest => values[0],
            BorderMode::Reflect => values[reflect_index(d as i64, n)],
        });
    }
    out.extend_from_slice(values);
    for d in 0..right {
        out.push(match mode {
            BorderMode::Nearest => values[n - 1],
            BorderMode::Reflect => values[reflect_index(n as i64 + d as i64, n)],
        });
    }
    out
}

/// Index into `[0, n)` under symmetric reflection, for arbitrary distance.
fn reflect_index(i: i64, n: usize) -> usize {
    if n == 1 {
        return 0;
    }
    let period = 2 * n as i64;
    let mut m = i.rem_euclid(period);
    if m >= n as i64 {
        m = period - 1 - m;
    }
    m as usize
}

/// Running minimum over a fixed window, monotonic-deque based (O(n)).
///
/// Emits `result[i] = min(values[i .. i + window])` over the padded input, so
/// callers control alignment by how they pad.
fn windowed_min(values: &[f64], window: usize) -> Vec<f64> {
    if window <= 1 || values.is_empty() {
        return values.to_vec();
    }
    let mut deque: std::collections::VecDeque<usize> = std::collections::VecDeque::new();
    let mut out = Vec::with_capacity(values.len().saturating_sub(window - 1));
    for i in 0..values.len() {
        while deque.back().is_some_and(|&j| values[j] >= values[i]) {
            deque.pop_back();
        }
        deque.push_back(i);
        if let Some(&front) = deque.front() {
            if i >= window && front + window <= i {
                deque.pop_front();
            }
        }
        if i + 1 >= window {
            out.push(values[*deque.front().expect("deque cannot be empty")]);
        }
    }
    out
}

/// `scipy.ndimage.minimum_filter1d(values, size, mode)` with `origin=0`.
pub fn minimum_filter1d(values: &[f64], size: usize, mode: BorderMode) -> Vec<f64> {
    if size <= 1 || values.is_empty() {
        return values.to_vec();
    }
    let left = size / 2;
    let right = size - left - 1;
    let padded = pad(values, left, right, mode);
    windowed_min(&padded, size)
}

/// Streaming sliding-window minimum for the worklet's limiter: push samples
/// in order, read the minimum over the last `window` pushes.
#[derive(Clone, Debug)]
pub struct SlidingMin {
    window: usize,
    pushed: usize,
    deque: std::collections::VecDeque<(usize, f64)>,
}

impl SlidingMin {
    pub fn new(window: usize) -> Self {
        Self {
            window: window.max(1),
            pushed: 0,
            deque: std::collections::VecDeque::new(),
        }
    }

    pub fn reset(&mut self) {
        self.pushed = 0;
        self.deque.clear();
    }

    pub fn push(&mut self, value: f64) -> f64 {
        while self.deque.back().is_some_and(|&(_, v)| v >= value) {
            self.deque.pop_back();
        }
        self.deque.push_back((self.pushed, value));
        let cutoff = self.pushed as i64 - self.window as i64 + 1;
        while self.deque.front().is_some_and(|&(i, _)| (i as i64) < cutoff) {
            self.deque.pop_front();
        }
        self.pushed += 1;
        self.deque.front().expect("deque cannot be empty").1
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
