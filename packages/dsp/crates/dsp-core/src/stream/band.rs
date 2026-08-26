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
    pub chunk_start: usize,
    /// Where the backward pass has come down to, when one is in flight.
    pub cursor: Option<usize>,
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

    pub fn prewarm(
        &mut self,
        source: &[Vec<f64>],
        source_base: usize,
        total: usize,
        end: usize,
        budget: usize,
    ) -> bool {
        self.pull_forward(source, source_base, total);
        if self.ready_end() >= end {
            return true;
        }
        if self.cursor.is_none() && !self.start_chunk(total) {
            return false;
        }
        self.work(budget);
        self.ready_end() >= end
    }

    /// Backward-pass samples one frame of output owes. Paid a slice faster
    /// than the chunk is consumed: at exactly the chunk's own rate the last
    /// slice lands in the call that needs it, and any rounding there turns
    /// into a synchronous catch-up.
    pub fn per_frame(&self) -> usize {
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
