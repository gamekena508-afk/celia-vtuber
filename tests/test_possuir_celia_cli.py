"""The CLI against a REAL HTTP server (uvicorn on a free port) with the real possession core; only the daemon, TTS and screen are fakes.
Proves the loop an agent runs: start -> next -> say/act -> a correction -> stale -> end phrase -> STOP (exit 3)."""
import asyncio
import json
import os
import socket
import stat
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from celia.core import possession as possession_module  # noqa: E402
from celia.core import possession_io as io  # noqa: E402
from celia import possession_routes as routes  # noqa: E402
from test_possession_routes import Fakes  # noqa: E402

CLI = ROOT / "bin" / "possuir-celia"


@pytest.fixture()
def server(tmp_path, monkeypatch):
    fakes = Fakes()
    monkeypatch.setattr(possession_module, "_instance", possession_module.Possession(tmp_path / "poss"))
    monkeypatch.setattr(routes, "_trace", None)
    monkeypatch.setattr(io, "daemon_call", fakes.daemon_call)
    monkeypatch.setattr(io, "capture_screen", fakes.capture_screen)
    monkeypatch.setattr(io, "persona", lambda: {"ok": True, "persona_prompt": "Você é a Célia", "chars": 15})
    monkeypatch.setattr(io, "cancel_playback", lambda sid: {"ok": True})
    monkeypatch.setattr(io, "current_speech_id", lambda: "")
    app = FastAPI()
    app.include_router(routes.init_possession_routes())
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=lambda: loop.run_until_complete(srv.serve()), daemon=True)
    thread.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    env = {**os.environ, "CELIA_CENTRAL_URL": f"http://127.0.0.1:{port}", "CELIA_VAR_DIR": str(tmp_path / "var"), "CELIA_WORKSPACE": str(ROOT)}

    def cli(*args):
        done = subprocess.run([sys.executable, str(CLI), *args], capture_output=True, text=True, env=env, timeout=60)
        try:
            body = json.loads(done.stdout.strip().splitlines()[-1]) if done.stdout.strip() else {}
        except ValueError:
            body = {"raw": done.stdout}
        return done.returncode, body

    def owner(text, **kw):
        fut = asyncio.run_coroutine_threadsafe(routes.mirror_owner_text(text, source="microphone", authenticated_owner=kw.get("auth", True), self_echo=kw.get("echo", False)), loop)
        taken = fut.result(5)
        time.sleep(0.15)
        return taken
    yield cli, owner, fakes, tmp_path
    srv.should_exit = True
    thread.join(5)


def test_the_whole_loop_the_body_agent_runs(server):
    cli, owner, fakes, tmp = server
    code, started = cli("start", "--controller", "claude")
    assert code == 0 and started["ok"] and "lease" not in started and started["body"] == "held" and started["world_epoch"] == 12
    lease_file = tmp / "var/possession/lease-claude.json"
    assert stat.S_IMODE(lease_file.stat().st_mode) == 0o600, "the lease is private to the user"
    assert cli("start", "--controller", "codex")[0] == 5, "a second controller is refused while the first holds it"
    assert cli("say", "oi")[0] != 0, "there is no say command any more"

    code, quiet = cli("next", "--wait", "0", "--coalesce-ms", "0")
    assert code == 0 and quiet["events"] == [] and quiet["active"] is True

    assert owner("Célia, vem aqui do meu lado pra aparecer") is False, "the persona still gets it"
    code, got = cli("next", "--wait", "2", "--coalesce-ms", "0")
    assert code == 0 and len(got["events"]) == 1
    seq, cmd0 = got["events"][0]["seq"], got["command_version"]
    assert cli("next", "--wait", "0")[1]["events"] == [], "the cursor is kept: nothing is delivered twice"

    code, did = cli("act", "--shortcut", "come_here", "--reply-to", str(seq))
    assert code == 0 and did["ok"] and did["latency_ms"] is not None and did["effect"]["kind"] in {"position", "not_measured"}
    assert cli("sheet", "--json", json.dumps({"objective": "aparecer na câmera", "task": "ao lado do criador", "framing": {"status": "unknown"}, "hint": "posição ajustada; câmera não verificada"}))[0] == 0

    # interjections and fragments never invalidate the running task
    owner("hum")
    owner("tá")
    assert cli("next", "--wait", "1", "--coalesce-ms", "0")[1]["command_version"] == cmd0
    assert cli("act", "--shortcut", "face_owner")[0] == 0

    # a stop order cancels what was planned; the next plan starts from `next`
    owner("Célia, para tudo agora")
    code, stale = cli("act", "--shortcut", "come_here")
    assert code == 4 and stale["stale"] is True
    assert cli("next", "--wait", "1", "--coalesce-ms", "0")[0] == 0
    assert cli("act", "--shortcut", "face_owner")[0] == 0, "after reading the stop the agent acts again"

    # the world/session changed under the agent's feet
    fakes.refuse, fakes.epoch = "world_epoch_changed", 13
    code, changed = cli("act", "--shortcut", "come_here")
    assert code == 7 and changed["world_epoch_changed"] is True
    fakes.refuse = None
    assert cli("state")[0] == 0

    # finishing a miniquest is not the end
    assert cli("quest", "--objective", "abrigo", "--miniquest", "", "--next", "", "--pending", "")[0] == 0
    assert cli("state")[1]["possession"]["active"] is True

    owner("Acabou a gravação.")
    code, end = cli("next", "--wait", "1")
    assert code == 3 and "STOP" in end
    for args in (("act", "--shortcut", "come_here"), ("state",), ("sheet", "--json", "{}")):
        assert cli(*args)[0] == 3, args
    assert not possession_module.is_possessed() and possession_module.sheet_prompt_block() == ""
    assert owner("vem aqui") is False


def test_silence_does_not_stop_the_body_and_keeps_the_possession(server):
    cli, owner, fakes, _ = server
    cli("start", "--controller", "codex")
    assert owner("Célia, fica quieta") is True
    assert cli("next", "--wait", "1", "--coalesce-ms", "0")[1]["silenced"] is True
    assert cli("act", "--shortcut", "face_owner")[0] == 0
    assert possession_module.is_possessed()


def test_stop_command_ends_it_and_removes_the_lease_and_never_restarts(server):
    cli, owner, fakes, tmp = server
    cli("start", "--controller", "claude")
    code, body = cli("stop")
    assert code == 0 and body["stopped"] is True
    assert not list((tmp / "var/possession").glob("lease-*.json"))
    assert cli("next", "--wait", "0")[0] == 1, "no lease: the agent cannot continue"
    assert not possession_module.is_possessed()
