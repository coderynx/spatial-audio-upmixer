# Primary-stem ensemble

The opt-in `stem_ensemble` path runs `BS-Roformer-SW.ckpt` and
`model_scnet_ep_36_sdr_10.0891.ckpt` on the same primary parent, then uses the
fixed `avg_wave` 0.5/0.5 sample average for Bass and Drums. Guitar, Piano, and
Other remain the SW estimates; SCNet Vocals and Other are ignored. Drum-piece
requests ensemble the intermediate Drums stem before drumsep.

The model registry entry and SCNet inference implementation are in
`packages/core/src/separation/inference/`; the algorithm and eligibility
choice follow `~/Projects/upmixer-knowledge/techniques/ensembling.md` and the
SCNet model source registry. The in-repo evaluation harness remains the gate:
synthetic-corpus tests verify functional plumbing only, with no quality claim
or measured metric asserted here. Actual measured values belong to final
validation on a lawful reference corpus.
