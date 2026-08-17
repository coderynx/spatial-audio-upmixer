//! Zero-phase band splits, computed once per sample and paid for gradually.
//!
//! The offline stages take their band with `sosfiltfilt` over the whole
//! signal. Recomputing that inside a render call re-filters the horizon on
//! both sides of every block — the redundancy ledger entries D25/D26 record
//! for earlier stages, and D33 for this one. Here the forward pass carries
//! its state, so every sample is filtered once, and only the anticausal
//! backward pass is redone: one `ahead`-sample warm-up per `chunk` of output,
//! sliced across the render calls that consume the previous chunk so no
//! single quantum pays for a whole one.

use crate::kernels::biquad::SosFilter;
use crate::kernels::filtfilt::default_padlen;

struct Chan {
    forward: SosFilter,
    backward: SosFilter,
    /// Forward-pass output for `[fwd_base, fwd_base + fwd.len())`.
    fwd: Vec<f64>,
    /// Finished band for `[ready_base, ready_base + ready.len())`.
    ready: Vec<f64>,
    /// The chunk the backward pass is currently filling.
    out: Vec<f64>,
}

pub struct RollingBand {
    sections: Vec<[f64; 6]>,
    /// Source channel each slot tracks, in slot order.
    channels: Vec<usize>,
    chans: Vec<Chan>,
    ahead: usize,
    chunk: usize,
    pad: usize,
    fwd_base: usize,
    fwd_pos: usize,
    ready_base: usize,
    chunk_start: usize,
    /// Where the backward pass has come down to, when one is in flight.
    cursor: Option<usize>,
    head_done: bool,
    tail_done: bool,
    served: usize,
}

impl RollingBand {
    pub fn new(sections: Vec<[f64; 6]>, ahead: usize, chunk: usize, channels: Vec<usize>, base: usize) -> Self {
        let pad = default_padlen(&sections);
        let chans = channels
            .iter()
            .map(|_| Chan {
                forward: SosFilter::from_flat(&sections),
                backward: SosFilter::from_flat(&sections),
                fwd: Vec::new(),
                ready: Vec::new(),
                out: Vec::new(),
            })
            .collect();
        Self {
            sections,
            channels,
            chans,
            ahead,
            chunk: chunk.max(1),
            pad,
            fwd_base: base,
            fwd_pos: base,
            ready_base: base,
            chunk_start: base,
            cursor: None,
            head_done: false,
            tail_done: false,
            served: base,
        }
    }

    /// Source the caller must keep ahead of what it reads: the warm-up plus
    /// the chunk being filled behind the one being consumed.
    pub fn look_ahead(&self) -> usize {
        self.ahead + 2 * self.chunk
    }

    pub fn channels(&self) -> &[usize] {
        &self.channels
    }

    /// The band for slot `slot` over `[start, end)`, as far as it is ready.
    pub fn band(&self, slot: usize, start: usize, end: usize) -> &[f64] {
        let ready = &self.chans[slot].ready;
        let lo = (start - self.ready_base).min(ready.len());
        let hi = (end - self.ready_base).min(ready.len());
        &ready[lo..hi]
    }

    /// Take in whatever `source` has gained, make sure `[start, end)` is
    /// ready, and pay down the next chunk by what those frames owe.
    ///
    /// `total` is the programme length: reaching it is what tells the forward
    /// pass to run out over `sosfiltfilt`'s trailing pad rather than wait for
    /// samples that are never coming.
    pub fn advance(
        &mut self,
        source: &[Vec<f64>],
        source_base: usize,
        total: usize,
        start: usize,
        end: usize,
    ) {
        self.pull_forward(source, source_base, total);

        // Compacted a chunk at a time: draining what the caller passed on
        // every call would memmove the whole buffer per render quantum.
        let drop = (start.saturating_sub(self.ready_base)).min(self.chans[0].ready.len());
        if drop >= self.chunk {
            for chan in &mut self.chans {
                chan.ready.drain(..drop);
            }
            self.ready_base += drop;
        }

        let goal = end.min(total);
        while self.ready_end() < goal {
            if self.cursor.is_none() && !self.start_chunk(total) {
                break;
            }
            self.work(usize::MAX);
        }

        let owed = end.saturating_sub(self.served) * self.per_frame();
        self.served = self.served.max(end);
        if self.cursor.is_none() {
            self.start_chunk(total);
        }
        self.work(owed);
    }

