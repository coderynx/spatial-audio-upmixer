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

## Preset crossovers

The routing presets use the three values evaluated by Lee's perceptual-band
study: 4 kHz for lead, vocal, and drum stems; 2 kHz for cymbals, harmonic
instruments, crowd, and other material; and 500 Hz for `Vocals Reverb`. This
is a conservative initial allocation: vocal and transient height sends retain
only their airy tail, while the dedicated diffuse reverb can rise broadly.
The source is Y.-H. Lee, “2D-to-3D Ambience Upmixing based on Perceptual Band
Allocation,” *Journal of the Audio Engineering Society* 63(10), 2015,
doi:10.17743/jaes.2015.0075.

The values are applied to every routing preset; presets continue to control
placement and ambient-send amount independently.

## Checks

- Rust covers every 48 kHz STFT bin at 500, 2000, and 4000 Hz for finite,
  bounded, complementary masks.
- The existing block-size, near-mono, and preview/export pins remain the
  regression checks for the shared split.
- The 48 kHz ambient-send workload measured 0.862 ms mean, 2.610 ms p99
  (0.98× the 2.67 ms deadline), and 3.518 ms worst on this machine.
- Listening verdict: pending the prescribed dark-tail, bright-tail, cymbal,
  sibilance, and wide-pad renders. Ambient-send amounts were not retuned.
