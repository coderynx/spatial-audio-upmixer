//! Block-streaming FIR convolution.
//!
//! Uniform-partitioned overlap-save: the kernel is transformed once, split
//! into blocks the size of the caller's hop, and multiplied against a delay
//! line of input spectra. Feeding a signal through in arbitrary blocks
//! produces exactly the linear convolution the offline path computes, so the
//! preview's FIR stages are the same filter as the export's, not an
//! approximation of it.
//!
//! Partition size tracks the caller's block size, which keeps the latency at
//! zero: the transform window ends at the current block, so a block's own
//! output is available in the same call.

use rustfft::num_complex::Complex64;

use crate::kernels::fft::RealFft;

/// Below this the transform costs more than the multiply-accumulate does.
const DIRECT_MAX_TAPS: usize = 32;

/// Hops shorter than this partition badly — the delay line grows as the
/// kernel length over the hop, and the per-block transform stops paying off.
const MIN_HOP: usize = 32;

struct Partitioned {
    hop: usize,
    fft: RealFft,
    /// One spectrum per `hop`-sized slice of the kernel.
    kernel: Vec<Vec<Complex64>>,
    /// Input spectra; `fdl[(cursor + i) % len]` is the one from `i` hops ago.
    fdl: Vec<Vec<Complex64>>,
    cursor: usize,
}

pub struct StreamingConvolver {
    kernel: Vec<f64>,
    /// Input tail, oldest first — enough to rebuild the delay line or to run
    /// a direct pass.
    history: Vec<f64>,
    max_hop: usize,
    part: Option<Partitioned>,
}

impl StreamingConvolver {
    pub fn new(kernel: Vec<f64>) -> Self {
        Self { kernel, history: Vec::new(), max_hop: 0, part: None }
    }

    pub fn reset(&mut self) {
        self.history.clear();
        self.max_hop = 0;
        self.part = None;
    }

    /// Swap the kernel, keeping `history` — the next `process` call rebuilds
    /// the partition's frequency-domain delay line by re-transforming the
    /// retained input tail (the same machinery [`Self::prepare`] already uses
    /// to stay exact across a hop change), so the new filter picks up from
    /// the signal already in flight instead of starting cold.
    pub fn retune_kernel(&mut self, kernel: Vec<f64>) {
        self.kernel = kernel;
        self.part = None;
    }

    pub fn latency(&self) -> usize {
        0
    }

    /// Convolve one block, carrying the overhang into the next call.
    pub fn process(&mut self, block: &[f64]) -> Vec<f64> {
        if block.is_empty() {
            return Vec::new();
        }
        if self.kernel.is_empty() {
            return vec![0.0; block.len()];
        }

        let hop = block.len();
        let direct = self.kernel.len() <= DIRECT_MAX_TAPS || hop < MIN_HOP;
        if direct {
            // A direct pass leaves no delay line, so the next partitioned
            // call rebuilds one from history.
            self.part = None;
        } else {
            self.prepare(hop);
        }

        self.history.extend_from_slice(block);
        let out = if direct { self.direct(hop) } else { self.partitioned(hop) };

        self.max_hop = self.max_hop.max(hop);
        let keep = self.kernel.len() + 2 * self.max_hop;
        if self.history.len() > keep {
            self.history.drain(..self.history.len() - keep);
        }
        out
    }

    /// Read `history` as if it extended back to the start of the stream with
    /// zeros; trimming only ever discards samples no partition can reach.
    fn window(&self, end: isize, len: usize) -> Vec<f64> {
        let mut window = vec![0.0; len];
        let start = end - len as isize;
        for (j, slot) in window.iter_mut().enumerate() {
            let index = start + j as isize;
            if index >= 0 && (index as usize) < self.history.len() {
                *slot = self.history[index as usize];
            }
        }
        window
    }

    /// Build the partitioning for `hop`, seeding the delay line from history
    /// so a hop change mid-stream stays sample-exact.
    fn prepare(&mut self, hop: usize) {
        if self.part.as_ref().map(|p| p.hop) == Some(hop) {
            return;
        }
        let n = 2 * hop;
        let fft = RealFft::new(n);
        let count = self.kernel.len().div_ceil(hop);
        let kernel = (0..count)
            .map(|i| {
                let stop = ((i + 1) * hop).min(self.kernel.len());
                fft.rfft(&self.kernel[i * hop..stop])
            })
            .collect();

        let end = self.history.len() as isize;
        let fdl = (0..count)
            .map(|i| fft.rfft(&self.window(end - (i as isize) * hop as isize, n)))
            .collect();

        self.part = Some(Partitioned { hop, fft, kernel, fdl, cursor: 0 });
    }

    fn partitioned(&mut self, hop: usize) -> Vec<f64> {
        let window = self.window(self.history.len() as isize, 2 * hop);
        let part = self.part.as_mut().expect("partitioning prepared");
        let count = part.fdl.len();
        let spectrum = part.fft.rfft(&window);
        part.cursor = (part.cursor + count - 1) % count;
        part.fdl[part.cursor] = spectrum;

        let mut acc = vec![Complex64::new(0.0, 0.0); hop + 1];
        for i in 0..count {
            let x = &part.fdl[(part.cursor + i) % count];
            let h = &part.kernel[i];
            for (slot, (x, h)) in acc.iter_mut().zip(x.iter().zip(h.iter())) {
                *slot += x * h;
            }
        }
        part.fft.irfft(&acc)[hop..].to_vec()
    }

    fn direct(&self, n: usize) -> Vec<f64> {
        let end = self.history.len() as isize;
        (0..n)
            .map(|i| {
                let t = end - n as isize + i as isize;
                let mut acc = 0.0;
                for (k, &h) in self.kernel.iter().enumerate() {
                    let index = t - k as isize;
                    if index < 0 {
                        break;
                    }
                    acc += h * self.history[index as usize];
                }
                acc
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernels::fft::fftconvolve;

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
