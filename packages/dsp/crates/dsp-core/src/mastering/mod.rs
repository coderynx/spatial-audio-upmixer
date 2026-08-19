//! Mastering-bus stages. Ordering is contracted — chain head, reference
//! match, EQ, dynamic EQ, compression, bass control, BS.1770 loudness, soft
//! clip, then the look-ahead limiter last — and lives with the caller in
//! `mastering/chain.py`.

pub mod bass;
pub mod clip;
pub mod compressor;
pub mod decorrelate;
pub mod dyneq;
pub mod eq;
pub mod head;
pub mod limiter;

/// A multichannel bed. Channel naming and layout stay with the caller; the
/// core only needs to know which index is LFE, since every stage treats it
/// differently.
pub type Bed = [Vec<f64>];

/// Indices of every channel except LFE.
pub fn non_lfe(n_channels: usize, lfe: Option<usize>) -> Vec<usize> {
    (0..n_channels).filter(|i| Some(*i) != lfe).collect()
}
