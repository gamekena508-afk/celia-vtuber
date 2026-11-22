"""O `speech.started` rebroadcastado carrega o perfil ativo.

É o único sinal de servidor que a faixa de voz do browser (external-playback-bridge.js)
usa para esperar o humano: só fora de "roteiro", e nunca sem o campo.
Ver docs/FILA_DE_FALA.md.
"""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_operational_profiles import behavior  # noqa: F401 - exposes pytest fixture


@pytest.fixture(autouse=True)
def isolate_training_archives(monkeypatch, tmp_path):
    monkeypatch.setenv("CELIA_TRAINING_ASSET_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("CELIA_TRAINING_TRACE_ENABLED", "0")


@pytest.mark.parametrize("profile", ["chat", "roteiro"])
def test_speech_started_broadcast_carries_active_profile(behavior, monkeypatch, profile):
    import celia.routes as routes

    behavior.select_profile(profile)
    monkeypatch.setattr(routes, "continuous_behavior", behavior)

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=SimpleNamespace(SAMPLE_RATE=16000),
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False

        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def interrupt(self, *args, **kwargs): return False

    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, Singleton()))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "u-" + profile})
        for _ in range(8):
            message = ws.receive_json()
            if message.get("type") == "speech.started":
                break
        else:
            raise AssertionError("speech.started não foi rebroadcastado")
    assert message["utterance_id"] == "u-" + profile
    assert message["source"] == "microphone"
    assert message["profile"] == profile
