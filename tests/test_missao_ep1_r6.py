"""Missão EP1, risco R6 (21/10): comentário virou ação. "Zera Minecraft." → minecraft_parar; "vou fazer uma série" → minecraft_objetivo.
Com a série gravando, nem a LLM nem os atalhos da persona mexem no corpo; só os comandos determinísticos da série."""
import asyncio
import importlib
import sys
from pathlib import Path

import pytest

import celia.core.minecraft_series as ms
from celia.agent.agents.basic_memory_agent import BasicMemoryAgent

TOOLS = [{"name": n} for n in ("minecraft_acao", "minecraft_objetivo", "minecraft_parar", "minecraft_observar", "search")]


class FakeCenter:
    def __init__(self, state):
        self._state_cache = (0.0, state)


@pytest.fixture
def series(monkeypatch):
    def set_state(state):
        monkeypatch.setattr(ms, "_center", None if state is False else FakeCenter(state))
    return set_state


@pytest.mark.parametrize("state, live", [(False, False), (None, False), ("NO_WORLD", False), ("EPISODE_END", False),
                                         ("INTRO_READY", True), ("GAMEPLAY", True), ("TALK_PAUSE", True), ("WORLD_LOADING", True)])
def test_series_live_follows_the_last_state_read_from_the_daemon(series, state, live):
    series(state)
    assert ms.series_live() is live


def _agent():
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    return agent


def _names(tools):
    return {t["name"] for t in tools}


def test_llm_loses_the_body_tools_while_the_series_records(series):
    series("GAMEPLAY")
    kept = BasicMemoryAgent._filter_tools_for_turn(_agent(), list(TOOLS), "Vou fazer uma série disso aqui.", origin="owner")
    assert not ({"minecraft_acao", "minecraft_objetivo", "minecraft_parar"} & _names(kept or []))


def test_outside_the_series_the_tools_are_untouched(series):
    series("NO_WORLD")
    kept = BasicMemoryAgent._filter_tools_for_turn(_agent(), list(TOOLS), "coleta madeira no minecraft", origin="owner")
    assert "minecraft_acao" in _names(kept or []) or "minecraft_objetivo" in _names(kept or [])


@pytest.mark.parametrize("text", ["Zera Minecraft.", "Para.", "espera aí, Célia", "para tudo", "coleta 3 madeira",
                                  "teu objetivo é chegar na idade do ferro", "continua a missão"])
def test_persona_shortcuts_do_not_touch_the_body_during_the_series(series, text):
    series("GAMEPLAY")
    forced = BasicMemoryAgent._forced_system_tool_call(_agent(), text)
    assert not forced or forced["name"] not in ms.SERIES_TOOLS_BLOCKED, (text, forced)


def test_outside_the_series_para_still_stops(series):
    series(False)
    forced = BasicMemoryAgent._forced_system_tool_call(_agent(), "Para.")
    assert forced and forced["name"] == "minecraft_parar"


# ---- segunda camada: o próprio MCP do corpo recusa enquanto a série grava ---------------------------------------------------------------------------
@pytest.fixture
def mcp_mod():
    root = Path(__file__).resolve().parents[1]
    for p in (root / "mcp", root / "integrations" / "minecraft"):
        if str(p) not in sys.path:
            sys.path.insert(0, str(p))
    return importlib.import_module("minecraft_mcp")


@pytest.mark.parametrize("state, refused", [("GAMEPLAY", True), ("INTRO_READY", True), ("NO_WORLD", False)])
def test_mcp_body_tools_refuse_while_recording(monkeypatch, mcp_mod, state, refused):
    calls = []

    async def fake_jev(method, path, payload=None, timeout=6.0):
        calls.append(path)
        if path == "/series":
            return {"ok": True, "state": state}
        if path == "/possession/perceive":
            return {"possessed": True}  # stops the non-series path early, without touching a body
        return {"ok": False}

    monkeypatch.setattr(mcp_mod, "_jev", fake_jev)
    out = asyncio.run(mcp_mod.minecraft_acao("jump", {}))
    goal = asyncio.run(mcp_mod.minecraft_objetivo("idade_do_ferro"))
    if refused:
        assert "series_recording" in out and "series_recording" in goal
        assert "/mission/stop" not in calls and "/mission/start" not in calls
    else:
        assert "series_recording" not in out and "body_possessed" in out


def test_mcp_daemon_offline_is_not_recording(monkeypatch, mcp_mod):
    async def offline(method, path, payload=None, timeout=6.0):
        return {"ok": False, "error": "jev_unavailable"}

    monkeypatch.setattr(mcp_mod, "_jev", offline)
    assert asyncio.run(mcp_mod._series_live()) is False
