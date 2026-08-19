"""Tests for the linked dynamic EQ's chain wiring and manifest surface.

The DSP itself is pinned in `packages/dsp`'s `unit_mastering_dyneq.rs`,
including the decaying-broadband-strike case the stage was designed against;
what is checked here is that the stage is absent unless bands are given, that
it lands between the static EQ and the compressor, and that a manifest band
list survives validation and reaches the config field the chain reads.
"""
from __future__ import annotations

import numpy as np
import pytest

import upmixer.mastering.dyneq  # noqa: F401
from upmixer.config import UpmixConfig
from upmixer.formats import FORMAT_MAP
from upmixer.manifest import ManifestError, parse_manifest, validate_manifest
from upmixer.mastering import MasteringChain

SR = 48_000

BAND = {
    "freq_hz": 3800.0,
    "q": 2.0,
    "threshold_db": -30.0,
    "ratio": 4.0,
    "attack_ms": 10.0,
    "release_ms": 150.0,
}


def _bed(freq: float = 3800.0, amplitude: float = 0.5, n: int = SR) -> dict[str, np.ndarray]:
    t = np.arange(n) / SR
    sig = amplitude * np.sin(2 * np.pi * freq * t)
    return {name: sig.copy() for name in [label.value for label in FORMAT_MAP["5.1"].channels]}


