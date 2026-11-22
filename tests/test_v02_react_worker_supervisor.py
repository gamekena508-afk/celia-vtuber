from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml
from types import SimpleNamespace

from celia.bridge.registry import BotRegistry
from celia.core.bot_process_manager import BotLaunchSpec, BotProcessManager
from src.celia.server import _react_launch_allowlist


def test_server_creates_only_fixed_react_launch_spec_when_runtime_and_token_exist(monkeypatch):
    executable = Path(__file__).resolve().parents[1] / "bots/react/.venv/Scripts/python.exe"
    if not executable.is_file():
        pytest.skip("dedicated React environment is not installed")
    token = "fixture-react-heartbeat-token-0123456789"
    monkeypatch.setenv("CELIA_BOT_HEARTBEAT_TOKEN_REACT", token)
    specs = _react_launch_allowlist(SimpleNamespace(system_config=SimpleNamespace(port=12393)))
    assert set(specs) == {"react-worker-v02"}
    spec = specs["react-worker-v02"]
    assert spec.executable.name.lower() == "python.exe"
    assert spec.args == ("worker_process.py",)
    assert spec.cwd.name == "react"
    assert spec.heartbeat_token == token
    assert token not in repr(spec)


@pytest.mark.asyncio
async def test_react_worker_real_process_heartbeats_then_stops(tmp_path: Path):
    project = tmp_path / "project"
    bot_root = project / "bots" / "react"
    config_dir = project / "config"
    bot_root.mkdir(parents=True)
    config_dir.mkdir()
    manifest = {"schema_version": 1, "bot_id": "react", "enabled": False,
                "launch_policy": "manual; P5 supervisor test only"}
    (bot_root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    config = {"schema_version": 1, "default_enabled": False, "startup_policy": "manual_only",
              "bots": {"react": {"enabled": False, "root": "bots/react", "launcher": "react-test",
                                   "health": "heartbeat", "restart": "never", "startup_timeout_sec": 8,
                                   "launchable": True, "manifest": "bots/react/manifest.yaml",
                                   "readiness": "test", "phase": "test"}}}
    (config_dir / "bots.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    manager_ref = {}
    token = "fixture-react-heartbeat-token-0123456789"

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            authorized = self.headers.get("Authorization") == f"Bearer {token}"
            manager = manager_ref.get("manager")
            accepted = authorized and manager is not None and manager.heartbeat(
                body["bot_id"], instance_id=body["instance_id"], queue_depth=body["queue_depth"],
                busy=body["state"] == "busy",
            )
            self.send_response(200 if accepted else 409)
            self.end_headers()
            self.wfile.write(b"{\"accepted\":true}" if accepted else b"{\"accepted\":false}")

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    manager = None
    try:
        spec = BotLaunchSpec(
            bot_id="react", cwd=bot_root.resolve(),
            executable=(Path(__file__).resolve().parents[1] / "bots/react/.venv/Scripts/python.exe").resolve(),
            args=(str(Path(__file__).resolve().parents[1] / "bots/react/worker_process.py"),
                  "--state-root", str(tmp_path / "isolated-state")),
            startup_timeout_s=8, stop_timeout_s=2, restart_policy="never",
            heartbeat_token=token,
            heartbeat_url=f"http://127.0.0.1:{server.server_port}/bridge/v2/bots/{{bot_id}}/heartbeat",
        )
        registry = BotRegistry.load(project, launch_allowlist={"react-test": spec})
        manager = BotProcessManager(registry, log_dir=tmp_path / "logs", max_restarts=0)
        manager_ref["manager"] = manager
        started = await manager.start("react")
        assert started.connected and started.process_alive and started.instance_id
        stopped = await manager.stop("react")
        assert stopped.state == "stopped" and not stopped.process_alive
        assert not any(token in path.read_text(encoding="utf-8", errors="replace")
                       for path in (tmp_path / "logs").glob("*"))
    finally:
        if manager is not None:
            await manager.shutdown()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
