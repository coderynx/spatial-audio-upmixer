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
