# Spatial phase 2 — perceptual height allocation

## Objective response

`routing::ambient::height_mask` applies `H_height = r^8 / (1 + r^8)` and
`H_rear = 1 - H_height`, where `r = f / f_c`. The masks sum to one at every
STFT bin; their per-bin energy is the squared gain.

| Crossover | 0.5× crossover (rear / height) | Crossover | 2× crossover |
|---|---:|---:|---:|
| 500 Hz | 0.996109 / 0.003891 | 0.5 / 0.5 | 0.003891 / 0.996109 |
| 2000 Hz | 0.996109 / 0.003891 | 0.5 / 0.5 | 0.003891 / 0.996109 |
| 4000 Hz | 0.996109 / 0.003891 | 0.5 / 0.5 | 0.003891 / 0.996109 |

At half and double crossover, the dominant destination carries 0.992233 of
that bin's energy; each carries 0.25 at crossover.

## Checks

- Rust covers every 48 kHz STFT bin at 500, 2000, and 4000 Hz for finite,
  bounded, complementary masks.
- The existing block-size, near-mono, and preview/export pins remain the
  regression checks for the shared split.
- The 48 kHz ambient-send workload measured 0.862 ms mean, 2.610 ms p99
  (0.98× the 2.67 ms deadline), and 3.518 ms worst on this machine.
- Listening verdict: pending the prescribed dark-tail, bright-tail, cymbal,
  sibilance, and wide-pad renders. No ambient-send presets were retuned.
