from types import SimpleNamespace

from celia.bridge_routes import _bridge_emotion_context


def test_bridge_reuses_singleton_emotion_engine_for_external_turn(monkeypatch):
    calls = []

    class Emotions:
        state = SimpleNamespace(emotion="happy")

        def process_input(self, text):
            calls.append(text)
            return "existing emotion prompt"

    singleton = SimpleNamespace(context=object(), emotions=Emotions())
    monkeypatch.setattr(
        "celia.bridge_routes.CeliaSingleton.get_instance", lambda: singleton
    )

    prompt, emotion = _bridge_emotion_context("isso foi ótimo")

    assert prompt == "existing emotion prompt"
    assert emotion == "happy"
    assert calls == ["isso foi ótimo"]
