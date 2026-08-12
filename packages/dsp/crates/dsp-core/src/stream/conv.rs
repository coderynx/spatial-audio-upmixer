//! Block-streaming FIR convolution.
//!
//! Overlap-add with a persistent tail: feeding a signal through in arbitrary
//! blocks produces exactly the linear convolution the offline path computes,
//! so the preview's FIR stages are the same filter as the export's, not an
//! approximation of it.

use crate::kernels::fft::fftconvolve;

pub struct StreamingConvolver {
    kernel: Vec<f64>,
    tail: Vec<f64>,
}

impl StreamingConvolver {
    pub fn new(kernel: Vec<f64>) -> Self {
        let tail = vec![0.0; kernel.len().saturating_sub(1)];
        Self { kernel, tail }
    }

    pub fn reset(&mut self) {
        self.tail.fill(0.0);
    }

    pub fn latency(&self) -> usize {
        0
    }

    /// Convolve one block, carrying the overhang into the next call.
    pub fn process(&mut self, block: &[f64]) -> Vec<f64> {
        if block.is_empty() {
            return Vec::new();
        }
        let full = fftconvolve(block, &self.kernel);
        let n = block.len();

        let mut out = Vec::with_capacity(n);
        for i in 0..n {
            out.push(full[i] + self.tail.get(i).copied().unwrap_or(0.0));
        }

        let overhang = full.len() - n;
        let mut next_tail = vec![0.0; self.tail.len().max(overhang)];
        for (i, slot) in next_tail.iter_mut().enumerate() {
            let carried = if i + n < self.tail.len() { self.tail[i + n] } else { 0.0 };
            let fresh = if i < overhang { full[n + i] } else { 0.0 };
            *slot = carried + fresh;
        }
        next_tail.truncate(self.tail.len().max(overhang));
        self.tail = next_tail;
        out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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

    #[test]
    fn an_impulse_streams_out_the_whole_kernel() {
        let k = kernel(64);
        let mut conv = StreamingConvolver::new(k.clone());
        let mut got = conv.process(&[1.0]);
        for _ in 0..70 {
            got.extend(conv.process(&[0.0]));
        }
        for (i, tap) in k.iter().enumerate() {
            assert!((got[i] - tap).abs() < 1e-12, "tap {i}");
        }
    }

    #[test]
    fn reset_clears_the_carried_tail() {
        let mut conv = StreamingConvolver::new(kernel(32));
        conv.process(&[1.0, 2.0, 3.0]);
        conv.reset();
        let after = conv.process(&[0.0; 8]);
        assert!(after.iter().all(|v| v.abs() < 1e-15));
    }
}
