import pytest
from types import SimpleNamespace
from unittest.mock import AsyncMock
import asyncio
import time

from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_operational_profiles import behavior  # noqa: F401 - exposes pytest fixture


@pytest.fixture(autouse=True)
def isolate_asr_test_archives(monkeypatch, tmp_path):
    monkeypatch.setenv("CELIA_TRAINING_ASSET_DIR", str(tmp_path / "assets"))
    monkeypatch.setenv("CELIA_TRAINING_TRACE_ENABLED", "0")


def test_real_client_ws_ignores_vad_and_common_asr_but_consumes_explicit_stop(behavior, monkeypatch):
    import celia.routes as routes
    import celia.core.roteiro_live as live
    behavior.select_profile("roteiro")
    behavior.observe("celia.tts_started", utterance_id="playing")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    monkeypatch.setattr(live.roteiro_live, "planned_celia_reply", lambda: {
        "beat_id": "a", "line_id": "a:1", "text": "Prepared reply",
    })
    asr = SimpleNamespace(SAMPLE_RATE=16000, async_transcribe_np=AsyncMock(
        side_effect=["Célia, para.", "Célia, para mim isso funciona.", "Calma, série."],
    ))
    class Singleton:
        context = SimpleNamespace(history_uid=None, asr_engine=asr,
                                  character_config=SimpleNamespace(human_name="Criador"))
        codex_playback_active = False
        interrupts = []
        submissions = []
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, text, **kwargs):
            self.submissions.append(text)
            await self.broadcast({"type": "test-submitted"})
        async def interrupt(self, text, **kwargs):
            self.interrupts.append((text, kwargs))
            await self.broadcast({"type": "test-interrupted"})
    singleton = Singleton()
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    def until(ws, kind):
        for _ in range(8):
            message = ws.receive_json()
            if message.get("type") == kind:
                return message
        raise AssertionError(f"missing {kind}")
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        until(ws, "ready")
        ws.send_json({"type": "interrupt-signal", "text": "Célia, para."})
        ws.send_json({"type": "heartbeat"})
        until(ws, "heartbeat-ack")
        assert not singleton.interrupts
        for utterance, expected in [("common", "test-submitted"), ("stop", "test-interrupted")]:
            ws.send_json({"type": "speech.started", "utterance_id": utterance})
            if utterance == "common":
                time.sleep(1.2)
                ws.send_json({"type": "mic-audio-data", "audio": [0.1] * 16000})
                until(ws, "asr-partial")
                assert not singleton.interrupts
            else:
                ws.send_json({"type": "mic-audio-data", "audio": [0.1] * 7000})
            ws.send_json({"type": "mic-audio-end", "utterance_id": utterance})
            until(ws, expected)
            if utterance == "common":
                assert not singleton.interrupts
                assert behavior.playback_state == "playing"
        assert len(singleton.interrupts) == 1
        assert singleton.interrupts[0][1] == {"role": "browser", "force": True}
        assert len(singleton.submissions) == 1


