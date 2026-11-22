"""The MCP tools are a thin authenticated client of the Jev daemon: they must fail closed and never run a mission themselves."""
import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "mcp"))
sys.path.insert(0, str(ROOT / "integrations/minecraft"))


def load(monkeypatch, tmp_path, url):
    token = tmp_path / "control.token"
    token.write_text("t" * 48)
    token.chmod(0o600)
    monkeypatch.setenv("CELIA_JEV_URL", url)
    monkeypatch.setenv("CELIA_MINECRAFT_CONTROL_FILE", str(token))
    spec = importlib.util.spec_from_file_location("minecraft_mcp_under_test", ROOT / "mcp/minecraft_mcp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_iron_order_with_the_daemon_down_reports_the_real_failure_and_starts_nothing(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    reply = asyncio.run(module.minecraft_objetivo("idade_do_ferro", 1))
    data = json.loads(reply.removeprefix("[MC] "))
    assert data == {"ok": False, "status": "failed", "error": "jev_unavailable"}


def test_a_non_loopback_daemon_url_is_never_contacted(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://example.com:8784")
    assert module._jev_request("GET", "/mission", None, 1.0) == {"ok": False, "error": "jev_url_not_loopback"}


def test_manual_actions_are_refused_while_a_jev_mission_runs_but_stop_always_goes_through(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    calls = []

    async def fake_jev(method, path, payload=None, timeout=6.0):
        calls.append((method, path))
        return {"ok": True, "running": True, "mission": {"id": "m", "status": "running", "epoch": 1}}

    async def fake_dispatch(action):
        calls.append(("dispatch", action.type))
        return {"ok": True, "status": "succeeded"}

    monkeypatch.setattr(module, "_jev", fake_jev)
    monkeypatch.setattr(module, "_dispatch", fake_dispatch)
    busy = asyncio.run(module.minecraft_acao("navigate", {"x": 1, "y": 64, "z": 1, "distance": 2}, 10))
    assert json.loads(busy.removeprefix("[MC] "))["error"] == "busy_jev_mission"
    assert ("dispatch", "navigate") not in calls
    asyncio.run(module.minecraft_parar())
    assert ("POST", "/mission/stop") in calls and ("dispatch", "stop") in calls


# ------------------------------------------------------------------ 2026-10-20: a text "parametros" must never silence the body ----------------
def test_text_parameters_come_back_as_a_readable_refusal_not_a_validation_crash(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    reply = asyncio.run(module.minecraft_acao("follow", "forward 1"))  # measured live: parametros='forward 1'
    assert reply.startswith("[MC] Ação recusada") and "objeto JSON" in reply and "Nada foi executado" in reply


def test_a_json_object_inside_a_string_is_accepted_and_still_goes_through_the_allowlist(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    refused = asyncio.run(module.minecraft_acao("follow", '{"player": "x; rm -rf", "distance": 5}'))
    assert refused.startswith("[MC] Ação recusada pela allowlist"), refused  # the string became a dict and was judged like any other
    assert asyncio.run(module.minecraft_acao("shell", '{}')).startswith("[MC] Ação recusada pela allowlist")


def test_empty_text_parameters_are_treated_as_no_parameters(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    reply = asyncio.run(module.minecraft_acao("stop", "  "))
    assert "objeto JSON" not in reply


# ------------------------------------------------------------------ the persona's own tools do not compete for a possessed body ------------------
def test_body_tools_are_refused_while_an_external_controller_owns_the_body_but_stop_and_status_still_work(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    asked = []

    async def fake_jev(method, path, payload=None, timeout=6.0):
        asked.append((method, path))
        if path == "/possession/perceive":
            return {"ok": True, "possessed": True}
        return {"ok": False, "error": "unexpected"}
    monkeypatch.setattr(module, "_jev", fake_jev)
    for reply in (asyncio.run(module.minecraft_acao("follow", {"player": "PlayerOne", "distance": 3})), asyncio.run(module.minecraft_objetivo("coletar_madeira", 1))):
        assert json.loads(reply.removeprefix("[MC] "))["error"] == "body_possessed"
    assert ("POST", "/mission/start") not in asked and not any(path.startswith("/body") for _m, path in asked), "nothing reached the body or started a mission"
    assert "body_possessed" not in asyncio.run(module.minecraft_objetivo("status", 1)), "reading the status is still allowed"


def test_when_the_daemon_cannot_be_asked_the_tools_are_not_disabled_by_accident(monkeypatch, tmp_path):
    module = load(monkeypatch, tmp_path, "http://127.0.0.1:1")
    assert asyncio.run(module._body_possessed()) is False  # fails OPEN: an outage never silently kills the persona's tools
