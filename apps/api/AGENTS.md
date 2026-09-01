# API Agent Guide

Read the root guide and [Web API architecture](../../docs/web_api_architecture.md).
This package is a delivery layer over public `upmixer` APIs; do not add DSP or
directly control Torch, models, or devices here.

Keep web state, capability checks, and error presentation in this package.
Add endpoints to their `features/<name>/` vertical slice, not a top-level
routes module or shared schema/model grab-bag. Follow the architecture's
cross-slice import rules and worker-mixin composition pattern.
