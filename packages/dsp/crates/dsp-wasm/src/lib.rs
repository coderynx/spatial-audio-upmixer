//! WebAssembly bindings for the shared DSP core.
//!
//! Deliberately a plain C ABI rather than wasm-bindgen: this module is
//! instantiated inside an `AudioWorkletGlobalScope`, which has no module
//! loader for wasm-bindgen's generated glue. The worklet shim owns the
//! marshalling instead.

mod engine;
mod measure;
mod offline;
mod panner;
mod params;
mod scale;

use std::alloc::{alloc, dealloc, Layout};

/// Allocate `bytes` of linear memory for the host to write into.
///
/// # Safety
/// The caller must return the pointer to [`dsp_free`] with the same size.
#[no_mangle]
pub unsafe extern "C" fn dsp_alloc(bytes: usize) -> *mut u8 {
    if bytes == 0 {
        return std::ptr::null_mut();
    }
    let layout = Layout::from_size_align(bytes, 8).expect("invalid allocation layout");
    alloc(layout)
}

/// Release memory previously handed out by [`dsp_alloc`].
///
/// # Safety
/// `ptr` must come from [`dsp_alloc`] with the same `bytes`.
#[no_mangle]
pub unsafe extern "C" fn dsp_free(ptr: *mut u8, bytes: usize) {
    if ptr.is_null() || bytes == 0 {
        return;
    }
    let layout = Layout::from_size_align(bytes, 8).expect("invalid allocation layout");
    dealloc(ptr, layout);
}

/// Core revision, encoded so the host can assert wheel/wasm provenance.
#[no_mangle]
pub extern "C" fn dsp_core_version_len() -> usize {
    upmixer_dsp_core::version().len()
}

/// Pointer to the version string bytes (length from [`dsp_core_version_len`]).
#[no_mangle]
pub extern "C" fn dsp_core_version_ptr() -> *const u8 {
    upmixer_dsp_core::version().as_ptr()
}
