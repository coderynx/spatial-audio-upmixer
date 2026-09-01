# Documentation Map

Start with the smallest document that matches the change. The root
[`AGENTS.md`](../AGENTS.md) is the operational entry point; this map is useful
to both agents and contributors.

| Need | Reference |
| --- | --- |
| Repository workflow and conventions | [Agent workflow](agent_workflow.md) |
| Core DSP boundary and separation quality | `packages/core/AGENTS.md`, [evaluation harness](evaluation_harness.md) |
| Rust bindings, parity, or realtime preview | `packages/dsp/AGENTS.md`, [DSP development](dsp_development.md), [preview/export parity](contracts/preview_export_parity.md) |
| Project data and manifests | [Project/manifest parity](project_manifest_parity.md) |
| API endpoint or background job | [Web API architecture](web_api_architecture.md) |
| Web state, persistence, or preview wiring | [Web architecture](web_architecture.md) |
| Web appearance or interaction | [UI design](web_ui_design.md), [UI controls](web_ui_controls.md), [UI canvas](web_ui_canvas.md) |
| Delivery standards | [`standards/`](standards/) |
| Explicit implementation history | [`plans/`](plans/) and [`reports/`](reports/) |

`plans/` and `reports/` are historical records, not current behavior contracts.
Prefer a contract or architecture document when they disagree.
