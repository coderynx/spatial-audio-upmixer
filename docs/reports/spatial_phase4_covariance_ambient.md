# Spatial phase 4 — covariance-targeted ambient renderer

## Decision

Rejected at the legal-clearance gate on 2026-08-25. The active-claims review
did not clear covariance-targeted mixing with decorrelated residual injection
for the existing ambient feeds. No prototype, control, or dormant renderer was
added.

## Result

The paired ambient renderer remains the only available mode. Its output and
preview cost are unchanged.

## Reconsideration

Re-open only after counsel clears a concrete implementation against the
then-current claims register. Start again at the Rust-only 7.1.4 prototype;
do not add a substitute direct/ambient estimator or panning method.
