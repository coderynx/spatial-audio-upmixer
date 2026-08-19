//! Chain head: subsonic high-pass on the mains, DC removal on LFE.
//!
//! One shared 2nd-order Butterworth design reaches every non-LFE channel, so
//! the stage is identical LTI filtering and commutes with the LF sum the same
//! way the EQ stage does. LFE is band-limited upstream and its sub content is
//! the point, so it gets a 1st-order pole-zero DC blocker instead.

use crate::kernels::biquad::sosfilt;
use crate::kernels::butter::{butter_sos, BandType};

/// Corner of the LFE DC blocker, in Hz. Structural rather than tunable: it
/// exists to remove offset, not to shape the sub band.
pub const DC_BLOCK_HZ: f64 = 5.0;

#[derive(Clone, Copy, Debug, PartialEq, serde::Deserialize)]
pub struct HeadParams {
    pub cutoff_hz: f64,
}

/// The sections one channel runs, by whether it carries LFE.
pub fn head_sos(sample_rate: u32, p: &HeadParams, is_lfe: bool) -> Vec<[f64; 6]> {
    let nyquist = sample_rate as f64 / 2.0;
    let (order, hz) = if is_lfe { (1, DC_BLOCK_HZ) } else { (2, p.cutoff_hz) };
    butter_sos(order, (hz / nyquist).clamp(1e-6, 0.999), BandType::High)
}

/// High-pass every channel in place.
pub fn chain_head(bed: &mut super::Bed, lfe: Option<usize>, sample_rate: u32, p: &HeadParams) {
    let mains = head_sos(sample_rate, p, false);
    let sub = head_sos(sample_rate, p, true);
    for (i, channel) in bed.iter_mut().enumerate() {
        let sections = if Some(i) == lfe { &sub } else { &mains };
        *channel = sosfilt(sections, channel);
    }
}