    /// Backward-pass samples one frame of output owes. Paid a slice faster
    /// than the chunk is consumed: at exactly the chunk's own rate the last
    /// slice lands in the call that needs it, and any rounding there turns
    /// into a synchronous catch-up.
    fn per_frame(&self) -> usize {
        (self.chunk + self.ahead).div_ceil(self.chunk) + 1
    }

    fn ready_end(&self) -> usize {
        self.ready_base + self.chans[0].ready.len()
    }

    /// Filter every source sample that has arrived, exactly once.
    fn pull_forward(&mut self, source: &[Vec<f64>], source_base: usize, total: usize) {
        let available = source_base + source[self.channels[0]].len();
        if !self.head_done {
            // `sosfiltfilt` opens on an odd extension of the first `pad`
            // samples, seeded with its own step state; the forward pass has
            // to enter the signal the same way or the opening bars differ.
            if available < self.fwd_pos + self.pad + 1 {
                return;
            }
            let offset = self.fwd_pos - source_base;
            for (slot, &channel) in self.channels.iter().enumerate() {
                let x = &source[channel];
                let head = x[offset];
                let chan = &mut self.chans[slot];
                chan.forward.set_step_state(2.0 * head - x[offset + self.pad]);
                for j in (1..=self.pad).rev() {
                    chan.forward.tick(2.0 * head - x[offset + j]);
                }
            }
            self.head_done = true;
        }

        for (slot, &channel) in self.channels.iter().enumerate() {
            let x = &source[channel];
            let chan = &mut self.chans[slot];
            for n in self.fwd_pos..available {
                let y = chan.forward.tick(x[n - source_base]);
                chan.fwd.push(y);
            }
        }
        self.fwd_pos = available;

        if !self.tail_done && total > 0 && available >= total {
            let last = total - 1 - source_base;
            for (slot, &channel) in self.channels.iter().enumerate() {
                let x = &source[channel];
                let tail = x[last];
                let chan = &mut self.chans[slot];
                for j in 1..=self.pad.min(last) {
                    let y = chan.forward.tick(2.0 * tail - x[last - j]);
                    chan.fwd.push(y);
                }
            }
            self.tail_done = true;
        }
    }

    /// Aim the backward pass at the next chunk, once its warm-up exists.
    fn start_chunk(&mut self, total: usize) -> bool {
        let start = self.chunk_start;
        if start >= total {
            return false;
        }
        let fwd_end = self.fwd_base + self.chans[0].fwd.len();
        let want = start + self.chunk + self.ahead;
        let hi = want.min(fwd_end);
        if hi < want && !self.tail_done {
            return false;
        }
        let len = self.chunk.min(total - start);
        for chan in &mut self.chans {
            chan.backward = SosFilter::from_flat(&self.sections);
            chan.backward.set_step_state(chan.fwd[hi - 1 - self.fwd_base]);
            chan.out = vec![0.0; len];
        }
        self.cursor = Some(hi);
        true
    }

    /// Walk the backward pass down by at most `samples`.
    fn work(&mut self, samples: usize) {
        let Some(mut cursor) = self.cursor else { return };
        let start = self.chunk_start;
        let stop = cursor.saturating_sub(samples).max(start);
        let kept = start + self.chans[0].out.len();
        while cursor > stop {
            cursor -= 1;
            let n = cursor - self.fwd_base;
            for chan in &mut self.chans {
                let y = chan.backward.tick(chan.fwd[n]);
                if cursor < kept {
                    chan.out[cursor - start] = y;
                }
            }
        }
        self.cursor = Some(cursor);
        if cursor == start {
            self.finish(kept);
        }
    }

    fn finish(&mut self, next: usize) {
        let drop = next - self.fwd_base;
        for chan in &mut self.chans {
            let out = std::mem::take(&mut chan.out);
            chan.ready.extend(out);
            chan.fwd.drain(..drop);
        }
        self.fwd_base = next;
        self.chunk_start = next;
        self.cursor = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::kernels::butter::{butter_bandpass_sos, butter_sos, BandType};
    use crate::kernels::filtfilt::sosfiltfilt;

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
