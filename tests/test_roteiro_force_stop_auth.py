import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def test_force_stop_is_available_only_through_authenticated_roteiro_socket(monkeypatch):
    import celia.roteiro_routes as routes
    from celia.celia_singleton import CeliaSingleton
    from celia.core.roteiro_live import RoteiroLiveRuntime

    runtime = RoteiroLiveRuntime()
    asyncio.run(runtime.activate())
    singleton = SimpleNamespace(
        broadcast=AsyncMock(),
        interrupt=AsyncMock(return_value=False),
        clear_pending_inputs=lambda: None,
    )
    monkeypatch.setattr(routes, "roteiro_live", runtime)
    monkeypatch.setattr(routes.roteiro_auth, "valid_session", lambda token: token == "test-session")
    monkeypatch.setattr(CeliaSingleton, "get_instance", lambda: singleton)

    app = FastAPI()
    app.include_router(routes.init_roteiro_routes())
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as denied:
            with client.websocket_connect("/roteiro/ws") as websocket:
                websocket.receive_json()
        assert denied.value.code == 4401

        with client.websocket_connect(
            "/roteiro/ws",
            headers={"Cookie": "celia_roteiro_session=test-session"},
        ) as websocket:
            assert websocket.receive_json()["type"] == "roteiro.snapshot"
            websocket.send_json({"action": "force_stop", "source": "roteiro.ctrl_p"})
            confirmation = websocket.receive_json()

    assert confirmation["snapshot"]["last_event"]["type"] == "roteiro.force_stop"
    singleton.interrupt.assert_awaited_once_with(role="browser", force=True)
