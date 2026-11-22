from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from celia.bridge.bot_admin_routes import init_bot_admin_routes
from celia.bridge.registry import BotRegistry
from celia.core.bot_process_manager import BotProcessManager
from src.celia.server import OfflineOperationGuard


ROOT = Path(__file__).resolve().parents[1]


def _app(tmp_path, *, offline: bool):
    registry = BotRegistry.load(ROOT)
    manager = BotProcessManager(registry, log_dir=tmp_path / "logs")
    app = FastAPI()
    app.add_middleware(OfflineOperationGuard, enabled=offline)
    app.include_router(init_bot_admin_routes(manager))
    return app, manager


def test_authenticated_status_is_readable_offline_but_control_and_heartbeat_are_503(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_ADMIN_BOT_TOKEN", "fixture-admin-token")
    app, manager = _app(tmp_path, offline=True)
    client = TestClient(app, client=("127.0.0.1", 40000))
    headers = {"Authorization": "Bearer fixture-admin-token"}

    listed = client.get("/bridge/v2/bots", headers=headers)
    assert listed.status_code == 200
    assert len(listed.json()["bots"]) == 7
    assert all(not bot["launchable"] for bot in listed.json()["bots"])
    assert client.get("/admin/api/bots", headers=headers).status_code == 200
    assert client.post("/admin/api/bots/discord/start", headers=headers).status_code == 503
    assert client.post("/bridge/v2/bots/discord/heartbeat", headers=headers, json={
        "version": "2.0", "bot_id": "discord", "instance_id": "fixture-instance",
        "state": "connected", "queue_depth": 0,
    }).status_code == 503
    assert client.get("/bridge/v2/events/fixture-event", headers=headers).status_code == 503
    assert manager.status("discord").pid is None


def test_admin_and_heartbeat_routes_authenticate_and_fail_closed_when_unconfigured(tmp_path, monkeypatch):
    monkeypatch.delenv("CELIA_ADMIN_BOT_TOKEN", raising=False)
    monkeypatch.delenv("CELIA_BOT_HEARTBEAT_TOKEN_DISCORD", raising=False)
    app, manager = _app(tmp_path, offline=False)
    client = TestClient(app, client=("127.0.0.1", 40000))

    assert client.get("/bridge/v2/bots").status_code == 503
    monkeypatch.setenv("CELIA_ADMIN_BOT_TOKEN", "fixture-admin-token")
    headers = {"Authorization": "Bearer fixture-admin-token"}
    assert client.get("/admin/api/bots", headers=headers).status_code == 200
    assert client.post("/admin/api/bots/discord/start", headers=headers).status_code == 409

    heartbeat = {
        "version": "2.0", "bot_id": "discord", "instance_id": "fixture-instance",
        "state": "connected", "queue_depth": 0,
    }
    assert client.post("/bridge/v2/bots/discord/heartbeat", json=heartbeat).status_code == 503
    monkeypatch.setenv("CELIA_BOT_HEARTBEAT_TOKEN_DISCORD", "fixture-heartbeat-token")
    hb_headers = {"Authorization": "Bearer fixture-heartbeat-token"}
    assert client.post("/bridge/v2/bots/discord/heartbeat", headers=hb_headers, json=heartbeat).status_code == 409
    assert manager.status("discord").pid is None


def test_admin_routes_are_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CELIA_ADMIN_BOT_TOKEN", "fixture-admin-token")
    app, _manager = _app(tmp_path, offline=False)
    remote = TestClient(app, client=("203.0.113.17", 40000))
    response = remote.get("/admin/api/bots", headers={"Authorization": "Bearer fixture-admin-token"})
    assert response.status_code == 403
