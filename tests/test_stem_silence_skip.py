"""Tests for silence-skip stem separation optimisation.

Covers:
  - find_active_spans  (silence.py)
  - stitch_with_crossfade  (silence.py)
  - StemUpmixPipeline._execute_plan_with_silence_skip  (stem_pipeline.py)
  - _cache_key  invalidation on silence-skip parameter changes  (stem_cache.py)
"""
from __future__ import annotations

from unittest.mock import patch

import numpy as np
import pytest

from upmixer.separation.silence import (
    find_active_spans,
    stitch_with_crossfade,
)


SR = 48_000


def _silence(n: int) -> np.ndarray:
    return np.zeros((n, 2), dtype=np.float64)


def _sine(n: int, amp: float = 0.5, freq: float = 440.0) -> np.ndarray:
    t = np.linspace(0, n / SR, n, endpoint=False)
    ch = (amp * np.sin(2 * np.pi * freq * t)).astype(np.float64)
    return np.column_stack([ch, ch])


# ---------------------------------------------------------------------------
# find_active_spans
# ---------------------------------------------------------------------------

class TestFindActiveSpans:
    def test_all_silence_returns_empty(self):
        audio = _silence(SR * 5)
        spans = find_active_spans(audio, SR)
        assert spans == []

    def test_all_active_returns_full_span(self):
        audio = _sine(SR * 5)
        spans = find_active_spans(audio, SR)
        assert len(spans) == 1
        assert spans[0] == (0, SR * 5)

    def test_zero_length_audio_returns_empty(self):
        audio = np.zeros((0, 2), dtype=np.float64)
        spans = find_active_spans(audio, SR)
        assert spans == []

    def test_mono_input_accepted(self):
        audio = _sine(SR * 5)[:, 0]
        spans = find_active_spans(audio, SR)
        assert len(spans) == 1

    def test_active_after_silence_boundary_detected(self):
        silence_s = 4.0
        active_s = 4.0
        n_sil = int(silence_s * SR)
        n_act = int(active_s * SR)
        audio = np.vstack([_silence(n_sil), _sine(n_act)])
        spans = find_active_spans(
            audio, SR,
            threshold_db=-90.0,
            min_silence_s=2.0,
            pad_ms=0.0,
            min_active_s=0.0,
        )
        assert len(spans) == 1
        s, e = spans[0]
        hop = max(1, int(0.010 * SR))
        assert abs(s - n_sil) <= hop * 2
        assert e == n_sil + n_act

    def test_short_gap_merged_into_single_span(self):
        n_act = int(3.0 * SR)
        n_gap = int(0.5 * SR)
        audio = np.vstack([_sine(n_act), _silence(n_gap), _sine(n_act)])
        spans = find_active_spans(
            audio, SR,
            threshold_db=-90.0,
            min_silence_s=2.0,
            pad_ms=0.0,
            min_active_s=0.0,
        )
        assert len(spans) == 1

    def test_long_gap_produces_two_spans(self):
        n_act = int(3.0 * SR)
        n_gap = int(4.0 * SR)
        audio = np.vstack([_sine(n_act), _silence(n_gap), _sine(n_act)])
        spans = find_active_spans(
            audio, SR,
            threshold_db=-90.0,
            min_silence_s=2.0,
            pad_ms=0.0,
            min_active_s=0.0,
        )
        assert len(spans) == 2

    def test_padding_extends_span_into_silence(self):
        n_sil = int(3.0 * SR)
        n_act = int(4.0 * SR)
        audio = np.vstack([_silence(n_sil), _sine(n_act)])
        pad_ms = 200.0
        spans = find_active_spans(
            audio, SR,
            threshold_db=-90.0,
            min_silence_s=2.0,
            pad_ms=pad_ms,
            min_active_s=0.0,
        )
        assert len(spans) == 1
        s, _ = spans[0]
        pad_samp = int(pad_ms / 1000.0 * SR)
        assert s < n_sil
        assert s >= n_sil - pad_samp - int(0.010 * SR)

    def test_signal_at_threshold_treated_as_active(self):
        threshold_db = -60.0
        amp = 10 ** (threshold_db / 20.0) * 1.01
        audio = _sine(SR * 3, amp=amp)
        spans = find_active_spans(audio, SR, threshold_db=threshold_db, min_active_s=0.0)
        assert len(spans) == 1

    def test_signal_below_threshold_treated_as_silent(self):
        threshold_db = -60.0
        amp = 10 ** (threshold_db / 20.0) * 0.99
        audio = _sine(SR * 3, amp=amp)
        spans = find_active_spans(audio, SR, threshold_db=threshold_db, min_active_s=0.0)
        assert spans == []

    def test_min_active_s_expands_short_span(self):
        n_sil_pre = int(4.0 * SR)
        n_act = int(1.0 * SR)
        n_sil_post = int(4.0 * SR)
        audio = np.vstack([_silence(n_sil_pre), _sine(n_act), _silence(n_sil_post)])
        spans = find_active_spans(
            audio, SR,
            threshold_db=-90.0,
            min_silence_s=0.1,
            pad_ms=0.0,
            min_active_s=5.0,
        )
        assert len(spans) == 1
        s, e = spans[0]
        assert (e - s) >= int(5.0 * SR) - 2

    def test_spans_non_overlapping_and_sorted(self):
        blocks = [_sine(int(3.0 * SR)), _silence(int(3.0 * SR))] * 3
        audio = np.vstack(blocks)
        spans = find_active_spans(
            audio, SR,
            threshold_db=-90.0,
            min_silence_s=2.0,
            pad_ms=0.0,
            min_active_s=0.0,
        )
        for i in range(len(spans) - 1):
            assert spans[i][1] <= spans[i + 1][0]


