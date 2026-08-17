//! Playhead moves: cold jumps and warmed seeks.

use super::{build_decorrelator, build_unifier, PreviewEngine, SEEK_PREROLL_MS};
use crate::stream::meters::Level;

impl PreviewEngine {
    /// Reset filter states and drop the playhead at `frame`, with no run-up.
    ///
    /// Leaves every filter cold at `frame`, so the caller is responsible for
    /// warming it back up (see [`Self::seek`]) or for a use that tolerates a
    /// cold start, such as a measurement excerpt where a `preroll` of real
    /// audio is rendered and discarded before anything is measured.
    pub(crate) fn jump_to(&mut self, frame: usize) {
        let target = frame.min(self.total_frames);
        self.rewind();
        self.emitted = target;
        self.unify_done = target;
        self.pre.base = target;
        self.post.base = target;
        // Rebuilt at the landing frame: their band splits carry a forward
        // pass and `rewind` left one starting at the top of the programme.
        let n_channels = self.params.speakers.len();
        self.unifier = build_unifier(self.sample_rate, n_channels, &self.params, target);
        self.decorrelator = build_decorrelator(self.sample_rate, n_channels, &self.params, target);
        // A cold jump has rendered nothing at the new position yet, so the
        // last render's levels are stale — reset them rather than reporting
        // whatever was playing before the jump. `seek`'s own preroll render
        // (when there is one) overwrites this with real levels right after;
        // when there isn't one (e.g. landing exactly on frame 0), this is
        // what `stem_spectrum`'s own live-position read already agrees on.
        for pair in &mut self.meters.stems {
            *pair = [Level::default(); 2];
        }
        for level in &mut self.meters.channels {
            *level = Level::default();
        }
        self.meters.output = [Level::default(); 2];
        for tail in &mut self.output_meter_tail {
            tail.clear();
        }
    }

    /// Jump to `frame`, warming the filter states up from shortly before it.
    ///
    /// Starting cold would be audible: the surround and height sends are
    /// Haas-delayed by up to 37 ms and would drop out, and the compressor
    /// would re-attack from silence. Rendering a discarded run-up instead
    /// lets every state settle, so a seek lands on the audio the export
    /// would have produced there.
    pub fn seek(&mut self, frame: usize) {
        let target = frame.min(self.total_frames);
        let preroll = (self.sample_rate as f64 * SEEK_PREROLL_MS / 1000.0) as usize;
        self.jump_to(target.saturating_sub(preroll));

        let block = 4096;
        let width = self.params.speakers.len().max(2);
        let mut scratch = vec![0.0; width * block];
        while self.emitted < target {
            let step = block.min(target - self.emitted);
            if self.render(&mut scratch, step) == 0 {
                break;
            }
        }
    }
}
