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

struct History {
    samples: Vec<f64>,
    start: usize,
    len: usize,
}

impl History {
    fn new() -> Self {
        Self {
            samples: Vec::new(),
            start: 0,
            len: 0,
        }
    }

    fn clear(&mut self) {
        self.start = 0;
        self.len = 0;
    }

    fn reserve(&mut self, capacity: usize) {
        if self.samples.len() >= capacity {
            return;
        }
        let mut samples = vec![0.0; capacity];
        for (i, slot) in samples.iter_mut().take(self.len).enumerate() {
            *slot = self.get(i);
        }
        self.samples = samples;
        self.start = 0;
    }

    fn push(&mut self, block: &[f64]) {
        let capacity = self.samples.len();
        for &sample in block {
            if self.len < capacity {
                self.samples[(self.start + self.len) % capacity] = sample;
                self.len += 1;
            } else if capacity > 0 {
                self.samples[self.start] = sample;
                self.start = (self.start + 1) % capacity;
            }
        }
    }

    fn get(&self, index: usize) -> f64 {
        self.samples[(self.start + index) % self.samples.len()]
    }

    /// Read history as if it extended backward with zeros.
    fn fill_window(&self, end: isize, out: &mut [f64]) {
        out.fill(0.0);
        let start = end - out.len() as isize;
        for (i, slot) in out.iter_mut().enumerate() {
            let index = start + i as isize;
            if index >= 0 && (index as usize) < self.len {
                *slot = self.get(index as usize);
            }
        }
    }
}

struct Partitioned {
    hop: usize,
    fft: RealFft,
    /// One spectrum per `hop`-sized slice of the kernel.
    kernel: Vec<Vec<Complex64>>,
    /// `fdl[(cursor + i) % len]` is the input from `i` hops ago.
    fdl: Vec<Vec<Complex64>>,
    cursor: usize,
    input: Vec<f64>,
    spectrum: Vec<Complex64>,
    acc: Vec<Complex64>,
    time: Vec<f64>,
}

pub struct StreamingConvolver {
    kernel: Vec<f64>,
    /// Retained in a ring so hop and kernel changes can rebuild the FDL.
    history: History,
    max_hop: usize,
    part: Option<Partitioned>,
}

impl StreamingConvolver {
    pub fn new(kernel: Vec<f64>) -> Self {
        Self {
            kernel,
            history: History::new(),
            max_hop: 0,
            part: None,
        }
    }

    pub fn reset(&mut self) {
        self.history.clear();
        if let Some(part) = &mut self.part {
            for spectrum in &mut part.fdl {
                spectrum.fill(Complex64::new(0.0, 0.0));
            }
            part.cursor = 0;
        }
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
        let mut out = Vec::with_capacity(block.len());
        self.process_into(block, &mut out);
        out
    }

    /// Convolve into caller-owned storage, reusing its capacity.
    pub fn process_into(&mut self, block: &[f64], out: &mut Vec<f64>) {
        self.process_with_spectrum(block, None, out);
    }

    /// Filter one signal through a matched pair while sharing its forward FFT.
    pub fn process_pair_into(
        pair: &mut [Self; 2],
        block: &[f64],
        left_out: &mut Vec<f64>,
        right_out: &mut Vec<f64>,
    ) {
        let (left, right) = pair.split_at_mut(1);
        left[0].process_into(block, left_out);
        let spectrum = left[0].part.as_ref().map(|part| part.spectrum.as_slice());
        right[0].process_with_spectrum(block, spectrum, right_out);
    }

    fn process_with_spectrum(
        &mut self,
        block: &[f64],
        spectrum: Option<&[Complex64]>,
        out: &mut Vec<f64>,
    ) {
        out.clear();
        if block.is_empty() {
            return;
        }
        if self.kernel.is_empty() {
            out.resize(block.len(), 0.0);
            return;
        }

        let hop = block.len();
        self.max_hop = self.max_hop.max(hop);
        self.history.reserve(self.kernel.len() + 2 * self.max_hop);
        let direct = self.kernel.len() <= DIRECT_MAX_TAPS || hop < MIN_HOP;
        if direct {
            // A direct pass leaves no delay line, so the next partitioned
            // call rebuilds one from history.
            self.part = None;
        } else {
            self.prepare(hop);
        }

        self.history.push(block);
        if direct {
            self.direct_into(hop, out);
        } else {
            self.partitioned_into(spectrum, out);
        }
    }

    /// Build the partitioning for `hop`, seeding its FDL from history.
    fn prepare(&mut self, hop: usize) {
        if self.part.as_ref().map(|part| part.hop) == Some(hop) {
            return;
        }
        let n = 2 * hop;
        let fft = RealFft::new(n);
        let count = self.kernel.len().div_ceil(hop);
        let bins = hop + 1;
        let mut input = vec![0.0; n];
        let spectrum = vec![Complex64::new(0.0, 0.0); bins];
        let mut kernel = vec![vec![Complex64::new(0.0, 0.0); bins]; count];
        for (i, transformed) in kernel.iter_mut().enumerate() {
            input.fill(0.0);
            let start = i * hop;
            let end = ((i + 1) * hop).min(self.kernel.len());
            input[..end - start].copy_from_slice(&self.kernel[start..end]);
            fft.rfft_into(&mut input, transformed);
        }

        let mut fdl = vec![vec![Complex64::new(0.0, 0.0); bins]; count];
        let history_end = self.history.len as isize;
        for (i, transformed) in fdl.iter_mut().enumerate() {
            self.history
                .fill_window(history_end - i as isize * hop as isize, &mut input);
            fft.rfft_into(&mut input, transformed);
        }
        self.part = Some(Partitioned {
            hop,
            fft,
            kernel,
            fdl,
            cursor: 0,
            input,
            spectrum,
            acc: vec![Complex64::new(0.0, 0.0); bins],
            time: vec![0.0; n],
        });
    }

    fn partitioned_into(&mut self, shared: Option<&[Complex64]>, out: &mut Vec<f64>) {
        let history = &self.history;
        let part = self.part.as_mut().expect("partitioning prepared");
        history.fill_window(history.len as isize, &mut part.input);
        if let Some(shared) = shared.filter(|shared| shared.len() == part.spectrum.len()) {
            part.spectrum.copy_from_slice(shared);
        } else {
            part.fft.rfft_into(&mut part.input, &mut part.spectrum);
        }

        let count = part.fdl.len();
        part.cursor = (part.cursor + count - 1) % count;
        part.fdl[part.cursor].copy_from_slice(&part.spectrum);
        part.acc.fill(Complex64::new(0.0, 0.0));
        for i in 0..count {
            let x = &part.fdl[(part.cursor + i) % count];
            let h = &part.kernel[i];
            for (slot, (x, h)) in part.acc.iter_mut().zip(x.iter().zip(h)) {
                *slot += x * h;
            }
        }
        part.fft.irfft_into(&mut part.acc, &mut part.time);
        out.extend_from_slice(&part.time[part.hop..]);
    }

    fn direct_into(&self, n: usize, out: &mut Vec<f64>) {
        let end = self.history.len as isize;
        out.extend((0..n).map(|i| {
            let t = end - n as isize + i as isize;
            let mut acc = 0.0;
            for (k, &h) in self.kernel.iter().enumerate() {
                let index = t - k as isize;
                if index < 0 {
                    break;
                }
                acc += h * self.history.get(index as usize);
            }
            acc
        }));
    }
}
