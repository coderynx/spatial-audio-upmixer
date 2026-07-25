# Default binaural decoding filters

`aalto2016_N3_01-08ch.wav` / `aalto2016_N3_09-16ch.wav` — 3rd-order (16-channel)
ambisonic-to-binaural decoding filters, split into two 8-channel WAVs because
browsers cap multichannel WAV/OGG decoding at 8 channels (loaded and
concatenated at runtime by `ambisonics/dist/hoa-loader`'s `HOAloader`).

Source: Aalto University Department of Signal Processing and Acoustics,
Communication Acoustics — BRIRs derived from an Eigenmike measurement,
shipped as example filters with the JSAmbisonics library
(https://github.com/polarch/JSAmbisonics,
`examples/IRs/ambisonic2binaural_filters/aalto2016_N3_*.wav`).

License: BSD 3-Clause, per the JSAmbisonics repository (copyright Archontis
Politis, 2016).

These are the preview's default HRTF; `useStemPreview`'s `loadHrtf(url)`
lets a caller swap in a different order-3 decoding-filter WAV pair (same
`<base>_01-08ch.wav`/`_09-16ch.wav` naming convention) at runtime.