def _dense_bed(seconds: int = 5, channels: int = 6) -> list[np.ndarray]:
    """A dense bed at the level the profile thresholds are calibrated for.

    Correlated low end and decorrelated highs, the way routing produces them,
    plus percussive hits — without transients the high bands have nothing to
    flare on and the whole calibration is untested. Scaled to a full-band
    linked RMS of −20 dBFS, which is what COMP_PROFILES assume at this same
    point in the chain.

    It also carries a passage envelope. A stationary fixture makes the low
    bands look inert — their level varies by well under a dB — which says more
    about the fixture than about the stage: real programme moves 6-10 dB
    between a verse and a chorus, and that is the swing a low-band preset is
    there to catch.
    """
    rng = np.random.default_rng(7)
    n = SR * seconds
    t = np.arange(n) / SR

    def pink() -> np.ndarray:
        spectrum = np.fft.rfft(rng.standard_normal(n))
        freqs = np.fft.rfftfreq(n, 1 / SR)
        spectrum[1:] /= np.sqrt(freqs[1:])
        spectrum[0] = 0
        return np.fft.irfft(spectrum, n)

    shared = pink() + 0.5 * np.sin(2 * np.pi * 110 * t) + 0.25 * np.sin(2 * np.pi * 220 * t)
    for onset in range(0, n - SR, SR // 2):
        decay = np.exp(-np.arange(4800) / 900)
        shared[onset:onset + 4800] += rng.standard_normal(4800) * decay * 1.2

    # Quiet passage, loud passage, back off — an 8 dB swing over the take.
    passage = np.interp(
        t, [0, seconds * 0.3, seconds * 0.45, seconds * 0.8, seconds], [0.4, 0.4, 1.0, 1.0, 0.55]
    )
    bed = [(shared * 0.7 + pink() * 0.6) * passage for _ in range(channels)]
    linked = np.sqrt((np.asarray(bed) ** 2).mean(axis=0))
    scale = 10 ** (-20 / 20) / np.sqrt((linked ** 2).mean())
    return [channel * scale for channel in bed]


def chain_output(cfg: UpmixConfig, channels: dict[str, np.ndarray]):
    return MasteringChain(cfg).process(channels, SR, FORMAT_MAP["5.1"])


def _manifest(block: dict) -> dict:
    return {
        "version": "1.0.0",
        "assets": [{"input": "in.wav", "output": "out.wav"}],
        "mastering": {"dynamic_eq": block},
    }


class TestChainWiring:
    def test_no_bands_is_the_stage_absent(self):
        cfg = UpmixConfig(loudness_normalize=False)
        assert cfg.mastering_dyneq_bands is None
        plain, _ = chain_output(cfg, _bed())
        empty, _ = chain_output(
            UpmixConfig(loudness_normalize=False, mastering_dyneq_bands=[]), _bed()
        )
        assert np.array_equal(plain["FL"], empty["FL"])

    def test_a_triggered_band_cuts_the_mains_and_leaves_lfe(self):
        cfg = UpmixConfig(loudness_normalize=False, mastering_dyneq_bands=[BAND])
        cut, _ = chain_output(cfg, _bed())
        plain, _ = chain_output(UpmixConfig(loudness_normalize=False), _bed())
        assert np.sqrt((cut["FL"] ** 2).mean()) < 0.5 * np.sqrt((plain["FL"] ** 2).mean())
        assert np.allclose(cut["LFE"], plain["LFE"])

    def test_a_band_below_its_threshold_changes_nothing(self):
        quiet = _bed(amplitude=0.001)
        cfg = UpmixConfig(loudness_normalize=False, mastering_dyneq_bands=[BAND])
        out, _ = chain_output(cfg, quiet)
        plain, _ = chain_output(UpmixConfig(loudness_normalize=False), quiet)
        assert np.array_equal(out["FL"], plain["FL"])

    def test_it_runs_ahead_of_the_compressor(self):
        # The stage is contracted to sit before the glue: cutting the band
        # first leaves the compressor less to work against, so the bed comes
        # out louder than it would if the order were reversed.
        base = dict(loudness_normalize=False, mastering_comp_profile="transparent")
        with_dyneq, _ = chain_output(
            UpmixConfig(**base, mastering_dyneq_bands=[BAND]), _bed()
        )
        without, _ = chain_output(UpmixConfig(**base), _bed())
        assert np.abs(with_dyneq["FL"]).max() < np.abs(without["FL"]).max()


class TestProfiles:
    def test_a_profile_resolves_to_its_bands(self):
        from upmixer.mastering.dyneq import DYNEQ_PROFILES, resolve_dyneq_bands

        assert resolve_dyneq_bands("tame-harshness", None) == DYNEQ_PROFILES["tame-harshness"]
        assert resolve_dyneq_bands(None, None) == []
        assert resolve_dyneq_bands("no-such-profile", None) == []

    def test_explicit_bands_beat_the_profile(self):
        from upmixer.mastering.dyneq import resolve_dyneq_bands

        assert resolve_dyneq_bands("tame-harshness", [BAND]) == [BAND]

    def test_resolving_hands_back_a_copy_the_caller_cannot_corrupt(self):
        from upmixer.mastering.dyneq import DYNEQ_PROFILES, resolve_dyneq_bands

        resolve_dyneq_bands("tame-harshness", None)[0]["ratio"] = 99.0
        assert DYNEQ_PROFILES["tame-harshness"][0]["ratio"] != 99.0

    def test_every_profile_is_a_valid_manifest_band_list(self):
        """The presets are authored by hand; nothing else checks them against
        the bounds the manifest enforces on a user's own bands."""
        from upmixer.mastering.dyneq import DYNEQ_PROFILES

        for name, bands in DYNEQ_PROFILES.items():
            validate_manifest(_manifest({"bands": bands})), name

    def test_a_profile_reaches_the_chain_through_the_manifest(self):
        data = _manifest({"profile": "tame-harshness"})
        validate_manifest(data)
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig(**jobs[0].config)
        assert cfg.mastering_dyneq_profile == "tame-harshness"

        cut, _ = chain_output(UpmixConfig(**jobs[0].config, loudness_normalize=False), _bed())
        plain, _ = chain_output(UpmixConfig(loudness_normalize=False), _bed())
        assert np.sqrt((cut["FL"] ** 2).mean()) < np.sqrt((plain["FL"] ** 2).mean())

    def test_an_unknown_profile_is_rejected(self):
        with pytest.raises(ManifestError):
            validate_manifest(_manifest({"profile": "not-a-profile"}))

    def test_every_profile_engages_without_crushing_a_dense_bed(self):
        """The thresholds are absolute dBFS on the pre-normalization bed, so
        they are only meaningful against a level. This pins the calibration
        the profile table's docstring claims: at the bed level COMP_PROFILES
        also assume, every band acts, and none of them acts hard."""
        import upmixer_dsp
        from upmixer.mastering.dyneq import BAND_FIELDS, DYNEQ_PROFILES

        bed = _dense_bed()
        linked = np.sqrt((np.asarray(bed) ** 2).mean(axis=0))
        level_db = 20 * np.log10(np.sqrt((linked ** 2).mean()))
        assert level_db == pytest.approx(-20.0, abs=0.5), "fixture level drifted"

        for name, bands in DYNEQ_PROFILES.items():
            _, cuts = upmixer_dsp.dynamic_eq(
                [np.ascontiguousarray(c) for c in bed],
                None,
                SR,
                [tuple(float(b[f]) for f in BAND_FIELDS) for b in bands],
            )
            for band, cut in zip(bands, cuts):
                assert 0.5 < cut < 12.0, (
                    f"{name} @ {band['freq_hz']:.0f} Hz cut {cut:.2f} dB — a band that "
                    f"never engages is dead weight, one that engages hard is not surgical"
                )


class TestManifestBlock:
    def test_bands_reach_the_config_field_the_chain_reads(self):
        data = _manifest({"bands": [BAND]})
        validate_manifest(data)
        _, jobs = parse_manifest(data)
        cfg = UpmixConfig(**jobs[0].config)
        assert cfg.mastering_dyneq_bands == [BAND]

    @pytest.mark.parametrize(
        "band",
        [
            {**BAND, "freq_hz": 30000.0},
            {**BAND, "q": 0.0},
            {**BAND, "ratio": 100.0},
            {k: v for k, v in BAND.items() if k != "attack_ms"},
            {**BAND, "shape": "bell"},
        ],
    )
    def test_a_malformed_band_is_rejected(self, band):
        with pytest.raises(ManifestError):
            validate_manifest(_manifest({"bands": [band]}))

    def test_more_than_four_bands_is_rejected(self):
        with pytest.raises(ManifestError):
            validate_manifest(_manifest({"bands": [BAND] * 5}))
