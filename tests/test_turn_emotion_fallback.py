import inspect

from celia.conversations import single_conversation as conversation


def _capture_publish(monkeypatch):
    calls = []

    def fake_publish(emotion, **kwargs):
        calls.append((emotion, kwargs))
        return {
            "ok": True,
            "bus": {"ok": True, "emocao": emotion, "mudou": True},
        }

    monkeypatch.setattr(conversation, "publish_emotion", fake_publish)
    return calls


def test_detector_emotion_is_published_as_persistent_visual_fallback(monkeypatch):
    calls = _capture_publish(monkeypatch)

    conversation.publish_turn_detector_emotion("ANGRY", turn_id="turn-1")

    assert calls == [(
        "angry",
        {
            "source": "turn_detector",
            "priority": 70,
            "ttl_sec": None,
            "persistent": True,
            "metadata": {"turn_id": "turn-1"},
        },
    )]


def test_detector_neutral_is_still_a_real_visual_decision(monkeypatch):
    calls = _capture_publish(monkeypatch)

    conversation.publish_turn_detector_emotion("neutral", turn_id="turn-2")

    assert calls[0][0] == "neutral"
    assert calls[0][1]["source"] == "turn_detector"
    assert calls[0][1]["persistent"] is True


def test_explicit_llm_tag_overrides_detector_with_higher_priority(monkeypatch):
    calls = _capture_publish(monkeypatch)

    conversation.publish_turn_detector_emotion("angry", turn_id="turn-3")
    conversation.publish_llm_emotion_override(
        "happy",
        turn_id="turn-3",
        detector_fallback="angry",
    )

    assert calls[0][0] == "angry"
    assert calls[0][1]["priority"] == 70
    assert calls[1][0] == "happy"
    assert calls[1][1]["source"] == "llm_output_tag"
    assert calls[1][1]["priority"] == 80
    assert calls[1][1]["persistent"] is True


def test_detector_fallback_waits_for_first_real_output_chunk():
    source = inspect.getsource(conversation.process_single_conversation)

    fallback_call = source.index("publish_turn_detector_emotion(")
    chunk_loop = source.index("async for output_item in agent_output_stream:")

    assert fallback_call > chunk_loop
    assert "response_emotion_started = False" in source
    assert source.count("publish_turn_detector_emotion(") == 1


def test_missing_detector_emotion_does_not_publish(monkeypatch):
    calls = _capture_publish(monkeypatch)

    result = conversation.publish_turn_detector_emotion(None, turn_id="turn-4")

    assert result is None
    assert calls == []
