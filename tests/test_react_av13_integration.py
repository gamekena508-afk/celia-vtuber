import importlib.util
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REACT_PATH = ROOT / "extensions" / "react" / "react_av13.py"


def load_react_module():
    spec = importlib.util.spec_from_file_location("react_av13_test", REACT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_react_imports_without_windows_only_dependencies():
    module = load_react_module()
    assert module.CELIA_PROCESS_URL.endswith("/bridge/process")
    assert module.CELIA_SPEAK_URL.endswith("/bridge/speak")


def test_react_context_is_marked_for_standard_bridge():
    module = load_react_module()
    payload = module._payload_processar_react("contexto da tela")

    assert payload == {
        "text": "contexto da tela",
        "autor": module.CELIA_REACT_AUTHOR,
        "fonte": "react",
        "images": [],
        "synthesize_audio": False,
    }


def test_react_speaks_only_a_distinct_bridge_response(monkeypatch):
    module = load_react_module()
    calls = []

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        if url == module.CELIA_PROCESS_URL:
            return Response({"response": "Entendi o contexto e vou reagir."})
        return Response({"ok": True})

    monkeypatch.setattr(module.requests, "post", post)
    module.CeliaBridgeClient(cooldown=0)._processar("Há uma cena nova na tela.")

    assert calls[0][0] == module.CELIA_PROCESS_URL
    assert calls[0][1]["fonte"] == "react"
    assert calls[1][0] == module.CELIA_SPEAK_URL
    assert calls[1][1]["text"] == "Entendi o contexto e vou reagir."


def test_react_suppresses_near_verbatim_context_echo(monkeypatch):
    module = load_react_module()
    calls = []

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "Há uma cena nova na tela"}

    def post(url, json, timeout):
        calls.append((url, json, timeout))
        return Response()

    monkeypatch.setattr(module.requests, "post", post)
    module.CeliaBridgeClient(cooldown=0)._processar("Há uma cena nova na tela.")

    assert [call[0] for call in calls] == [module.CELIA_PROCESS_URL]


def test_react_reaction_is_routed_as_context_not_direct_speech(monkeypatch):
    module = load_react_module()
    sent = []
    module._ultima_reacao = 0
    monkeypatch.setattr(module.random, "choice", lambda phrases: phrases[0])
    monkeypatch.setattr(module.celia_bridge, "enviar", sent.append)

    module._reagir("gatilho")

    assert len(sent) == 1
    assert sent[0].startswith("[REACT — evento detectado]")


def test_react_resolves_default_pulse_monitor(monkeypatch):
    module = load_react_module()

    class Result:
        stdout = "celia-test-sink\n"

    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: Result())

    assert module._resolver_fonte_pulse() == "celia-test-sink.monitor"
    assert module._resolver_fonte_pulse("explicit.monitor") == "explicit.monitor"


def test_react_prioritizes_dedicated_groq_asr_key(monkeypatch):
    module = load_react_module()
    monkeypatch.setenv("GROQ_API_KEY", "general")
    monkeypatch.setenv("GROQ_WHISPER_API_KEY", "whisper")
    monkeypatch.setenv("REACT_GROQ_ASR_API_KEY", "react")

    assert module._obter_chave_groq_asr() == "react"

    monkeypatch.delenv("REACT_GROQ_ASR_API_KEY")
    assert module._obter_chave_groq_asr() == "whisper"


def test_react_isolated_pulse_validation_never_calls_bridge_or_tts(monkeypatch):
    module = load_react_module()
    monkeypatch.setattr(module, "_capturar_wav_pulse", lambda source, duration: b"wav")
    monkeypatch.setattr(module, "_medir_wav_pcm16", lambda wav: (87.5, 16000))
    monkeypatch.setattr(
        module,
        "_transcrever_wav_validacao",
        lambda wav: "A YouTube validation transcript.",
    )

    result = module.validar_pulse_isolado("react-validation.monitor", 3)

    assert result["status"] == "passed"
    assert result["source"] == "react-validation.monitor"
    assert result["bridge_called"] is False
    assert result["xtts_called"] is False
    assert result["nvidia_called"] is False


def test_react_measures_wav_from_actual_pipe_bytes(monkeypatch):
    module = load_react_module()
    read_requests = []

    class Wav:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def getsampwidth(self):
            return 2

        def getnchannels(self):
            return 1

        def readframes(self, count):
            read_requests.append(count)
            return b"\x01\x00" * 12

    monkeypatch.setattr(module.wave, "open", lambda *args, **kwargs: Wav())

    _, frames = module._medir_wav_pcm16(b"streaming-wav")

    assert read_requests == [-1]
    assert frames == 12
