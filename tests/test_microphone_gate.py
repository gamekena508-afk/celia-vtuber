from types import SimpleNamespace
from fastapi import FastAPI
from fastapi.testclient import TestClient
from celia.core.microphone_gate import MicrophoneGate
from test_operational_profiles import behavior


def test_mute_unmute_invalidates_old_audio():
    gate = MicrophoneGate()
    old = gate.revision
    gate.set_enabled(False)
    assert not gate.accepts(old)
    gate.set_enabled(True)
    assert not gate.accepts(old)
    assert gate.accepts(gate.revision)


def test_browser_mute_blocks_pet_and_syncs_reconnect(behavior, monkeypatch):
    import celia.routes as routes
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    class Singleton:
        context = SimpleNamespace(history_uid=None, asr_engine=SimpleNamespace(SAMPLE_RATE=16000),
                                  character_config=SimpleNamespace(human_name="Criador"))
        codex_playback_active = False
        def __init__(self): self.clients = {}
        async def register(self, uid, ws, role, playback_client="browser"): self.clients[uid] = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): self.clients.pop(uid, None)
        async def broadcast(self, message):
            for ws in list(self.clients.values()): await ws.send_json(message)
        async def interrupt(self, *args, **kwargs): return False

    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, Singleton()))
    with TestClient(app) as client:
        with client.websocket_connect("/client-ws?role=browser") as ui, client.websocket_connect("/client-ws?role=browser&playback_client=pet") as pet:
            assert ui.receive_json()["type"] == "ready"
            assert pet.receive_json()["type"] == "ready"
            ui.send_json({"type": "microphone-state-set", "enabled": False})
            assert ui.receive_json()["enabled"] is False
            assert pet.receive_json()["enabled"] is False
            # An old Pet can still send; server must drop these before ASR/VAD.
            for packet in [{"type": "speech.started", "utterance_id": "muted"},
                           {"type": "mic-audio-data", "audio": [0.1]*100},
                           {"type": "mic-audio-end", "utterance_id": "muted"}]:
                pet.send_json(packet)
            pet.send_json({"type": "heartbeat"})
            assert pet.receive_json()["type"] == "heartbeat-ack"
            with client.websocket_connect("/client-ws?role=browser&playback_client=pet") as late:
                assert late.receive_json()["type"] == "ready"
                late.send_json({"type": "microphone-state-get"})
                assert late.receive_json()["enabled"] is False
            pet.send_json({"type": "microphone-state-set", "enabled": True})
            assert pet.receive_json()["enabled"] is True
            assert ui.receive_json()["enabled"] is True
            # No actual audio/model call: show that speech.started now reaches the route.
            pet.send_json({"type": "speech.started", "utterance_id": "unmuted"})
            assert pet.receive_json()["type"] == "speech.started"
            assert ui.receive_json()["utterance_id"] == "unmuted"
            ui.send_json({"type": "microphone-state-set", "enabled": "false"})
            assert ui.receive_json()["enabled"] is True
