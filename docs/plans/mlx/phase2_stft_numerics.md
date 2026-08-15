# Phase 2 — MLX STFT/iSTFT and numerics parity kit

Read `docs/plans/mlx/README.md` first for context and ground rules.
Requires phase 1 merged.

## Goal

The shared numerical building blocks every MLX arch needs, each proven
equivalent to its torch counterpart by tests. No arch code yet. This phase
exists because the subtle bugs of this project (STFT padding conventions,
RoPE rotation convention) are cheapest to catch in isolation.

## Deliverables

`packages/core/src/separation/inference/mlx/ops.py` (split if it crosses
~400 lines) containing:

1. **`stft(x, n_fft, hop_length, win_length, window, normalized)`** matching
   `torch.stft(..., center=True, return_complex=True)` exactly:
   - reflect-pad `n_fft // 2` on both ends (torch's `center=True` uses
     reflect padding),
   - frame with `hop_length`, multiply by window (zero-padded to `n_fft`
     when `win_length < n_fft`),
   - `mx.fft.rfft` per frame; output shape `(..., n_fft//2 + 1, frames)`
     complex64, matching torch's layout.
   - honor `normalized=` (the BS-Roformer-SW config may set
     `stft_normalized`; check the bundled YAMLs in `inference/configs/`).
2. **`istft(spec, n_fft, hop_length, win_length, window, length)`** matching
   `torch.istft(..., center=True)`: irfft per frame, overlap-add, divide by
   the summed squared window envelope, trim center padding, honor `length=`.
3. **Window functions**: hann (periodic) and hamming as `mx.array`
   constructors — torch's `hann_window(periodic=True)` and
   `scipy.signal.windows.hamming` are both in play (`demix.py` uses hamming
   for overlap-add weighting, the archs use hann inside the model).
4. **`rope_rotate(t, freqs)`** reproducing
   `rotary_embedding_torch.RotaryEmbedding.rotate_queries_or_keys` for the
   configs used here. Read the vendored call sites first:
   `archs/bs_roformer.py` `_rotate_queries_or_keys` (lines ~39-56) — note it
   builds freqs explicitly to dodge a zero-width-tensor DML issue; the MLX
   version only needs the mathematical result. Decide between `mx.fast.rope`
   (only if its half-rotation convention matches — rotary_embedding_torch
   uses interleaved GPT-NeoX-style `rotate_half`; verify, do not assume) and
   a ~15-line manual implementation. A manual implementation proven by test
   beats a fast kernel with the wrong convention.
5. **`attention(q, k, v, scale=None)`** via
   `mx.fast.scaled_dot_product_attention`, dropout-free (inference only).
   Also cover the `LinearAttention` variant's needs if inspection of
   `archs/bs_roformer.py` (lines ~148-170) shows it is reachable in
   production configs — check the bundled YAMLs for `linear_attn`; if no
   production config enables it, note that in the module docstring and skip
   it.

## Tests

`packages/core/tests/test_mlx_ops.py`, all under
`pytest.importorskip("mlx")`, all seeded/deterministic, comparing against
torch CPU reference:

- stft/istft round-trip and direct comparison vs `torch.stft`/`torch.istft`
  for every (n_fft, hop, win) combination appearing in
  `inference/configs/*.yaml` (grep them out; do not hardcode a guess).
  Tolerance: `atol=1e-4` on spectra, `atol=1e-5` on istft audio.
- Odd lengths, short signals (< n_fft), and the `length=` trim path.
- rope parity vs `rotary_embedding_torch` for dim_head values in the
  configs, sequence lengths including 1: `atol=1e-5`.
- attention parity vs `F.scaled_dot_product_attention`: `atol=1e-4`
  (fp32), shapes matching real band/time transformer dims from configs.

## Out of scope

- Arch modules, demix loop, engine wiring.
- fp16/bf16 — everything fp32 in this phase.

## Done when

- All parity tests pass on an Apple-silicon machine with mlx installed.
- Full suite still green without mlx installed (importorskip working).
- Any convention discovery worth keeping (e.g. "mx.fast.rope convention
  matches / does not match, manual impl chosen") recorded in the module
  docstring, one line each — that is a standards/model-quirk note the
  comment policy permits.
