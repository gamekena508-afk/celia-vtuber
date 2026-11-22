"""Ligação do temporizador de fragmentos no /client-ws REAL (ASR de mentira, Singleton que registra submit_input): exatamente uma vez, no prazo,
e parada urgente que não espera nem herda nada do pendente."""
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_operational_profiles import behavior  # noqa: F401 - fixture


@pytest.fixture(autouse=True)
def _isolado(monkeypatch, tmp_path):
    monkeypatch.setenv("CELIA_TRAINING_ASSET_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("CELIA_TRAINING_TRACE_ENABLED", "0")
    monkeypatch.setenv("CELIA_ASR_CONTINUATION_WINDOW_MS", "400")     # prazo curto só para o teste; em produção 3000 (medido)


class _Singleton:
    def __init__(self, transcricoes):
        self.submissions, self.ws = [], None
        self.context = SimpleNamespace(history_uid=None, character_config=SimpleNamespace(human_name="Criador"),
                                       asr_engine=SimpleNamespace(SAMPLE_RATE=16000, async_transcribe_np=AsyncMock(side_effect=list(transcricoes))))
    codex_playback_active = False

    async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
    async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
    async def unregister(self, uid): pass
    async def broadcast(self, message): await self.ws.send_json(message)

    async def submit_input(self, text, **kwargs):
        self.submissions.append(text)
        await self.broadcast({"type": "test-submitted", "text": text})

    async def interrupt(self, *args, **kwargs): return False


def _app(monkeypatch, behavior, transcricoes):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    monkeypatch.setattr(routes.orchestrator.context_log, "recent_summary", lambda max_chars=700: "")
    singleton = _Singleton(transcricoes)
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    return singleton, app


def _fala(ws, uid, voz=8000):
    ws.send_json({"type": "speech.started", "utterance_id": uid})
    ws.send_json({"type": "mic-audio-data", "audio": [0.1] * 16000})
    ws.send_json({"type": "mic-audio-end", "utterance_id": uid, "voiced_samples": voz, "preroll_samples": 5760, "captured_samples": 16000})


def _ate(ws, tipo, limite=6.0):
    fim = time.monotonic() + limite
    while time.monotonic() < fim:
        m = ws.receive_json()
        if m.get("type") == tipo:
            return m
    raise AssertionError(f"não chegou {tipo}")


def test_unfinished_fragment_reaches_the_brain_exactly_once_by_time_without_other_speech(monkeypatch, behavior):
    texto = "Estamos de volta para um vídeo de..."
    singleton, app = _app(monkeypatch, behavior, [texto, texto])            # 1ª passada: HOLD; 2ª: a reentrega do prazo
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        t0 = time.monotonic()
        _fala(ws, "f1")
        enviado = _ate(ws, "test-submitted")
        assert enviado["text"] == texto
        assert 0.3 <= time.monotonic() - t0 <= 3.0, "tem de sair NO prazo (≈0,4 s), não instantes antes nem só na próxima fala"
        time.sleep(1.0)                                                      # nada mais pode chegar
    assert singleton.submissions == [texto]


def test_continuation_inside_the_window_becomes_one_turn_and_nothing_is_released_later(monkeypatch, behavior):
    singleton, app = _app(monkeypatch, behavior, ["Tandam dentro", "do Minecraft"])
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        _fala(ws, "a")
        time.sleep(0.15)
        _fala(ws, "b")
        enviado = _ate(ws, "test-submitted")
        assert enviado["text"] == "Tandam dentro do Minecraft"
        time.sleep(1.0)
    assert singleton.submissions == ["Tandam dentro do Minecraft"], "o prazo do primeiro pedaço não pode liberar nada depois da fusão"


def test_interjection_alone_never_reaches_the_brain(monkeypatch, behavior):
    singleton, app = _app(monkeypatch, behavior, ["Hum.", "Ahhh."])
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        _fala(ws, "h1")
        time.sleep(0.4)
        _fala(ws, "h2")
        time.sleep(0.8)
    assert singleton.submissions == []


def test_urgent_stop_passes_at_once_and_the_cancelled_fragment_never_executes_afterwards(monkeypatch, behavior):
    pendente = "Zera Minecraft, vou fazer a série de..."
    singleton, app = _app(monkeypatch, behavior, [pendente, "Para tudo agora", "Frase de fechamento completa aqui no fim."])
    vistos = []
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        _fala(ws, "p1")                                   # fica pendente (inacabado)
        time.sleep(0.1)
        _fala(ws, "p2")                                   # parada urgente, dentro da janela
        time.sleep(0.9)                                   # bem depois do prazo do pendente (0,4 s)
        _fala(ws, "p3")                                   # sentinela: a conversa continua normal
        fim = time.monotonic() + 6.0
        while time.monotonic() < fim:
            m = ws.receive_json()
            vistos.append(m)
            if m.get("type") == "test-submitted" and "fechamento" in m["text"]:
                break
    # Só eventos de TURNO contam; "asr-trace" é observabilidade (registra o HELD e o cancelamento do pendente).
    textos = [m.get("text", "") for m in vistos if m.get("type") in {"user-input-transcription", "test-submitted"}]
    assert not any("Zera Minecraft" in x for x in textos), "o fragmento cancelado pela parada não pode virar turno depois"
    assert any("Para tudo agora" in x for x in textos), "a parada urgente tem que ter chegado"
    assert "Zera Minecraft, vou fazer a série de..." not in singleton.submissions
