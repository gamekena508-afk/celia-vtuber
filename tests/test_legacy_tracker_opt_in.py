from celia.core import emotion_bus, track_emotion_client


def _stub_bus(monkeypatch):
    calls = []

    def publish(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True, "emocao": args[0], "mudou": True}

    monkeypatch.setattr(emotion_bus.barramento, "publicar", publish)
    return calls


def test_default_emotion_delivery_uses_web_bus_without_legacy_tracker(monkeypatch):
    monkeypatch.delenv("CELIA_ENABLE_LEGACY_VTS_TRACKER", raising=False)
    bus_calls = _stub_bus(monkeypatch)

    def must_not_connect(*args, **kwargs):
        raise AssertionError("legacy tracker must not be contacted by default")

    monkeypatch.setattr(track_emotion_client, "send_command", must_not_connect)

    result = track_emotion_client.publish_emotion(
        "teasing", source="test", priority=40, ttl_sec=2
    )

    assert result["ok"] is True
    assert result["legacy_tracker"] == "disabled"
    assert len(bus_calls) == 1


def test_legacy_tracker_remains_available_only_with_explicit_opt_in(monkeypatch):
    monkeypatch.setenv("CELIA_ENABLE_LEGACY_VTS_TRACKER", "1")
    _stub_bus(monkeypatch)
    sent = []
    monkeypatch.setattr(
        track_emotion_client,
        "send_command",
        lambda payload: sent.append(payload) or {"ok": True},
    )

    result = track_emotion_client.publish_emotion(
        "happy", source="test", priority=40, ttl_sec=2
    )

    assert result["ok"] is True
    assert result["tracker"] == {"ok": True}
    assert sent[0]["cmd"] == "set_emotion"


def test_default_lipsync_never_retains_or_sends_legacy_tracker_data(monkeypatch):
    monkeypatch.delenv("CELIA_ENABLE_LEGACY_VTS_TRACKER", raising=False)
    track_emotion_client._pending_lipsync.clear()
    track_emotion_client.register_lipsync_envelope(
        "speech-1", volumes=[0.2, 0.5], slice_length_ms=20, duration_sec=0.04
    )

    assert track_emotion_client._pending_lipsync == {}
    assert track_emotion_client.start_lipsync_envelope("speech-1") == {
        "ok": False,
        "status": "legacy_tracker_disabled",
    }