# ---------------------------------------------------------------------------
# stitch_with_crossfade
# ---------------------------------------------------------------------------

class TestStitchWithCrossfade:
    def test_output_shape_matches_total_length(self):
        audio = _sine(SR).astype(np.float32)
        out = stitch_with_crossfade([(0, SR, audio)], total_length=SR * 2, fade_samples=480)
        assert out.shape == (SR * 2, 2)
        assert out.dtype == np.float32

    def test_silent_region_is_exactly_zero(self):
        audio = _sine(SR).astype(np.float32)
        out = stitch_with_crossfade([(SR, SR * 2, audio)], total_length=SR * 3, fade_samples=0)
        assert np.all(out[:SR] == 0.0)
        assert np.all(out[SR * 2:] == 0.0)

    def test_active_region_content_preserved(self):
        n = SR
        audio = _sine(n).astype(np.float32)
        out = stitch_with_crossfade([(0, n, audio)], total_length=n, fade_samples=0)
        np.testing.assert_array_equal(out, audio)

    def test_fade_in_ramps_from_zero(self):
        n = SR
        audio = np.ones((n, 2), dtype=np.float32)
        fade = 480
        out = stitch_with_crossfade([(0, n, audio)], total_length=n, fade_samples=fade)
        assert abs(float(out[0, 0])) < 1e-6
        assert abs(float(out[fade - 1, 0]) - 1.0) < 0.05

    def test_fade_out_ramps_to_zero(self):
        n = SR
        audio = np.ones((n, 2), dtype=np.float32)
        fade = 480
        out = stitch_with_crossfade([(0, n, audio)], total_length=n, fade_samples=fade)
        assert abs(float(out[-1, 0])) < 1e-6
        assert abs(float(out[n - fade, 0]) - 1.0) < 0.05

    def test_fade_monotonic(self):
        n = SR
        fade = 480
        audio = np.ones((n, 2), dtype=np.float32)
        out = stitch_with_crossfade([(0, n, audio)], total_length=n, fade_samples=fade)
        assert np.all(np.diff(out[:fade, 0]) >= -1e-7)
        assert np.all(np.diff(out[n - fade:, 0]) <= 1e-7)

    def test_no_fade_when_fade_samples_zero(self):
        n = 100
        audio = np.ones((n, 2), dtype=np.float32)
        out = stitch_with_crossfade([(0, n, audio)], total_length=n, fade_samples=0)
        np.testing.assert_array_equal(out, audio)

    def test_short_audio_zero_padded_to_span_length(self):
        span_len = 100
        audio = np.ones((50, 2), dtype=np.float32)
        out = stitch_with_crossfade([(0, span_len, audio)], total_length=span_len, fade_samples=0)
        assert out.shape[0] == span_len
        assert np.all(out[50:] == 0.0)

    def test_long_audio_truncated_to_span_length(self):
        span_len = 50
        audio = np.ones((200, 2), dtype=np.float32)
        out = stitch_with_crossfade([(0, span_len, audio)], total_length=span_len, fade_samples=0)
        assert out.shape[0] == span_len

    def test_empty_span_list_returns_zeros(self):
        out = stitch_with_crossfade([], total_length=SR, fade_samples=480)
        assert out.shape == (SR, 2)
        assert np.all(out == 0.0)


