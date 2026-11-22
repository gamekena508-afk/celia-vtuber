import numpy as np
from celia.asr.openai_compatible_asr import VoiceRecognition


def test_asr_sends_deterministic_temperature_and_no_prompt(monkeypatch):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"text": "abre o YouTube"}
    def post(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()
    monkeypatch.setattr("celia.asr.openai_compatible_asr.httpx.post", post)
    asr = VoiceRecognition("http://primary/v1", prompt="", temperature=0.0)
    assert asr.transcribe_np(np.zeros(6400, dtype=np.float32)) == "abre o YouTube"
    assert captured["data"]["temperature"] == "0.0"
    assert "prompt" not in captured["data"]
    assert captured["data"]["language"] if "language" in captured["data"] else True


def test_asr_prompt_is_only_sent_when_explicit(monkeypatch):
    captured = {}
    class Response:
        def raise_for_status(self): pass
        def json(self): return {"text": "Célia"}
    def post(url, **kwargs): captured.update(kwargs); return Response()
    monkeypatch.setattr("celia.asr.openai_compatible_asr.httpx.post", post)
    VoiceRecognition("http://primary/v1", prompt="Célia", lang="pt").transcribe_np(np.zeros(6400,dtype=np.float32))
    assert captured["data"]["prompt"] == "Célia"
    assert captured["data"]["language"] == "pt"


def test_factory_forwards_temperature():
    from celia.asr.asr_factory import ASRFactory
    asr = ASRFactory.get_asr_system(
        "openai_compatible_asr",
        base_url="http://primary/v1",
        model="groq/whisper-large-v3-turbo",
        prompt="",
        temperature=0.25,
    )
    assert asr.temperature == 0.25
    assert asr.prompt == ""


def test_secondary_asr_refuses_dead_fallback_without_posting(monkeypatch):
    calls = {"get": 0, "post": 0}

    class GetResponse:
        status_code = 503

    def get(*args, **kwargs):
        calls["get"] += 1
        return GetResponse()

    def post(*args, **kwargs):
        calls["post"] += 1
        raise AssertionError("dead fallback must not receive transcription POST")

    monkeypatch.setattr("celia.asr.openai_compatible_asr.httpx.get", get)
    monkeypatch.setattr("celia.asr.openai_compatible_asr.httpx.post", post)
    asr = VoiceRecognition(
        "http://primary/v1",
        fallback_base_url="http://fallback/v1",
        fallback_model="openai/whisper-large-v3-turbo",
    )
    assert asr.secondary_transcribe_np(np.zeros(6400, dtype=np.float32)) == ""
    assert calls == {"get": 4, "post": 0}
    assert asr.last_error == "fallback:unavailable"


def test_secondary_asr_uses_same_audio_when_fallback_is_healthy(monkeypatch):
    posted = {}

    class GetResponse:
        status_code = 200

    class PostResponse:
        def raise_for_status(self):
            pass

        def json(self):
            return {"text": "segunda opinião"}

    monkeypatch.setattr(
        "celia.asr.openai_compatible_asr.httpx.get",
        lambda *args, **kwargs: GetResponse(),
    )

    def post(url, **kwargs):
        posted["url"] = url
        posted.update(kwargs)
        return PostResponse()

    monkeypatch.setattr("celia.asr.openai_compatible_asr.httpx.post", post)
    asr = VoiceRecognition(
        "http://primary/v1",
        fallback_base_url="http://fallback/v1",
        fallback_model="openai/whisper-large-v3-turbo",
        lang="pt",
    )
    assert asr.secondary_transcribe_np(np.zeros(6400, dtype=np.float32)) == "segunda opinião"
    assert posted["url"] == "http://fallback/v1/audio/transcriptions"
    assert posted["data"]["model"] == "openai/whisper-large-v3-turbo"
    assert posted["data"]["language"] == "pt"


def test_health_snapshot_accepts_local_root_health_when_v1_models_missing(monkeypatch):
    seen = []

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    def get(url, **kwargs):
        seen.append(url)
        if url == "http://fallback/v1/models":
            return Response(404)
        if url == "http://fallback/health":
            return Response(200)
        if url == "http://primary/v1/models":
            return Response(200)
        return Response(404)

    monkeypatch.setattr("celia.asr.openai_compatible_asr.httpx.get", get)
    asr = VoiceRecognition(
        "http://primary/v1",
        fallback_base_url="http://fallback/v1",
        fallback_model="openai/whisper-large-v3-turbo",
    )
    snapshot = asr.health_snapshot(force=True)
    assert snapshot == {"primary": True, "fallback": True}
    assert "http://fallback/health" in seen