@pytest.mark.parametrize(("raw", "expected"), [
    ("A transição foi errada para a interface da Coreia.",
     "A transcrição foi errada para a interface da Célia."),
    ("E aí, Sérgio, tudo bom?", "E aí, Célia, tudo bom?"),
    ("Era pra ser séria, mas só que a gente saiu errado.",
     "Era pra ser Célia, mas só que a gente saiu errado."),
    ("O Sérgio não era pra ser Sérgio, era pra ser Sélia.",
     "O Célia não era pra ser Célia, era pra ser Célia."),
])
def test_refined_asr_is_same_text_for_ui_and_brain(behavior, monkeypatch, raw, expected):
    import celia.routes as routes
    import celia.core.roteiro_live as live

    behavior.select_profile("roteiro")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    monkeypatch.setattr(
        routes.orchestrator.context_log,
        "recent_summary",
        lambda max_chars=700: "Criador: erro de transcrição do Whisper na interface",
    )
    monkeypatch.setattr(live.roteiro_live, "planned_celia_reply", lambda: {
        "beat_id": "b", "line_id": "b:1", "text": "Resposta preparada",
    })
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(
            return_value=raw
        ),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        submissions = []
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, text, **kwargs):
            self.submissions.append(text)
            await self.broadcast({"type": "test-submitted", "text": text})
        async def interrupt(self, *args, **kwargs): return False

    singleton = Singleton()
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "canonical"})
        ws.send_json({"type": "mic-audio-data", "audio": [0.1] * 16000})
        ws.send_json({
            "type": "mic-audio-end",
            "utterance_id": "canonical",
            "voiced_samples": 8000,
            "preroll_samples": 5760,
            "captured_samples": 16000,
        })
        transcription = ws.receive_json()
        while transcription.get("type") != "user-input-transcription":
            transcription = ws.receive_json()
        submitted = ws.receive_json()
        while submitted.get("type") != "test-submitted":
            submitted = ws.receive_json()

        assert transcription["raw_text"] == raw
        assert transcription["corrected_text"] == expected
        assert transcription["text"] == expected
        assert transcription["asr_decision"] == "CORRECT"
        assert transcription["asr_normalization_rules"]
        assert transcription["asr_refinement_status"] == "DISABLED"
        assert submitted["text"] == expected
        assert singleton.submissions == [expected]


def test_rms_preroll_does_not_count_as_real_voice(behavior, monkeypatch):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(return_value="Obrigado."),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, *args, **kwargs): raise AssertionError("short noise reached brain")
        async def interrupt(self, *args, **kwargs): return False

    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, Singleton()))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "noise"})
        ws.send_json({"type": "mic-audio-data", "audio": [0.0] * 6442})
        ws.send_json({
            "type": "mic-audio-end",
            "utterance_id": "noise",
            "voiced_samples": 682,
            "preroll_samples": 5760,
            "captured_samples": 6442,
        })
        trace = ws.receive_json()
        while trace.get("type") != "asr-trace":
            trace = ws.receive_json()
        assert trace["asr_event"] == "REJECTED_MIN_VOICED"
        assert trace["asr_decision"] == "REJECT"
        assert trace["asr_reject_reason"] == "short_voiced_time_with_weak_audio"
        assert asr.async_transcribe_np.await_count == 0


def test_strong_short_celia_call_below_180ms_reaches_whisper(behavior, monkeypatch):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(return_value="Célia!"),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, *args, **kwargs): pass
        async def interrupt(self, *args, **kwargs): return False

    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, Singleton()))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "short-celia"})
        ws.send_json({
            "type": "mic-audio-data",
            "utterance_id": "short-celia",
            "audio": [0.1] * 4000,
        })
        ws.send_json({
            "type": "mic-audio-end",
            "utterance_id": "short-celia",
            "voiced_samples": 2400,
            "captured_samples": 4000,
            "vad_source": "rms_endpoint_v2",
        })
        message = ws.receive_json()
        while message.get("type") != "user-input-transcription":
            message = ws.receive_json()
        assert message["raw_text"] == "Célia!"
        assert message["corrected_text"] == "Célia!"
        assert message["asr_decision"] == "KEEP"
        assert message["asr_acoustic"]["voiced_ms"] == 150

    assert asr.async_transcribe_np.await_count == 1


def test_ctrl_p_still_forces_singleton_interrupt_in_roteiro(behavior):
    from celia.core.voice_listener import VoiceListener
    behavior.select_profile("roteiro")
    calls = []
    listener = object.__new__(VoiceListener)
    listener.singleton = SimpleNamespace(
        interrupt=AsyncMock(), clear_pending_inputs=lambda: calls.append("clear"),
    )
    listener._run_coro = asyncio.run
    listener._hotkey_interrupt()
    listener.singleton.interrupt.assert_awaited_once_with(role="browser", force=True)
    assert calls == ["clear"]


