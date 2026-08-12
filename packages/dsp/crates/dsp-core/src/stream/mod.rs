//! Streaming forms of the offline stages, for the browser worklet.
//!
//! The worklet owns the decoded stems, so it always knows its input ahead of
//! the playhead. Rather than porting each stage to a causal approximation, it
//! runs the *offline* algorithms incrementally over a render horizon — which
//! is why the preview can be the same filter as the export rather than a
//! bounded approximation of it.

pub mod conv;
pub mod engine;
pub mod master;
pub mod measure;
pub mod meters;
pub mod output;
pub mod params;
pub mod routing;
pub mod state;
