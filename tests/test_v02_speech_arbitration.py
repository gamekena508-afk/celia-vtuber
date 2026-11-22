"""P4 contracts for speech priority, STOP and TTS ticket validation."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from celia.core.speech_coordinator import SpeechCoordinator, speech_coordinator
from celia.celia_singleton import CeliaSingleton
from celia.conversations import tts_manager as tts_module
from celia.conversations.tts_manager import TTSTaskManager


def test_discord_direct_preempts_game_and_game_ranks_over_live_and_ambient():
    coordinator = SpeechCoordinator()
    game = coordinator.request(source="game_commentary", ttl_sec=10)
    direct = coordinator.request(source="discord_direct", ttl_sec=10)
    live = coordinator.request(source="live", ttl_sec=10)
    ambient = coordinator.request(source="ambient", ttl_sec=10)

    assert direct is not None
    assert coordinator.valid(game.id) is False
    assert coordinator.valid(live.id)
    assert coordinator.valid(ambient.id)
    assert coordinator.source_priority("discord_direct") > coordinator.source_priority("game_commentary")
    assert coordinator.source_priority("game_commentary") > coordinator.source_priority("live")
    assert coordinator.source_priority("live") >= coordinator.source_priority("ambient")


def test_owner_stop_cancels_pending_and_returns_active_speech_ids():
    coordinator = SpeechCoordinator()
    active = coordinator.request(source="direct_user", ttl_sec=10)
    pending = coordinator.request(source="ambient", ttl_sec=10)
    assert active is not None and pending is not None
    coordinator.bind_speech(active.id, "speech-active")
    coordinator.mark_playing("speech-active")

    assert coordinator.owner_stop() == ["speech-active"]
    assert coordinator.valid(active.id) is False
    assert coordinator.valid(pending.id) is False
    assert coordinator.ticket_for_speech("speech-active")["reason"] == "owner_stop"


def test_ticket_cancelled_while_waiting_for_synthesis_never_calls_engine(monkeypatch):
    manager = TTSTaskManager.__new__(TTSTaskManager)
    manager._reply_turn_revision = None
    manager._speech_ids_by_turn = {}
    manager._payload_queue = asyncio.Queue()
    manager._latency_starts = {}
    manager._wait_for_synthesis_turn = AsyncMock()
    manager._release_synthesis_turn = AsyncMock()
    manager._generate_audio = AsyncMock(return_value="should-not-exist.wav")
    ticket = SimpleNamespace(id="ticket-1")
    chunk = {
        "speech_id": "speech-1",
        "turn_id": "turn-1",
        "context_revision": 1,
        "turn_revision": 1,
        "revision_kind": "context",
        "generation": 1,
    }
    monkeypatch.setattr(tts_module.continuous_behavior, "create_tts_chunk", lambda *_a, **_k: dict(chunk))
    monkeypatch.setattr(tts_module.continuous_behavior, "tts_chunk_valid", lambda _c: True)
    monkeypatch.setattr(tts_module.continuous_behavior, "current_revision_for", lambda _c: 1)
    monkeypatch.setattr(tts_module.latency_trace, "mark", lambda *_a, **_k: None)
    monkeypatch.setattr(tts_module.latency_trace, "request_gpu_snapshot", lambda *_a, **_k: None)
    monkeypatch.setattr(speech_coordinator, "request", lambda **_k: ticket)
    monkeypatch.setattr(speech_coordinator, "valid", lambda *_a, **_k: False)

    asyncio.run(
        manager._process_tts(
            tts_text="texto",
            display_text=object(),
            actions=None,
            live2d_model=object(),
            tts_engine=object(),
            sequence_number=0,
            websocket_send=AsyncMock(),
            turn_id="turn-1",
        )
    )

    assert manager._generate_audio.await_count == 0
    assert manager._payload_queue.get_nowait() == ({"_skip_delivery": True}, 0)


def test_cancelled_ticket_is_checked_again_at_ordered_dispatch(monkeypatch):
    manager = TTSTaskManager.__new__(TTSTaskManager)
    manager._next_sequence_to_send = 0
    manager._latency_starts = {0: None}
    manager._payload_queue = asyncio.Queue()
    manager._payload_queue.put_nowait(({
        "type": "audio",
        "speech_ticket_id": "ticket-2",
        "speech_id": "speech-2",
        "cancel_token": {},
    }, 0))
    send = AsyncMock()
    monkeypatch.setattr(tts_module.continuous_behavior, "tts_chunk_valid", lambda _c: True)
    monkeypatch.setattr(tts_module.continuous_behavior, "current_revision_for", lambda _c: 1)
    monkeypatch.setattr(speech_coordinator, "valid", lambda *_a, **_k: False)
    monkeypatch.setattr(tts_module, "record_audio_lifecycle", lambda *_a, **_k: None)

    async def scenario():
        worker = asyncio.create_task(manager._process_payload_queue(send))
        await asyncio.wait_for(manager._payload_queue.join(), timeout=1)
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)

    asyncio.run(scenario())
    send.assert_not_awaited()


def test_duplicate_playback_ack_resolves_waiter_only_once():
    async def scenario():
        singleton = CeliaSingleton()
        future = singleton.begin_playback_wait("speech-ack")
        first = singleton.resolve_playback_wait("speech-ack", "ended")
        second = singleton.resolve_playback_wait("speech-ack", "ended")
        result = await future
        singleton.end_playback_wait("speech-ack")
        return first, second, result

    assert asyncio.run(scenario()) == (True, False, "ended")


def test_stop_authorization_comes_from_server_playback_ownership_not_source_label():
    class Socket:
        async def send_text(self, _payload):
            return None

    async def scenario():
        singleton = CeliaSingleton()
        await singleton.register("older", Socket(), "browser", "browser")
        await singleton.register("owner", Socket(), "browser", "browser")
        ticket = speech_coordinator.request(source="ambient", ttl_sec=10)
        assert ticket is not None
        speech_coordinator.bind_speech(ticket.id, "speech-owned")
        speech_coordinator.mark_playing("speech-owned")

        # A non-owner cannot STOP even if its message claims an owner source.
        assert singleton.stop_playback_for_owner("older") == []
        assert speech_coordinator.valid(ticket.id)
        assert singleton.stop_playback_for_owner("owner") == ["speech-owned"]
        assert speech_coordinator.valid(ticket.id) is False

    speech_coordinator.reset_for_tests()
    try:
        asyncio.run(scenario())
    finally:
        speech_coordinator.reset_for_tests()


@pytest.mark.parametrize("stale_on_check", [2, 3], ids=["before-tts", "after-tts"])
def test_external_bridge_ticket_is_checked_before_and_after_tts(
    stale_on_check, monkeypatch, tmp_path
):
    from celia import bridge_routes

    generated = []

    class Engine:
        async def async_generate_audio(self, **_kwargs):
            generated.append(True)
            return str(tmp_path / "unused.wav")

    class Singleton:
        context = SimpleNamespace(tts_engine=Engine(), agent_engine=None, history_uid=None)
        active_connections = 0
        _connections = {}
        playback_owner_uid = None

        def set_playback_status(self, *_args):
            return None

    checks = 0

    def ticket_valid(*_args, **_kwargs):
        nonlocal checks
        checks += 1
        return checks != stale_on_check

    async def admitted(_priority):
        return True, 0.0, 0.0

    monkeypatch.setattr(bridge_routes.CeliaSingleton, "get_instance", lambda: Singleton())
    monkeypatch.setattr(bridge_routes, "_aguardar_vez_de_falar", admitted)
    monkeypatch.setattr(speech_coordinator, "valid", ticket_valid)
    speech_coordinator.reset_for_tests()
    endpoint = next(
        route.endpoint
        for route in bridge_routes.init_bridge_routes(None).routes
        if route.path == "/bridge/speak"
    )
    try:
        response = asyncio.run(endpoint(bridge_routes.SpeakRequest(text="fala controlada")))
    finally:
        speech_coordinator.reset_for_tests()

    assert response.ok is False and response.skipped is True
    assert bool(generated) is (stale_on_check == 3)