def test_back_to_back_utterances_keep_separate_audio_snapshots(behavior, monkeypatch):
    import celia.routes as routes
    import celia.core.roteiro_live as live

    behavior.select_profile("roteiro")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    monkeypatch.setattr(live.roteiro_live, "planned_celia_reply", lambda: None)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(side_effect=["Primeira fala.", "Segunda fala."]),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, *args, **kwargs):
            raise AssertionError("roteiro sem linha não deve submeter ao cérebro")
        async def interrupt(self, *args, **kwargs): return False

    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, Singleton()))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        for uid, value in (("u1", 0.1), ("u2", 0.2)):
            ws.send_json({"type": "speech.started", "utterance_id": uid})
            ws.send_json({
                "type": "mic-audio-data",
                "utterance_id": uid,
                "audio": [value] * 8000,
            })
            ws.send_json({
                "type": "mic-audio-end",
                "utterance_id": uid,
                "voiced_samples": 7000,
                "captured_samples": 8000,
                "vad_source": "rms_endpoint_v2",
                "endpoint_reason": "max_duration",
            })

        finals = []
        while len(finals) < 2:
            message = ws.receive_json()
            if message.get("type") == "user-input-transcription":
                finals.append(message["text"])

    assert finals == ["Primeira fala.", "Segunda fala."]
    assert asr.async_transcribe_np.await_count == 2
    first = asr.async_transcribe_np.await_args_list[0].args[0]
    second = asr.async_transcribe_np.await_args_list[1].args[0]
    assert abs(float(first.mean()) - 0.1) < 1e-5
    assert abs(float(second.mean()) - 0.2) < 1e-5


def test_weak_known_whisper_hallucination_is_visible_but_never_reaches_brain(behavior, monkeypatch):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(return_value="Legenda por Sônia Ruberti"),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        submissions = []
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, text, **kwargs): self.submissions.append(text)
        async def interrupt(self, *args, **kwargs): return False

    singleton = Singleton()
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "ghost"})
        ws.send_json({
            "type": "mic-audio-data",
            "utterance_id": "ghost",
            "audio": [0.001] * 8000,
        })
        ws.send_json({
            "type": "mic-audio-end",
            "utterance_id": "ghost",
            "voiced_samples": 4000,
            "captured_samples": 8000,
            "vad_source": "rms_endpoint_v2",
        })

        message = ws.receive_json()
        while message.get("type") != "user-input-transcription":
            message = ws.receive_json()

        assert message["raw_text"] == "Legenda por Sônia Ruberti"
        assert message["corrected_text"] == ""
        assert message["asr_decision"] == "REJECT"
        assert message["asr_rejected"] is True

    assert singleton.submissions == []


def test_turn_gate_rejects_strong_85ms_e_ai_before_brain(behavior, monkeypatch):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(return_value="E aí"),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        submissions = []
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, text, **kwargs): self.submissions.append(text)
        async def interrupt(self, *args, **kwargs): return False

    singleton = Singleton()
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "ghost-e-ai"})
        ws.send_json({
            "type": "mic-audio-data",
            "utterance_id": "ghost-e-ai",
            "audio": [0.1] * 16000,
        })
        ws.send_json({
            "type": "mic-audio-end",
            "utterance_id": "ghost-e-ai",
            "voiced_samples": 1360,
            "captured_samples": 16000,
            "vad_source": "rms_endpoint_v2",
        })
        for _ in range(12):
            message = ws.receive_json()
            if message.get("asr_event") == "REJECTED_TURN_GATE":
                break
        else:
            raise AssertionError("turn gate rejection trace not received")
        assert message["asr_rejected"] is True
        assert message["asr_reject_reason"] == "voiced_below_turn_floor"
        assert message["asr_submitted_to_brain"] is False

    assert asr.async_transcribe_np.await_count == 1
    assert singleton.submissions == []


