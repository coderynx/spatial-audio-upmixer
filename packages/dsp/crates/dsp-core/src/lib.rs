//! Shared DSP core for the upmixer export pipeline (via PyO3) and the web
//! preview (via WASM). Every stage here is the single implementation both
//! sides execute; see `docs/contracts/preview_export_parity.md`.

pub mod kernels;
pub mod loudness;
pub mod mastering;

/// Revision marker both bindings surface so a wheel/wasm mismatch is loud.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}
