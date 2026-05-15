"""Content-aware spatial mixing: maps stem analysis features to per-channel gains.

Each separated stem is analysed once (whole-file) to extract perceptual features,
which are then used to modulate the base routing-table gains:

  final_gain(ch) = base_gain(ch) × modifier(ch, features)

Modifiers are blended with a neutral value of 1.0 by `strength` ∈ [0, 1]:
  applied_mod(ch) = 1.0 + (raw_mod(ch) − 1.0) × strength

so strength=0 disables content awareness entirely and strength=1 applies it fully.

Modifier ranges are symmetric around 1.0 (lo + hi = 2.0) so that an average
signal (all features at 0.5) produces no change from the base routing.

Channel group mappings
──────────────────────
  LFE          : lf_ratio       → bass-heavy stems drive more LFE
  C            : narrowness     → narrow/centred content stays in centre
  FL / FR      : directness     → transient, dry content reinforced up-front
  SL / SR      : ambience       → wide, diffuse content spread to sides
  BL / BR      : ambience       → same, slightly attenuated
  TFL / TFR    : air            → diffuse high-freq content elevated to height front
  TBL / TBR    : air × 0.8     → same, slightly less at rear height
"""
from __future__ import annotations

from upmixer.analysis.stem_analyzer import StemAnalyzer, StemFeatures
from upmixer.config import UpmixConfig
from upmixer.formats import ChannelLabel, OutputFormat
from upmixer.separation.stem_router import StemRouter


def _clamp(lo: float, hi: float, v: float) -> float:
    return max(lo, min(hi, v))


def _lerp(lo: float, hi: float, t: float) -> float:
    """Linearly interpolate; t is clamped to [0, 1]."""
    return lo + (hi - lo) * _clamp(0.0, 1.0, t)


# ── Modifier tables: (lo, hi) per channel group ───────────────────────────────
# lo + hi = 2.0 ensures t=0.5 → modifier=1.0 (neutral)

_MOD_LFE      = (0.4, 1.6)  # lf_ratio driver
_MOD_C        = (0.6, 1.4)  # narrowness driver
_MOD_FRONT    = (0.75, 1.25)  # directness driver
_MOD_SIDE     = (0.3, 1.7)  # ambience driver
_MOD_BACK     = (0.3, 1.7)  # ambience × 0.75 driver
_MOD_HEIGHT_F = (0.2, 1.8)  # air driver
_MOD_HEIGHT_B = (0.2, 1.8)  # air × 0.8 driver

_LEFT_CHANNELS  = {ChannelLabel.FL, ChannelLabel.SL, ChannelLabel.BL, ChannelLabel.TFL, ChannelLabel.TBL}
_RIGHT_CHANNELS = {ChannelLabel.FR, ChannelLabel.SR, ChannelLabel.BR, ChannelLabel.TFR, ChannelLabel.TBR}


def _compute_modifiers(
    features: StemFeatures,
    fmt: OutputFormat,
    strength: float,
) -> dict[str, float]:
    """Return per-channel gain modifiers blended by strength."""
    sw = features.stereo_width
    sf = features.spectral_flatness
    lf = features.lf_ratio
    hf = features.hf_ratio
    td = features.transient_density

    # Derived indices, all in [0, 1]
    ambience   = sw * (0.5 + 0.5 * sf)      # wide + diffuse → surrounds / back
    air        = hf * (0.4 + 0.6 * sf)      # high-freq + diffuse → height
    directness = _clamp(0.0, 1.0,            # transient + narrow → front
                        0.5 * td + 0.5 * (1.0 - sw))
    narrowness = 1.0 - sw                    # mono-ish → centre

    raw: dict[str, float] = {}
    for label in fmt.channels:
        ch = label.value
        if label == ChannelLabel.LFE:
            raw[ch] = _lerp(*_MOD_LFE, lf)
        elif label == ChannelLabel.C:
            raw[ch] = _lerp(*_MOD_C, narrowness)
        elif label in {ChannelLabel.FL, ChannelLabel.FR}:
            front_t = _clamp(0.0, 1.0, 0.5 + 0.5 * directness - 0.3 * ambience)
            raw[ch] = _lerp(*_MOD_FRONT, front_t)
        elif label in {ChannelLabel.SL, ChannelLabel.SR}:
            raw[ch] = _lerp(*_MOD_SIDE, ambience)
        elif label in {ChannelLabel.BL, ChannelLabel.BR}:
            raw[ch] = _lerp(*_MOD_BACK, ambience * 0.75)
        elif label in {ChannelLabel.TFL, ChannelLabel.TFR}:
            raw[ch] = _lerp(*_MOD_HEIGHT_F, air)
        elif label in {ChannelLabel.TBL, ChannelLabel.TBR}:
            raw[ch] = _lerp(*_MOD_HEIGHT_B, air * 0.8)
        else:
            raw[ch] = 1.0

    # Blend raw modifier toward 1.0 (neutral) by strength
    return {ch: 1.0 + (m - 1.0) * strength for ch, m in raw.items()}


class ContentMixer:
    """Analyse all stems once and produce content-aware per-stem routing tables.

    Usage:
        mixer = ContentMixer(config, output_fmt, sample_rate)
        per_stem_routing = mixer.build(all_stems, router)
        channels = router.route(all_stems, n_samples, per_stem_routing=per_stem_routing)
    """

    def __init__(
        self,
        config: UpmixConfig,
        output_fmt: OutputFormat,
        sample_rate: int,
    ) -> None:
        self._strength = config.content_mix_strength
        self._fmt = output_fmt
        self._analyzer = StemAnalyzer(
            sample_rate=sample_rate,
            lf_cutoff_hz=config.surround_bass_cutoff_hz,
            hf_cutoff_hz=config.content_hf_analysis_hz,
        )

    def build(
        self,
        stems: dict[str, "np.ndarray"],  # noqa: F821
        router: StemRouter,
    ) -> dict[str, dict[str, float]]:
        """Analyse each stem and return content-aware routing for every stem key.

        For each stem key:
          1. Fetch base routing from router (ZONE_ROUTING or DEFAULT_ROUTING).
          2. Analyse stem audio to extract StemFeatures.
          3. Compute per-channel gain modifiers from features.
          4. Multiply base gains by modifiers.

        Channels absent from the base routing remain at zero (not added).
        """
        per_stem: dict[str, dict[str, float]] = {}

        for stem_key, audio in stems.items():
            base = router.get_routing(stem_key)
            if not base:
                continue

            features = self._analyzer.analyze(audio)
            mods = _compute_modifiers(features, self._fmt, self._strength)

            per_stem[stem_key] = {
                ch: gain * mods.get(ch, 1.0)
                for ch, gain in base.items()
            }

        return per_stem

    def describe(self, stem_key: str, features: StemFeatures) -> str:
        """Return a human-readable one-line description of stem features."""
        mods = _compute_modifiers(features, self._fmt, self._strength)
        ch_parts = [f"{ch}×{m:.2f}" for ch, m in sorted(mods.items()) if abs(m - 1.0) > 0.05]
        return (
            f"{stem_key}: "
            f"rms={features.rms:.3f} "
            f"width={features.stereo_width:.2f} "
            f"lf={features.lf_ratio:.2f} "
            f"hf={features.hf_ratio:.2f} "
            f"flat={features.spectral_flatness:.2f} "
            f"trans={features.transient_density:.2f} "
            + (" → " + " ".join(ch_parts) if ch_parts else "")
        )
