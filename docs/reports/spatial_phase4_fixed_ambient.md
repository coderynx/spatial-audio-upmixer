# Spatial phase 4 — fixed-filter ambient expander

## Status

The Rust-only 7.1.4 prototype is available for the required technical gates.
It has no product control surface: `paired` remains the only shipped renderer.

## Prototype contract

Each rear/height left/right input is independently passed through one fixed,
pure-wet velvet FIR per 7.1.4 destination. The filter map is deterministic,
has no signal-derived controls, and its state is partition invariant.

## Remaining gates

Written legal clearance, native and binaural listening, the paired-render
measurements, and the worklet benchmark remain required before product wiring.
