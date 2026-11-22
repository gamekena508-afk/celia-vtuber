#!/usr/bin/env python3
"""MCP tools for Célia's Mineflayer body, not a second Minecraft agent.

The Célia runtime remains the only caller that interprets requests. This MCP
only reads normalized body state and sends a validated physical action over a
loopback Socket.IO channel.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any

import socketio
from mcp.server.fastmcp import FastMCP

from celia_paths import MINECRAFT_BODY_DIR, WORKSPACE

MINECRAFT_DIR = MINECRAFT_BODY_DIR.parent
if str(MINECRAFT_DIR) not in sys.path:
    sys.path.insert(0, str(MINECRAFT_DIR))

from celia_minecraft.actions import MinecraftActionError, validate_action  # noqa: E402
from celia_minecraft.control import read_control_token
from celia_minecraft.gameplay import GameStateStore, WoodGoal

mcp = FastMCP("minecraft_mcp")
_BODY_URL = os.getenv("CELIA_MINECRAFT_BODY_URL", "http://127.0.0.1:8080")
_AGENT_NAME = os.getenv("MINECRAFT_BOT_NAME", "Celia")
_state_cache: dict[str, Any] = {}
_state_timestamp = 0.0
_client = socketio.AsyncClient(reconnection=True, reconnection_attempts=3, reconnection_delay=2)
_store = GameStateStore()
_goal: WoodGoal | None = None
_action_lock = asyncio.Lock()
_connect_lock = asyncio.Lock()
_control_file = Path(os.getenv("CELIA_MINECRAFT_CONTROL_FILE", str(WORKSPACE / "var/runtime/minecraft-control.token")))
_journal_path = WORKSPACE / "var/logs/minecraft-actions.jsonl"
_safety_tasks: set[asyncio.Task] = set()
_IRON_ORDERS = frozenset({"idade_do_ferro", "idade_ferro", "iron_age", "ferro"})
_JEV_URL = os.getenv("CELIA_JEV_URL", "http://127.0.0.1:8784")


def _jev_request(method: str, path: str, payload: dict[str, Any] | None, timeout: float) -> dict[str, Any]:
    """Thin authenticated client of the Jev daemon (loopback). The executive lives there, not in this process."""
    import urllib.error
    import urllib.request

    if not _JEV_URL.startswith(("http://127.0.0.1:", "http://localhost:")):
        return {"ok": False, "error": "jev_url_not_loopback"}
    try:
        token = read_control_token(_control_file)
        request = urllib.request.Request(
            _JEV_URL + path,
            data=None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"X-Jev-Token": token, "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except (ValueError, OSError):
            detail = ""
        return {"ok": False, "error": str(detail or f"http_{exc.code}")[:80]}
    except (OSError, ValueError, urllib.error.URLError):
        return {"ok": False, "error": "jev_unavailable"}


async def _jev(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 6.0) -> dict[str, Any]:
    return await asyncio.to_thread(_jev_request, method, path, payload, timeout)


BODY_POSSESSED_MESSAGE = '[MC] {"ok":false,"status":"failed","error":"body_possessed","note":"o corpo está com o agente da possessão; esta ferramenta não comanda o corpo agora"}'


async def _body_possessed() -> bool:
    """An external controller owns the body during a possession: the persona's own tools must not compete for it. Fails OPEN (False) if the daemon
    cannot be asked, so a daemon outage never disables the tools by accident; the daemon itself still refuses a mission while possessed."""
    status = await _jev("GET", "/possession/perceive", None, 1.5)
    return bool(status.get("possessed"))


SERIES_LIVE_STATES = frozenset({"WORLD_LOADING", "INTRO_READY", "GAMEPLAY", "TALK_PAUSE", "RECOVERY", "BLOCKED"})
SERIES_LIVE_MESSAGE = ('[MC] {"ok":false,"status":"failed","error":"series_recording","note":"a série está gravando: o corpo só obedece aos '
                       'comandos de voz da série; esta ferramenta não mexe no corpo agora"}')


async def _series_live() -> bool:
    """24/10 (missão EP1, risco R6): em 21/10 "Zera Minecraft." virou minecraft_parar. Com a série gravando, as ferramentas que mexem no corpo
    recusam (a série tem o próprio caminho determinístico). Daemon fora do ar = não está gravando (False)."""
    status = await _jev("GET", "/series", None, 1.5)
    return status.get("ok") is True and status.get("state") in SERIES_LIVE_STATES


async def _jev_running() -> bool:
    status = await _jev("GET", "/mission", None, 1.5)
    return bool(status.get("running"))


def _journal(event: dict[str, Any]) -> None:
    _journal_path.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt" and os.path.islink(_journal_path):
        raise PermissionError("journal must not be a symlink")
    fd = os.open(_journal_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        line = json.dumps({"time": time.time(), **event}, ensure_ascii=False) + "\n"
        os.write(fd, line.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)


@_client.on("state-update")
async def _on_state_update(states: object) -> None:
    global _state_cache, _state_timestamp
    if isinstance(states, dict):
        _state_cache = states
        _state_timestamp = time.monotonic()
        state = states.get(_AGENT_NAME)
        if isinstance(state, dict):
            if _store.update(state):
                events = _store.drain_events()
                if _goal and _goal.status == "running" and set(events) & {"damage", "death", "disconnect"}:
                    _goal.cancel()
                    task = asyncio.create_task(_dispatch(validate_action({"type": "stop", "params": {}, "timeout_seconds": 5})))
                    _safety_tasks.add(task)
                    task.add_done_callback(_safety_tasks.discard)


async def _watch_goal(goal: WoodGoal) -> None:
    """A stale stream pauses physical work without waking a language model."""
    while goal.status == "running":
        await asyncio.sleep(0.2)
        if goal.status != "running":
            return
        state = _current_state()
        if not state or state.get("gameplay", {}).get("health", 0) <= 0:
            goal.cancel()
            await _dispatch(validate_action({"type": "stop", "params": {}, "timeout_seconds": 5}))
            return


async def _ensure_connected() -> bool:
    if _client.connected:
        return True
    async with _connect_lock:
        if _client.connected:
            return True
        try:
            if not _BODY_URL.startswith(("http://127.0.0.1:", "http://localhost:")):
                return False
            token = read_control_token(_control_file)
            await _client.connect(_BODY_URL, auth={"control_token": token}, transports=["websocket", "polling"])
            await _client.emit("listen-to-agents")
            return True
        except (OSError, ValueError, socketio.exceptions.ConnectionError):
            return False


def _current_state() -> dict[str, Any]:
    return _store.current() or {}


async def _dispatch(action) -> dict[str, Any]:
    """Only this MCP ingress writes. Stop bypasses the long-action mutex."""
    if not await _ensure_connected():
        return {"ok": False, "status": "failed", "error": "body_unavailable"}
    state = _current_state()
    if action.type != "stop" and not state:
        return {"ok": False, "status": "failed", "error": "no_fresh_state"}
    envelope = {"command_id": uuid.uuid4().hex, "action": action.as_dict(),
                "session_id": state.get("session_id"), "expected_state_seq": state.get("seq")}
    try:
        _journal({"event": "dispatch", **envelope})
    except OSError:
        return {"ok": False, "status": "failed", "error": "audit_unavailable"}
    try:
        result = await _client.call("minecraft-action", envelope, timeout=action.timeout_seconds + 5)
    except (OSError, asyncio.TimeoutError, socketio.exceptions.SocketIOError) as exc:
        result = {"ok": False, "status": "uncertain", "error": type(exc).__name__}
    if not isinstance(result, dict):
        result = {"ok": False, "status": "uncertain", "error": "invalid_body_result"}
    if isinstance(result.get("state"), dict):
        await _on_state_update({_AGENT_NAME: result["state"]})
    try:
        _journal({"event": "result", "command_id": envelope["command_id"], "result": result})
    except OSError:
        return {"ok": False, "status": "uncertain", "error": "result_audit_unavailable"}
    return result


@mcp.tool()
async def minecraft_observar() -> str:
    """Read normalized position, health, hunger, inventory, nearby entities, and activity."""
    if not await _ensure_connected():
        return "[MC] Corpo Mineflayer indisponível na porta local 8080."
    for _ in range(8):
        state = _current_state()
        if state and time.monotonic() - _state_timestamp <= 5:
            mission = await _jev("GET", "/mission", None, 1.5)
            if mission.get("mission"):
                state = {**state, "jev": {"mission_id": mission["mission"]["id"], "status": mission["mission"]["status"],
                                          "epoch": mission["mission"]["epoch"], "brief": mission.get("brief", "")}}
            return json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        await asyncio.sleep(0.5)
    return "[MC] Corpo Mineflayer conectado, mas ainda sem estado do mundo."


@mcp.tool()
async def minecraft_eventos(desde: int = 0) -> str:
    """Jev's durable event journal for the current mission (facts only) plus the factual brief.

    `desde` is an event id cursor. Chat, signs and world text are never journaled as facts.
    """
    status = await _jev("GET", "/mission", None, 3.0)
    if not status.get("ok"):
        return "[MC] " + json.dumps({"ok": False, "error": status.get("error", "jev_unavailable")}, ensure_ascii=False)
    if not status.get("mission"):
        return "[MC] " + json.dumps({"ok": True, "mission": None, "note": "sem missão do Jev; use minecraft_observar para o estado físico"}, ensure_ascii=False)
    journal = await _jev("GET", f"/events?since={int(desde)}", None, 3.0)
    return "[MC] " + json.dumps({"ok": True, "mission": status["mission"]["id"], "status": status["mission"]["status"],
                                 "epoch": status["mission"]["epoch"], "brief": status.get("brief", ""),
                                 "events": journal.get("events", [])}, ensure_ascii=False)


@mcp.tool()
async def minecraft_acao(tipo: str, parametros: dict[str, Any] | str | None = None, timeout_seconds: int = 20) -> str:
    """Execute one allowlisted Mineflayer action selected by Célia's central runtime.

    Valid types: stop, navigate, follow, look, jump, attack, mine, place,
    equip, use, interact, chat. This endpoint rejects free-form commands,
    shell, server slash commands, and unexpected parameters.
    """
    # 2026-10-20: the model once sent parametros='forward 1' (text). Pydantic rejected it at the MCP boundary, the action never ran and
    # the persona said "deu erro, como sempre". A JSON object in a string is accepted; anything else comes back as a readable refusal so
    # the model can retry — never guessed into an action.
    if isinstance(parametros, str):
        try:
            parsed = json.loads(parametros) if parametros.strip() else {}
        except ValueError:
            parsed = None
        if not isinstance(parsed, dict):
            return '[MC] Ação recusada: "parametros" precisa ser um objeto JSON (por exemplo {"distance": 5}); recebi texto solto. Nada foi executado.'
        parametros = parsed
    if await _series_live():
        return SERIES_LIVE_MESSAGE
    if tipo != "stop" and await _body_possessed():
        return BODY_POSSESSED_MESSAGE
    try:
        action = validate_action(
            {"type": tipo, "params": parametros or {}, "timeout_seconds": timeout_seconds}
        )
    except MinecraftActionError as exc:
        return f"[MC] Ação recusada pela allowlist: {exc}"
    if tipo == "stop":
        if _goal:
            _goal.cancel()
        stopped = await _jev("POST", "/mission/stop", {}, 8.0)
        result = await _dispatch(action)
        mission = stopped.get("mission") if isinstance(stopped, dict) else None
        if isinstance(mission, dict) and isinstance(result, dict):
            result = {**result, "mission_id": mission["id"], "epoch": mission["epoch"], "brief": stopped.get("brief", "")}
    else:
        if await _jev_running():
            return '[MC] {"ok":false,"status":"failed","error":"busy_jev_mission"}'
        if _action_lock.locked() or (_goal and _goal.status == "running"):
            return '[MC] {"ok":false,"status":"failed","error":"busy"}'
        async with _action_lock:
            result = await _dispatch(action)
    return "[MC] " + json.dumps(result, ensure_ascii=False, separators=(",", ":"))


@mcp.tool()
async def minecraft_objetivo(objetivo: str = "coletar_madeira", quantidade: int = 1) -> str:
    """Start a goal for Jev, the executive that plays Minecraft.

    `idade_do_ferro`: persistent autonomous mission to an equipped iron pickaxe (returns at once; the
    Jev daemon keeps working; progress via minecraft_eventos). `retomar`: continue a stopped or
    interrupted mission. `status`: factual brief. `coletar_madeira`: the bounded wood recipe.
    Internal MCP operation, never a goal extracted from world chat.
    """
    global _goal
    if objetivo != "status" and await _series_live():
        return SERIES_LIVE_MESSAGE
    if objetivo != "status" and await _body_possessed():
        return BODY_POSSESSED_MESSAGE
    key = (objetivo or "").strip().lower()
    if key in _IRON_ORDERS or key in {"retomar", "continuar", "resume"}:
        if _goal and _goal.status == "running":
            _goal.cancel()
        reply = await _jev("POST", "/mission/start", {
            "order": "Célia, teu objetivo é chegar à Idade do Ferro no Minecraft.", "source": "owner",
            "resume_only": key in {"retomar", "continuar", "resume"}}, 25.0)
        if not reply.get("ok"):
            return "[MC] " + json.dumps({"ok": False, "status": "failed", "error": reply.get("error", "jev_unavailable")}, ensure_ascii=False)
        started = reply["status"] == "started"
        return "[MC] " + json.dumps({
            "ok": True, "status": reply["status"], "goal": "idade_do_ferro", "mission_id": reply["mission_id"], "epoch": reply["epoch"],
            "brief": reply.get("brief", ""),
            "nota": ("Missão aceita pelo Jev. Nada foi coletado ainda; o progresso real vem de minecraft_eventos." if started
                     else "Missão retomada de onde parou. O inventário real e o progresso estão nos fatos do Jev; não diga que nada foi coletado."),
        }, ensure_ascii=False)
    if key == "status":
        status = await _jev("GET", "/mission", None, 4.0)
        return "[MC] " + json.dumps(status, ensure_ascii=False)
    if objetivo != "coletar_madeira":
        return '[MC] {"ok":false,"status":"failed","error":"unknown_goal"}'
    if await _jev_running():
        return '[MC] {"ok":false,"status":"failed","error":"busy_jev_mission"}'
    if _action_lock.locked():
        return '[MC] {"ok":false,"status":"failed","error":"busy"}'
    await minecraft_observar()
    state = _current_state()
    if not state:
        return '[MC] {"ok":false,"status":"failed","error":"no_fresh_state"}'
    try:
        goal = WoodGoal(state, count=quantidade, max_actions=3)
    except ValueError as exc:
        return "[MC] " + json.dumps({"ok": False, "status": "failed", "error": str(exc)})
    async with _action_lock:
        _goal = goal
        watchdog = asyncio.create_task(_watch_goal(goal))
        results = []
        try:
            for _ in range(3):
                state = _current_state()
                if not state:
                    goal.cancel()
                    break
                action = goal.propose(state)
                if action is None:
                    goal.cancel()
                    break
                result = await _dispatch(action)
                results.append(result)
                goal.record_result(result, _current_state() or {})
                if goal.status != "running":
                    break
                seq = state.get("seq")
                for _ in range(12):
                    current = _current_state()
                    if current and current.get("seq", 0) > seq:
                        break
                    await asyncio.sleep(0.1)
            if goal.status == "running":
                goal.cancel()
        finally:
            watchdog.cancel()
            await asyncio.gather(watchdog, return_exceptions=True)
        return "[MC] " + json.dumps({"ok": goal.status == "succeeded", "status": goal.status,
            "goal": objetivo, "results": results, "state": _current_state()}, ensure_ascii=False)


@mcp.tool()
async def minecraft_parar() -> str:
    """Authenticated stop: ends the Jev mission (epoch bump) and the physical action, without any model call."""
    return await minecraft_acao("stop", {})


if __name__ == "__main__":
    mcp.run()
