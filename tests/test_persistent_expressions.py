import json
from pathlib import Path

from PIL import Image

from celia.celia_emotions import CeliaEmotions
from celia.bridge_routes import EmotionRequest
from celia.core.emotion_bus import BarramentoEmocao


ROOT = Path(__file__).resolve().parents[1]


def test_persistent_expression_does_not_expire_and_a_new_one_replaces_it():
    bus = BarramentoEmocao()

    first = bus.publicar("confused", origem="chat", prioridade=70, ttl_s=None, persistente=True)
    assert first["ok"] is True
    assert bus.valendo().emocao == "confused"
    assert bus.expirou_agora() is None

    second = bus.publicar("sad", origem="chat", prioridade=1, ttl_s=None, persistente=True)
    assert second["ok"] is True
    assert bus.valendo().emocao == "sad"
    assert bus.diagnostico()["persistente"] is True


def test_transient_event_keeps_bounded_expiry(monkeypatch):
    import celia.core.emotion_bus as emotion_bus

    now = [10.0]
    monkeypatch.setattr(emotion_bus.time, "monotonic", lambda: now[0])
    bus = BarramentoEmocao()
    assert bus.publicar("think", origem="tool", ttl_s=1.0)["ok"] is True
    now[0] = 11.1
    assert bus.expirou_agora().emocao == "think"
    assert bus.valendo() is None



def test_transient_reaction_restores_persistent_affect(monkeypatch):
    import celia.core.emotion_bus as emotion_bus

    now = [20.0]
    monkeypatch.setattr(emotion_bus.time, "monotonic", lambda: now[0])
    bus = BarramentoEmocao()
    assert bus.publicar("angry", origem="response", prioridade=80, ttl_s=None, persistente=True)["ok"]
    assert bus.publicar("think", origem="tool", prioridade=90, ttl_s=1.0)["ok"]
    assert bus.valendo().emocao == "think"
    now[0] = 21.1
    expired = bus.expirou_agora()
    assert expired.emocao == "think"
    assert bus.valendo().emocao == "angry"

def test_stale_speech_completion_cannot_overwrite_a_new_expression():
    bus = BarramentoEmocao()
    assert bus.publicar(
        "trol_speaking",
        origem="speech:one",
        prioridade=80,
        ttl_s=None,
        persistente=True,
    )["ok"] is True
    assert bus.publicar(
        "confused", origem="chat", prioridade=70, ttl_s=None, persistente=True
    )["ok"] is True

    result = bus.publicar(
        "trol_idle",
        origem="speech:one",
        prioridade=80,
        ttl_s=None,
        persistente=True,
        esperado_emocao="trol_speaking",
        esperada_origem="speech:one",
    )
    assert result == {"ok": False, "motivo": "estado_alterado", "valendo": "confused"}
    assert bus.valendo().emocao == "confused"


def test_trol_speaking_then_idle_persists_until_next_playback():
    bus = BarramentoEmocao()
    assert bus.publicar(
        "trol_speaking",
        origem="speech:one",
        prioridade=80,
        ttl_s=None,
        persistente=True,
    )["ok"] is True
    assert bus.valendo().emocao == "trol_speaking"

    assert bus.publicar(
        "trol_idle",
        origem="speech:one",
        prioridade=80,
        ttl_s=None,
        persistente=True,
        esperado_emocao="trol_speaking",
        esperada_origem="speech:one",
    )["ok"] is True
    assert bus.valendo().emocao == "trol_idle"

    # A later playback replaces the tongue-out expression and starts the next
    # speech face; no timer is allowed to clear the persistent state.
    assert bus.publicar(
        "trol_speaking",
        origem="speech:two",
        prioridade=80,
        ttl_s=None,
        persistente=True,
    )["ok"] is True
    assert bus.valendo().emocao == "trol_speaking"


def test_chat_input_classifier_does_not_publish_character_affect(monkeypatch):
    calls = []
    monkeypatch.setattr("celia.celia_emotions.publish_emotion", lambda *args, **kwargs: calls.append((args, kwargs)))

    CeliaEmotions().update("", force_emotion="confused")

    assert calls == []


def test_emotion_bridge_contract_allows_an_explicit_persistent_expression():
    request = EmotionRequest(emotion="confused", source="local-test", persistent=True)
    assert request.persistent is True


def test_served_model_registers_all_new_expression_files_and_canonical_indices():
    model_path = ROOT / "web/live2d-models/celia_vts/suiika_1_05_b2.model3.json"
    model = json.loads(model_path.read_text(encoding="utf-8"))
    expressions = model["FileReferences"]["Expressions"]
    expected = {
        "agry": 18,
        "fany": 19,
        "sad": 20,
        "trol final da fala": 21,
        "trol enquanto fala": 22,
        "holding back laughter": 23,
        "passionate": 24,
        "confused": 25,
    }
    registered = {entry["Name"]: entry["File"] for entry in expressions}
    for name in expected:
        assert registered[name] == f"Expressions/{name}.exp3.json"
        assert (model_path.parent / registered[name]).is_file()

    models = json.loads((ROOT / "model_dict.json").read_text(encoding="utf-8"))
    emotion_map = next(item for item in models if item["name"] == "celia_vts")["emotionMap"]
    # Asset names and parameters are canonical: speaking is the no-tongue
    # expression (22); post-speech is the tongue-out final expression (21).
    assert emotion_map["teasing"] == expected["trol enquanto fala"]
    assert emotion_map["trol_speaking"] == expected["trol enquanto fala"]
    assert emotion_map["trol_idle"] == expected["trol final da fala"]
    assert emotion_map["confused"] == expected["confused"]


def test_identity_ready_reasserts_current_face_expression():
    identity = (ROOT / "frontend" / "celia-identidade.js").read_text(encoding="utf-8")
    face = (ROOT / "frontend" / "celia-face.js").read_text(encoding="utf-8")

    assert 'new CustomEvent("celia-identidade-ready"' in identity
    assert 'addEventListener("celia-identidade-ready"' in face
    assert 'aplicar(desejado.indice, desejado.emocao);' in face


def test_confusion_gifs_have_transparency_without_opaque_chroma_green():
    for gif_name in ("entrada.gif", "loop.gif"):
        image = Image.open(ROOT / "frontend/media/celia-confusion" / gif_name)
        for frame_index in range(image.n_frames):
            image.seek(frame_index)
            pixels = list(image.convert("RGBA").get_flattened_data())
            assert sum(alpha == 0 for _, _, _, alpha in pixels) > len(pixels) // 2
            assert not any(
                alpha > 0 and green > 130 and red < 80 and blue < 80
                for red, green, blue, alpha in pixels
            )
