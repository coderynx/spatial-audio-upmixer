import pytest

pytest.importorskip("fastapi")
pytest.importorskip("sqlalchemy")

from upmixer_web.shared.separation import separation_capability
from upmixer_web.shared.storage import LocalObjectStorage


def test_local_storage_rejects_parent_path(tmp_path):
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="relative path"):
        storage.local_path("../escape.wav")


def test_configuration_lists_every_stem_and_runtime_capability(web_client):
    response = web_client.get("/api/v1/configuration")
    assert response.status_code == 200
    configuration = response.json()
    assert configuration["choices"]["stems"] == [
        "Vocals", "Bass", "Drums", "Guitar", "Piano", "Other",
        "Kick", "Snare", "Toms", "Hi-Hat", "Ride", "Crash", "Crowd",
        "Lead Vocals", "Backing Vocals", "Vocals Reverb",
    ]
    assert "vocal-presence" in configuration["choices"]["stem_eq_profiles"]
    assert configuration["choices"]["stem_phase_fix_reference_models"] == [
        "kimmel_unwa_ft2_bleedless.ckpt",
    ]
    assert (
        "mel_band_roformer_bleed_suppressor_v1.ckpt"
        in configuration["choices"]["stem_debleed_models"]
    )
    capability = configuration["capabilities"]["stem_separation"]
    assert isinstance(capability["available"], bool)
    assert isinstance(capability["accelerated"], bool)
    assert isinstance(capability["accelerator_detected"], bool)
    assert capability["accelerator_issue"] is None or isinstance(
        capability["accelerator_issue"],
        str,
    )
    assert capability["platform"]


def test_configuration_serves_engine_constants(web_client):
    from upmixer.config import UpmixConfig
    from upmixer.mastering.compressor import COMP_PROFILES

    response = web_client.get("/api/v1/configuration")
    assert response.status_code == 200
    constants = response.json()["constants"]

    expected_keys = {
        "channel_group_gains", "lfe_gain", "lfe_lowpass_hz", "surround_bass_cutoff_hz",
        "height_low_rolloff_hz", "height_low_rolloff_gain", "height_crossover_hz",
        "height_high_shelf_gain", "height_directional_band_hz", "height_directional_band_gain",
        "stem_transient_duck",
        "soft_limit_threshold", "limiter_lookahead_ms",
        "limiter_release_ms", "safety_margin_db", "loudness_max_gain_db", "surround_downmix_coeff",
        "height_downmix_coeff",
        "itu_center_coeff", "speaker_directions",
        "dyneq_profiles",
        "comp_profiles", "bass_profiles", "delivery_targets", "delivery_default",
        "bass_sub_cutoff_hz", "bass_mid_cutoff_hz",
        "bass_excite_blend", "bass_excite_drive", "bass_lf_spreads",
        "bass_punch_fast_ms", "bass_punch_slow_ms", "bass_punch_max_db",
        "bass_decorr_low_hz", "bass_decorr_high_hz", "bass_decorr_sections",
        "bass_decorr_max_delay_ms", "bass_decorr_fast_ms", "bass_decorr_slow_ms",
        "binaural_loudness_max_gain_db",
        "crosstalk_loudness_max_gain_db", "voicing_params", "transaural_voicing_params",
        "eq_fir_assets", "stem_eq_fir_assets", "decode_filter_set", "xtc_filter_set",
    }
    assert set(constants) == expected_keys

    # Served straight from core source modules (never re-typed): a couple of
    # spot-checks that the assembler reads the real values.
    cfg = UpmixConfig()
    assert constants["lfe_gain"] == cfg.lfe_gain
    assert constants["channel_group_gains"]["center"] == cfg.center_gain
    assert constants["comp_profiles"]["transparent"]["threshold_db"] == (
        COMP_PROFILES["transparent"]["threshold_db"]
    )
    # Voicing tables serialize the frozen dataclass in snake_case.
    listening = constants["voicing_params"]["listening"]
    assert listening["crossfeed_amount"] == 0.10
    assert listening["loudness_target_lkfs"] is None
    assert set(constants["transaural_voicing_params"]) == {
        "stereo", "smart_speaker", "car", "laptop", "phone",
    }
    # Delivery targets carry their tolerance so the web can render pass/fail.
    assert constants["delivery_targets"]["ebu-r128"] == {
        "target_lkfs": -23.0, "max_tp_dbtp": -1.0, "tolerance_lu": 0.5,
    }
    # The pair an unset target/ceiling resolves to, so the web never authors it.
    assert constants["delivery_default"] == {
        "target_lkfs": -18.0, "max_tp_dbtp": -1.0, "tolerance_lu": None,
    }


def test_configuration_serves_filter_asset_maps(web_client):
    from upmixer.binaural.profiles import DECODE_FILTER_SET
    from upmixer.crosstalk.profiles import XTC_FILTER_SET
    from upmixer.mastering.eq import EQ_FIR_ASSETS
    from upmixer.separation.stem_eq import STEM_EQ_FIR_ASSETS

    response = web_client.get("/api/v1/configuration")
    assert response.status_code == 200
    constants = response.json()["constants"]

    assert constants["eq_fir_assets"] == EQ_FIR_ASSETS
    assert constants["stem_eq_fir_assets"] == STEM_EQ_FIR_ASSETS
    assert constants["decode_filter_set"] == {p.value: n for p, n in DECODE_FILTER_SET.items()}
    assert constants["xtc_filter_set"] == {p.value: n for p, n in XTC_FILTER_SET.items()}


