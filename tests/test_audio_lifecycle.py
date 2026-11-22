from celia.core.audio_lifecycle import record_audio_lifecycle
from celia.core.observability import observability


def test_correlated_lifecycle_is_recorded_without_payload(monkeypatch):
    calls = []
    monkeypatch.setattr(observability, "emit", lambda *args: calls.append(args))
    assert record_audio_lifecycle({"event": "AUDIO_STARTED", "speech_id": "speech-1", "audio": "private", "text": "private"})
    assert calls == [("AUDIO_STARTED", "audio.lifecycle", "ok", {"speech_id": "speech-1", "reason": "", "source": "frontend"})]


def test_invalid_telemetry_never_raises_or_changes_state(monkeypatch):
    calls = []
    monkeypatch.setattr(observability, "emit", lambda *args: calls.append(args))
    for event, speech_id in [("unknown", "id"), ([], "id"), ("AUDIO_STARTED", ""), ("AUDIO_STARTED", "x" * 129)]:
        assert not record_audio_lifecycle({"event": event, "speech_id": speech_id})
    assert not calls
