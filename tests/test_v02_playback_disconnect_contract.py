"""Expected playback-owner disconnect contracts awaiting route handoff."""

import asyncio
import json

import pytest

from celia.celia_singleton import CeliaSingleton
from celia.core.speech_coordinator import speech_coordinator


class FakeSocket:
    async def send_text(self, payload):
        json.loads(payload)


@pytest.mark.asyncio
async def test_only_playback_owner_disconnect_releases_its_waiter():
    speech_coordinator.reset_for_tests()
    singleton = CeliaSingleton()
    await singleton.register("older-browser", FakeSocket(), "browser", "browser")
    await singleton.register("current-owner", FakeSocket(), "browser", "browser")
    assert singleton.playback_owner_uid == "current-owner"
    waiter = singleton.begin_playback_wait("speech-owned")
    ticket = speech_coordinator.request(source="ambient", ttl_sec=10)
    assert ticket is not None
    speech_coordinator.bind_speech(ticket.id, "speech-owned")
    speech_coordinator.mark_playing("speech-owned")

    await singleton.unregister("older-browser")
    assert not waiter.done(), "a non-owner websocket must not resolve playback waiters"

    await singleton.unregister("current-owner")
    assert waiter.done(), "disconnecting the playback owner must release its waiter"
    assert waiter.result() == "disconnected"
    stopped = speech_coordinator.ticket_for_speech("speech-owned")
    assert stopped["status"] == "cancelled"
    assert stopped["reason"] == "disconnected"
    singleton.end_playback_wait("speech-owned")
    speech_coordinator.reset_for_tests()


@pytest.mark.asyncio
async def test_duplicate_ack_after_disconnect_cannot_overwrite_terminal_status():
    singleton = CeliaSingleton()
    waiter = singleton.begin_playback_wait("speech-terminal")
    assert singleton.resolve_playback_wait("speech-terminal", "disconnected") is True
    assert singleton.resolve_playback_wait("speech-terminal", "ended") is False

    assert await waiter == "disconnected"
    assert singleton.playback_status("speech-terminal") == "disconnected"
    singleton.end_playback_wait("speech-terminal")


@pytest.mark.asyncio
async def test_timeout_is_terminal_for_late_playback_ack():
    singleton = CeliaSingleton()
    waiter = singleton.begin_playback_wait("speech-timeout")
    assert singleton.resolve_playback_wait("speech-timeout", "timeout") is True
    assert await waiter == "timeout"
    singleton.end_playback_wait("speech-timeout")

    assert singleton.resolve_playback_wait("speech-timeout", "ended") is False
    assert singleton.playback_status("speech-timeout") == "timeout"