def test_served_filter_assets_have_shipped_wavs(web_client):
    from pathlib import Path

    response = web_client.get("/api/v1/configuration")
    constants = response.json()["constants"]

    repo_root = Path(__file__).resolve().parents[3]
    public = repo_root / "apps" / "web" / "public"
    checks = [
        ("eq_fir_assets", "eq_fir"),
        ("stem_eq_fir_assets", "eq_fir"),
        ("decode_filter_set", "hrir"),
        ("xtc_filter_set", "xtc"),
    ]
    for key, subdir in checks:
        for basename in constants[key].values():
            matches = list((public / subdir).glob(f"{basename}*.wav"))
            assert matches, f"no shipped WAV for {basename} under {subdir}/"


def test_capability_uses_engine_selected_device(tmp_path, monkeypatch):
    class FakeStemSeparator:
        def __init__(self, **_kwargs):
            pass

        @property
        def backend(self):
            return "mps"

    monkeypatch.setattr(
        "upmixer_web.shared.separation.importlib.util.find_spec",
        lambda _name: object(),
    )
    monkeypatch.setattr(
        "upmixer.separation.separator.StemSeparator", FakeStemSeparator,
    )

    capability = separation_capability(tmp_path)

    assert capability["available"]
    assert capability["backend"] == "mps"
    assert capability["accelerated"]


def test_capability_rejects_unsupported_torch_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr("upmixer_web.shared.separation.sys.version_info", (3, 14, 0))
    monkeypatch.setattr(
        "upmixer_web.shared.separation.importlib.util.find_spec",
        lambda _name: pytest.fail("torch must not load on Python 3.14"),
    )

    capability = separation_capability(tmp_path)

    assert not capability["available"]
    assert capability["install_message"] == (
        "Stem separation is unavailable on Python 3.14 or newer. "
        "Use Python 3.11, 3.12, or 3.13."
    )


def test_configuration_offers_stereo_as_a_selectable_layout(web_client):
    choices = web_client.get("/api/v1/configuration").json()["choices"]
    assert "stereo" in choices["channel_layouts"]
    assert choices["layout_channels"]["stereo"] == ["FL", "FR"]
    assert "stereo" not in choices["binaural_beds"]
    assert "stereo" not in choices["transaural_beds"]


def test_configuration_serves_the_delivery_codec_capabilities(web_client):
    choices = web_client.get("/api/v1/configuration").json()["choices"]

    assert choices["output_types"] == ["multichannel", "adm-bwf", "binaural", "transaural"]
    codecs = {entry["name"]: entry for entry in choices["output_codecs"]}
    assert set(codecs) == {"wav_pcm", "flac", "ogg_vorbis", "ogg_opus"}
    assert codecs["wav_pcm"]["extension"] == ".wav"
    assert codecs["wav_pcm"]["max_channels"] is None
    # The two limits the client has to gate on.
    assert codecs["flac"]["max_channels"] == 8
    assert codecs["flac"]["subtypes"] == ["PCM_16", "PCM_24"]
    assert codecs["ogg_opus"]["sample_rates"] == [8000, 12000, 16000, 24000, 48000]
    assert codecs["ogg_vorbis"]["sample_rates"] is None


def test_stem_routing_resolve_returns_the_layout_own_channels(web_client):
    payload = {"stems": ["Vocals", "Drums"], "channel_layout": "5.1", "preset": "wide"}

    routing = web_client.post("/api/v1/stem-routing/resolve", json=payload).json()

    assert set(routing) == {"Vocals", "Drums"}
    assert set(routing["Vocals"]) <= {"FL", "FR", "C", "LFE", "SL", "SR"}
    assert routing["Vocals"]["C"] > 0.0


def test_stem_routing_resolve_serves_the_wet_vocal_stem(web_client):
    payload = {
        "stems": ["Vocals", "Vocals Reverb"],
        "channel_layout": "7.1.4",
        "preset": "balanced",
    }

    routing = web_client.post("/api/v1/stem-routing/resolve", json=payload).json()

    wet = routing["Vocals Reverb"]
    assert wet["SL"] > 0.0 and wet["TBL"] > 0.0
    assert wet.get("C", 0.0) < routing["Vocals"]["C"]


def test_stem_routing_resolve_rejects_unknown_preset_and_layout(web_client):
    unknown_preset = web_client.post(
        "/api/v1/stem-routing/resolve",
        json={"stems": ["Vocals"], "channel_layout": "7.1.4", "preset": "spacious"},
    )
    unknown_layout = web_client.post(
        "/api/v1/stem-routing/resolve",
        json={"stems": ["Vocals"], "channel_layout": "9.1.6", "preset": "balanced"},
    )

    assert unknown_preset.status_code == 422
    assert "Unknown stem routing preset" in unknown_preset.json()["detail"]
    assert unknown_layout.status_code == 422
