from fastapi import FastAPI
from fastapi.testclient import TestClient

from celia.bridge_routes import init_bridge_routes
from celia.core.conversation_event_queue import ConversationEventQueue
from celia.core.turn_arbiter import TurnArbiter


def test_v2_events_route_authenticates_and_uses_existing_arbiter(monkeypatch):
    queue = ConversationEventQueue()
    arbiter = TurnArbiter(queue)

    class Singleton:
        _turn_arbiter = arbiter

    monkeypatch.setattr("celia.bridge_routes.CeliaSingleton.get_instance", lambda: Singleton())
    monkeypatch.setenv("CELIA_BRIDGE_V2_TOKEN_TWITCH_LIVE_CHAT", "fixture-only-token")
    app = FastAPI()
    app.include_router(init_bridge_routes(None))
    client = TestClient(app)
    body = {
        "version": "2.0",
        "event_id": "fixture-event-01",
        "bot_id": "twitch",
        "session_id": "fixture-session",
        "event_type": "live_chat",
        "occurred_at": "2026-11-19T12:00:00Z",
        "ttl_ms": 5000,
        "payload": {"text": "hello"},
    }
    try:
        denied = client.post("/bridge/v2/events", json=body)
        assert denied.status_code == 401
        accepted = client.post(
            "/bridge/v2/events",
            json=body,
            headers={"Authorization": "Bearer fixture-only-token"},
        )
        assert accepted.status_code == 202
        assert accepted.json()["status"] == "queued"
        assert queue.snapshot()["pending"] == 1
        duplicate = client.post(
            "/bridge/v2/events",
            json=body,
            headers={"Authorization": "Bearer fixture-only-token"},
        )
        assert duplicate.status_code == 409
        assert duplicate.json()["status"] == "duplicate"
    finally:
        arbiter.close()