# ---------------------------------------------------------------------------
# _execute_plan_with_silence_skip
# ---------------------------------------------------------------------------

class TestExecutePlanWithSilenceSkip:
    """Uses a mock _execute_plan so no real separator is needed."""

    def _make_plan(self):
        from upmixer.separation.stem_plan import (
            SeparationPlan,
            SeparationTask,
            MODEL_PRIMARY,
            PRIMARY_OUTPUT_STEMS,
        )
        stems = frozenset({"Vocals", "Bass", "Drums", "Other"})
        task = SeparationTask(
            model=MODEL_PRIMARY,
            input_source="original",
            output_stems=PRIMARY_OUTPUT_STEMS,
            keep_stems=stems,
        )
        import hashlib
        h = hashlib.sha256(",".join(sorted(stems)).encode()).hexdigest()[:20]
        return SeparationPlan(tasks=[task], requested_stems=stems, stems_hash=h)

    def _make_pipeline(self):
        from upmixer.separation.stem_pipeline import StemUpmixPipeline
        from upmixer.config import UpmixConfig
        cfg = UpmixConfig()
        cfg.stem_silence_skip = True
        cfg.stem_silence_threshold_db = -90.0
        cfg.stem_silence_min_duration_s = 2.0
        cfg.stem_silence_crossfade_ms = 10.0
        cfg.stem_silence_pad_ms = 0.0
        return StemUpmixPipeline(cfg), cfg

    def _fake_execute_plan(self, plan, sep_path, sep_sr):
        """Return constant float32 arrays shaped (n, 2) based on WAV duration."""
        import soundfile as sf_mod
        audio, sr = sf_mod.read(sep_path, dtype="float32", always_2d=True)
        n = len(audio)
        return {name: np.full((n, 2), 0.25, dtype=np.float32) for name in plan.requested_stems}

    def test_all_silent_returns_zeros_skips_plan(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        n = SR * 5
        zone_audio = _silence(n)
        call_count = {"n": 0}

        def mock_execute(p, path, sr_val):
            call_count["n"] += 1
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            result = pipeline._execute_plan_with_silence_skip(plan, zone_audio, SR, SR, cfg)

        assert call_count["n"] == 0
        for arr in result.values():
            assert np.all(arr == 0.0)
            assert arr.dtype == np.float32

    def test_all_active_fast_path_calls_plan_once(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        n = SR * 5
        zone_audio = _sine(n)
        call_count = {"n": 0}

        def mock_execute(p, path, sr_val):
            call_count["n"] += 1
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            result = pipeline._execute_plan_with_silence_skip(plan, zone_audio, SR, SR, cfg)

        assert call_count["n"] == 1
        for arr in result.values():
            assert arr.shape[0] == n

    def test_all_active_uses_original_source_path(self, tmp_path):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        zone_audio = _sine(SR * 5)
        source = str(tmp_path / "source.wav")
        import soundfile as sf_mod
        sf_mod.write(source, zone_audio, SR, subtype="FLOAT")
        seen = []

        def mock_execute(p, path, sr_val):
            seen.append(path)
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            pipeline._execute_plan_with_silence_skip(
                plan, zone_audio, SR, SR, cfg, original_path=source,
            )

        assert seen == [source]

    def test_generated_full_active_wav_preserves_float_samples(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        zone_audio = _sine(SR * 5)
        subtypes = []

        def mock_execute(p, path, sr_val):
            import soundfile as sf_mod
            subtypes.append(sf_mod.info(path).subtype)
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            pipeline._execute_plan_with_silence_skip(
                plan, zone_audio, SR, SR, cfg,
            )

        assert subtypes == ["FLOAT"]

    def test_silent_head_zeros_in_silent_region(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        n_sil = int(4.0 * SR)
        n_act = int(6.0 * SR)
        zone_audio = np.vstack([_silence(n_sil), _sine(n_act)])

        def mock_execute(p, path, sr_val):
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            result = pipeline._execute_plan_with_silence_skip(plan, zone_audio, SR, SR, cfg)

        hop_len = max(1, int(0.010 * SR))
        for arr in result.values():
            assert arr.shape[0] == n_sil + n_act
            assert np.all(arr[:n_sil - hop_len] == 0.0), "head should be zero"

    def test_silence_skip_equivalent_when_no_silence(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        n = SR * 5
        zone_audio = _sine(n)

        def mock_execute(p, path, sr_val):
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            result_skip = pipeline._execute_plan_with_silence_skip(plan, zone_audio, SR, SR, cfg)

        cfg_no_skip = cfg.__class__()
        cfg_no_skip.stem_silence_skip = False

        import tempfile, soundfile as sf_mod
        tmp = tempfile.mktemp(suffix=".wav")
        sf_mod.write(tmp, zone_audio, SR, subtype="PCM_24")
        result_full = mock_execute(plan, tmp, SR)
        import os
        os.unlink(tmp)

        for name in plan.requested_stems:
            assert result_skip[name].shape == result_full[name].shape

    def test_multiple_active_spans_correct_length(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        n_act = int(3.0 * SR)
        n_sil = int(4.0 * SR)
        n_total = n_act + n_sil + n_act
        zone_audio = np.vstack([_sine(n_act), _silence(n_sil), _sine(n_act)])

        def mock_execute(p, path, sr_val):
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            result = pipeline._execute_plan_with_silence_skip(plan, zone_audio, SR, SR, cfg)

        for arr in result.values():
            assert arr.shape[0] == n_total

    def test_stems_all_present_in_output(self):
        pipeline, cfg = self._make_pipeline()
        plan = self._make_plan()
        zone_audio = _sine(SR * 5)

        def mock_execute(p, path, sr_val):
            return self._fake_execute_plan(p, path, sr_val)

        with patch.object(pipeline, "_execute_plan", side_effect=mock_execute):
            result = pipeline._execute_plan_with_silence_skip(plan, zone_audio, SR, SR, cfg)

        assert set(result.keys()) == plan.requested_stems


# ---------------------------------------------------------------------------
# _cache_key  invalidation on silence params
# ---------------------------------------------------------------------------

class TestCacheKeyInvalidation:
    def test_different_threshold_different_key(self, tmp_path):
        import soundfile as sf_mod
        wav = str(tmp_path / "x.wav")
        sf_mod.write(wav, np.zeros((1024, 2), dtype=np.float32), SR)
        from upmixer.separation.stem_cache import _cache_key
        k1 = _cache_key(wav, "abc", SR, silence_threshold_db=-90.0)
        k2 = _cache_key(wav, "abc", SR, silence_threshold_db=-60.0)
        assert k1 != k2

    def test_silence_skip_off_vs_on_different_key(self, tmp_path):
        import soundfile as sf_mod
        wav = str(tmp_path / "x.wav")
        sf_mod.write(wav, np.zeros((1024, 2), dtype=np.float32), SR)
        from upmixer.separation.stem_cache import _cache_key
        k1 = _cache_key(wav, "abc", SR, silence_skip=True)
        k2 = _cache_key(wav, "abc", SR, silence_skip=False)
        assert k1 != k2

    def test_different_min_duration_different_key(self, tmp_path):
        import soundfile as sf_mod
        wav = str(tmp_path / "x.wav")
        sf_mod.write(wav, np.zeros((1024, 2), dtype=np.float32), SR)
        from upmixer.separation.stem_cache import _cache_key
        k1 = _cache_key(wav, "abc", SR, silence_min_duration_s=2.0)
        k2 = _cache_key(wav, "abc", SR, silence_min_duration_s=5.0)
        assert k1 != k2

    def test_same_params_same_key(self, tmp_path):
        import soundfile as sf_mod
        wav = str(tmp_path / "x.wav")
        sf_mod.write(wav, np.zeros((1024, 2), dtype=np.float32), SR)
        from upmixer.separation.stem_cache import _cache_key
        k1 = _cache_key(wav, "abc", SR, silence_skip=True, silence_threshold_db=-90.0)
        k2 = _cache_key(wav, "abc", SR, silence_skip=True, silence_threshold_db=-90.0)
        assert k1 == k2

    def test_key_still_20_chars(self, tmp_path):
        import soundfile as sf_mod
        wav = str(tmp_path / "x.wav")
        sf_mod.write(wav, np.zeros((1024, 2), dtype=np.float32), SR)
        from upmixer.separation.stem_cache import _cache_key
        key = _cache_key(wav, "abc", SR, silence_skip=True, silence_threshold_db=-90.0)
        assert len(key) == 20