def test_measured_asr_error_repeated_by_fallback_is_uncertain_and_never_reaches_brain(behavior, monkeypatch):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(
            return_value="Vai de agnosticando, vai e vindo."
        ),
        async_secondary_transcribe_np=AsyncMock(
            return_value="Vai de agnosticando, vai e vindo."
        ),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        submissions = []
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, text, **kwargs): self.submissions.append(text)
        async def interrupt(self, *args, **kwargs): return False

    singleton = Singleton()
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"
        ws.send_json({"type": "speech.started", "utterance_id": "bad-both"})
        ws.send_json({
            "type": "mic-audio-data",
            "utterance_id": "bad-both",
            "audio": [0.1] * 16000,
        })
        ws.send_json({
            "type": "mic-audio-end",
            "utterance_id": "bad-both",
            "voiced_samples": 10000,
            "captured_samples": 16000,
            "vad_source": "rms_endpoint_v2",
        })
        for _ in range(12):
            message = ws.receive_json()
            if message.get("asr_event") == "SECONDARY_UNCERTAIN":
                break
        else:
            raise AssertionError("secondary uncertainty trace not received")
        assert message["asr_uncertain"] is True
        assert message["asr_rejected"] is True
        assert message["asr_reject_reason"] == "primary_fallback_repeat_measured_error"
        assert message["asr_route_status"] == "UNCERTAIN"
        assert message["asr_submitted_to_brain"] is False

    assert asr.async_transcribe_np.await_count == 1
    assert asr.async_secondary_transcribe_np.await_count == 1
    assert singleton.submissions == []


def test_short_fragment_is_held_and_merged_into_one_brain_turn(behavior, monkeypatch):
    import celia.routes as routes

    behavior.select_profile("chat")
    monkeypatch.setattr(routes, "continuous_behavior", behavior)
    asr = SimpleNamespace(
        SAMPLE_RATE=16000,
        async_transcribe_np=AsyncMock(side_effect=[
            "e você também disse",
            "que eu podia jogar agora",
        ]),
    )

    class Singleton:
        context = SimpleNamespace(
            history_uid=None,
            asr_engine=asr,
            character_config=SimpleNamespace(human_name="Criador"),
        )
        codex_playback_active = False
        submissions = []
        async def register(self, uid, ws, role, playback_client="browser"): self.ws = ws
        async def send_initial_state(self, ws): await ws.send_json({"type": "ready"})
        async def unregister(self, uid): pass
        async def broadcast(self, message): await self.ws.send_json(message)
        async def submit_input(self, text, **kwargs):
            self.submissions.append((text, kwargs.get("turn_id")))
            await self.ws.send_json({"type": "test-submitted", "text": text})
        async def interrupt(self, *args, **kwargs): return False

    singleton = Singleton()
    app = FastAPI()
    app.include_router(routes.init_client_ws_route(None, singleton))
    with TestClient(app).websocket_connect("/client-ws?role=browser") as ws:
        assert ws.receive_json()["type"] == "ready"

        ws.send_json({"type": "speech.started", "utterance_id": "frag-a"})
        ws.send_json({"type": "mic-audio-data", "utterance_id": "frag-a", "audio": [0.1] * 7000})
        ws.send_json({
            "type": "mic-audio-end", "utterance_id": "frag-a",
            "voiced_samples": 4080,  # 255 ms: hold, not a turn yet
            "captured_samples": 7000, "vad_source": "rms_endpoint_v2",
        })
        for _ in range(12):
            held = ws.receive_json()
            if held.get("asr_event") == "HELD_FRAGMENT":
                break
        else:
            raise AssertionError("first fragment was not held")
        assert singleton.submissions == []

        ws.send_json({"type": "speech.started", "utterance_id": "frag-b"})
        ws.send_json({"type": "mic-audio-data", "utterance_id": "frag-b", "audio": [0.1] * 16000})
        ws.send_json({
            "type": "mic-audio-end", "utterance_id": "frag-b",
            "voiced_samples": 12000, "captured_samples": 16000,
            "vad_source": "rms_endpoint_v2",
        })
        for _ in range(24):
            message = ws.receive_json()
            if message.get("type") == "test-submitted":
                break
        else:
            raise AssertionError("merged turn was not submitted")

    assert singleton.submissions == [
        ("e você também disse que eu podia jogar agora", "frag-b")
    ]
    assert asr.async_transcribe_np.await_count == 2
