import numpy as np
import pytest
import soundfile as sf

from upmixer.eval.corpus import synthetic_corpus
from upmixer.eval.harness import RunSettings, evaluate_corpus
from upmixer.eval.metrics import bleedless, fullness, sdr
from upmixer.eval.report import format_report


def _stereo(mono):
    return np.stack([mono, mono], axis=1).astype(np.float32)


@pytest.fixture
def tone(sample_rate, duration):
    t = np.arange(int(sample_rate * duration)) / sample_rate
    return _stereo(0.4 * np.sin(2 * np.pi * 330 * t))


def test_sdr_identical_signals_is_high(tone):
    assert sdr(tone, tone) > 100.0


def test_sdr_degrades_with_added_noise(tone):
    rng = np.random.default_rng(1)
    noisy = tone + 0.1 * rng.standard_normal(tone.shape).astype(np.float32)
    assert sdr(tone, noisy) < sdr(tone, tone)


def test_sdr_truncates_to_shared_length(tone):
    shorter = tone[: len(tone) // 2]
    assert np.isfinite(sdr(tone, shorter))


def test_sdr_rejects_channel_mismatch(tone):
    mono = tone[:, :1]
    with pytest.raises(ValueError):
        sdr(tone, mono)


def test_fullness_perfect_match_is_one(tone, sample_rate):
    assert fullness(tone, tone, sample_rate) == pytest.approx(1.0, abs=1e-6)


def test_fullness_drops_with_attenuation(tone, sample_rate):
    attenuated = tone * 0.3
    assert fullness(tone, attenuated, sample_rate) < 1.0


def test_bleedless_perfect_match_is_one(tone, sample_rate):
    assert bleedless(tone, tone, sample_rate) == pytest.approx(1.0, abs=1e-6)


def test_bleedless_drops_with_foreign_energy(tone, sample_rate):
    rng = np.random.default_rng(2)
    bled = tone + 0.5 * _stereo(rng.standard_normal(tone.shape[0]).astype(np.float32))
    assert bleedless(tone, bled, sample_rate) < 1.0


def test_evaluate_corpus_reports_sdr_fullness_and_bleedless(tmp_path):
    corpus = synthetic_corpus(sample_rate=22050, out_dir=str(tmp_path / "corpus"))

    def fake_separate(mixture_path):
        item = next(i for i in corpus.items if i.mixture == mixture_path)
        stems = {}
        for name, ref_path in item.stems.items():
            ref, _ = sf.read(ref_path, dtype="float32", always_2d=True)
            stems[name] = ref
        settings = RunSettings(model="identity-test", sample_rate=22050)
        return stems, settings

    report = evaluate_corpus(corpus, fake_separate, sample_rate=22050)

    assert report.scores, "expected at least one scored stem"
    by_stem = report.by_stem()
    by_category = report.by_category()
    assert set(by_category) == {"default", "dense_synth", "choir_cluster"}
    for mean_sdr, mean_fullness, mean_bleedless in by_stem.values():
        assert mean_sdr > 80.0
        assert mean_fullness == pytest.approx(1.0, abs=1e-6)
        assert mean_bleedless == pytest.approx(1.0, abs=1e-6)

    text = format_report(report)
    assert "SDR" in text and "fullness" in text and "bleedless" in text
    assert "identity-test" in text
