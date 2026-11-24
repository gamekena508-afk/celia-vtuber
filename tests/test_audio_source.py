"""Offline tests for the V02 Discord/window microphone policy."""
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from celia.core import audio_source as policy
from celia.core.microphone_gate import MicrophoneGate


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch, tmp_path):
    monkeypatch.setattr(policy, "VAR", tmp_path)


def test_auto_discord_only_when_celia_in_call():
    assert policy.status()["source"] == "window"
    policy.publish_discord(False)
    assert policy.active_source() == "window"
    policy.publish_discord(True, 42)
    assert policy.status()["source"] == "discord"
    assert policy.status()["channel_id"] == "42"
    policy.publish_discord(False)
    assert policy.active_source() == "window"


def test_stale_discord_lease_returns_to_window(monkeypatch):
    monkeypatch.setattr(policy.time, "time", lambda: 100.0)
    policy.publish_discord(True, 42)
    monkeypatch.setattr(policy.time, "time", lambda: 106.0)
    assert policy.active_source() == "window"


def test_manual_selection_and_persistent_follow():
    policy.configure(mode="discord")
    assert policy.active_source() == "none"
    policy.publish_discord(True, 42)
    assert policy.active_source() == "discord"
    policy.configure(follow=False)
    assert policy.config() == {"mode": "discord", "follow": False}
    policy.configure(mode="window")
    assert policy.active_source() == "window"
    assert policy.config()["follow"] is False
    policy.configure(mode="auto")
    assert policy.active_source() == "discord"


def test_switch_invalidates_pending_asr_and_preserves_manual_mute():
    gate = MicrophoneGate()
    gate.set_enabled(True)
    old = gate.revision
    assert gate.accepts(old)
    policy.publish_discord(True, 42)
    assert not gate.accepts(old)
    assert gate.snapshot()["window_allowed"] is False
    assert gate.enabled is True
    policy.publish_discord(False)
    assert gate.snapshot()["window_allowed"] is True
    assert gate.accepts(gate.revision)
    assert not gate.accepts(old)
    gate.set_enabled(False)
    policy.publish_discord(True, 42)
    assert not gate.accepts(gate.revision)
    policy.publish_discord(False)
    assert not gate.accepts(gate.revision)
    assert gate.enabled is False


def test_single_local_capture_owner_and_pet_display_only():
    gate = MicrophoneGate()
    gate.register("pet", "pet")
    gate.set_enabled(True)
    assert not gate.client_allowed("pet")
    previous = gate.revision
    gate.register("browser", "browser")
    assert gate.client_allowed("browser")
    assert not gate.client_allowed("pet")
    assert not gate.accepts(previous, "pet")
    gate.register("second_tab", "browser")
    assert not gate.client_allowed("second_tab")
    gate.unregister("browser")
    assert gate.client_allowed("second_tab")
    gate.unregister("second_tab")
    assert not gate.client_allowed("pet")
    assert gate.snapshot()["capture_surface"] is None


def test_owner_level_only_for_active_discord(monkeypatch):
    monkeypatch.setattr(policy.time, "time", lambda: 100.0)
    policy.publish_discord(True, 7)
    policy.publish_owner_level(0.7)
    assert policy.owner_level() == 0.7
    policy.configure(mode="window")
    assert policy.owner_level() == 0.0
    policy.configure(mode="auto")
    monkeypatch.setattr(policy.time, "time", lambda: 101.0)
    assert policy.owner_level() == 0.0


def test_common_follow_intent():
    assert policy.follow_intent("Célia, fica aí!") is False
    assert policy.follow_intent("Célia, volta a me seguir") is True
    assert policy.follow_intent("ela fica aí às vezes") is None


def test_loopback_configuration_api():
    from celia.bridge_routes import init_bridge_routes
    app = FastAPI()
    app.include_router(init_bridge_routes(None))
    with TestClient(app) as client:
        assert client.get("/bridge/audio-source").json()["source"] == "window"
        assert client.post("/bridge/audio-source", json={"mode": "invalid"}).status_code == 422
        assert client.post("/bridge/audio-source", json={"follow": "false"}).status_code == 422
        assert client.post("/bridge/audio-source", json={"mode": "auto", "follow": False}).json()["follow"] is False
        policy.publish_discord(True, 7)
        assert client.get("/bridge/audio-source").json()["source"] == "discord"
