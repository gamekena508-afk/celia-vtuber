from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_lipsync_uses_existing_audio_envelope_and_caps_mouth_opening():
    source = (ROOT / "frontend" / "celia-lipsync.js").read_text()
    assert "ParamMouthOpenY" in source
    assert "ParamMouthForm" in source
    assert "const MAX_OPEN = 0.58" in source
    assert "const SILENCE_GATE = 0.035" in source
    assert "Math.pow(intensity, 0.78)" in source
    assert "celia-lipsync-start" in source
    assert "celia-lipsync-stop" in source
    assert "simpleViseme" in source


def test_playback_bridge_forwards_speech_envelope_to_lipsync_without_touching_tts():
    source = (ROOT / "frontend" / "external-playback-bridge.js").read_text()
    assert "payload.volumes" in source
    assert "sliceLengthMs" in source
    assert '"celia-lipsync-start"' in source
    assert '"celia-lipsync-stop"' in source
