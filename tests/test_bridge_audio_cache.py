from pathlib import Path

from celia import bridge_routes


def test_legacy_relative_tts_audio_is_promoted_to_the_served_cache(tmp_path, monkeypatch):
    root = tmp_path / "celia"
    legacy = root / "cache"
    canonical = root / "var" / "cache"
    legacy.mkdir(parents=True)
    source = legacy / "voice.wav"
    source.write_bytes(b"RIFFtestWAVE")
    monkeypatch.setattr(bridge_routes, "RAIZ", root)
    monkeypatch.setattr(bridge_routes, "CACHE", canonical)

    resolved = Path(bridge_routes._serve_generated_audio("cache/voice.wav"))

    assert resolved == canonical / "voice.wav"
    assert resolved.read_bytes() == b"RIFFtestWAVE"
    assert not source.exists()
