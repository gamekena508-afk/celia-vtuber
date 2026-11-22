import pytest
import os
import subprocess
import sys
from pathlib import Path
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from src.celia.server import OfflineOperationGuard
from run_server import require_loopback_bind

ROOT = Path(__file__).resolve().parents[1]


def _guarded_app(enabled: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(OfflineOperationGuard, enabled=enabled)

    @app.get("/")
    async def frontend_shell():
        return {"page": "real-ui-shell"}

    @app.get("/games/api/version")
    async def game_version():
        return {"version": 1}

    @app.post("/bridge/v2/events")
    async def bridge_event():
        return {"accepted": True}

    @app.post("/asr")
    async def asr():
        return {"text": "transcribed"}

    @app.websocket("/client-ws")
    async def client_ws(ws: WebSocket):
        await ws.accept()

    return app


def test_offline_guard_keeps_frontend_readable_and_denies_operational_http_with_503():
    client = TestClient(_guarded_app(True))

    assert client.get("/").status_code == 200
    assert client.get("/games/api/version").status_code == 503
    for path in ("/bridge/v2/events", "/asr"):
        response = client.post(path)
        assert response.status_code == 503
        assert response.json()["status"] == "offline"


def test_offline_guard_denies_websocket_upgrade_with_http_503():
    app = _guarded_app(True)
    messages = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4", "extensions": {"websocket.http.response": {}}},
        "path": "/client-ws",
        "headers": [],
        "query_string": b"",
        "scheme": "ws",
        "server": ("127.0.0.1", 22493),
        "client": ("127.0.0.1", 50000),
        "subprotocols": [],
    }
    import asyncio

    asyncio.run(app(scope, receive, send))
    assert messages[0]["type"] == "websocket.http.response.start"
    assert messages[0]["status"] == 503


def test_disabled_guard_preserves_normal_app_behavior():
    client = TestClient(_guarded_app(False))
    assert client.get("/games/api/version").status_code == 200
    assert client.post("/bridge/v2/events").json() == {"accepted": True}


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "::1"])
def test_offline_mode_accepts_only_loopback_bind_names(host):
    require_loopback_bind(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "celia.local"])
def test_offline_mode_rejects_non_loopback_bind(host):
    with pytest.raises(ValueError, match="loopback bind"):
        require_loopback_bind(host)


def test_real_v02_app_serves_frontend_without_engine_initialization(tmp_path):
    state_root = tmp_path / "isolated-state"
    env = os.environ.copy()
    env.update({
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHON_DOTENV_DISABLED": "1",
        "CELIA_STATE_DIR": str(state_root),
        "CELIA_VAR_DIR": str(state_root),
        "CELIA_BIND_HOST": "127.0.0.1",
        "CELIA_PORT": "22493",
    })
    script = r'''
import asyncio, sys
from pathlib import Path
sys.path.insert(0, str(Path.cwd() / "src"))
from fastapi.testclient import TestClient
from run_server import prepare_offline_state
prepare_offline_state()
from launcher_config import effective_bind
from src.celia.config_manager import read_yaml, validate_config
raw = read_yaml(str(Path.cwd() / "conf.yaml"))
host, port = effective_bind(raw)
raw["system_config"]["host"] = host
raw["system_config"]["port"] = port
from src.celia.server import WebSocketServer
server = WebSocketServer(validate_config(raw), offline=True)
asyncio.run(server.initialize())
with TestClient(server.app) as client:
    page = client.get("/")
    mode = client.get("/offline/status")
    bridge = client.post("/bridge/v2/events")
    games = client.get("/games/api/version")
    assert page.status_code == 200 and "text/html" in page.headers.get("content-type", "")
    assert mode.status_code == 200 and mode.json()["mode"] == "offline-ui"
    assert bridge.status_code == 503 and bridge.json()["status"] == "offline"
    assert games.status_code == 503 and games.json()["status"] == "offline"
    ctx = server.default_context_cache
    assert all(engine is None for engine in (ctx.asr_engine, ctx.tts_engine, ctx.vad_engine, ctx.agent_engine, ctx.translate_engine))
print("REAL_APP_FRONTEND=200; OFFLINE_STATUS=200; OPERATIONS=503; ENGINES=UNLOADED")
'''
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stderr[-2500:]
    assert "REAL_APP_FRONTEND=200; OFFLINE_STATUS=200; OPERATIONS=503; ENGINES=UNLOADED" in result.stdout
