"""2026-10-20: the initiative loop got no Minecraft context because its source (:8789) no longer exists. It now reads the HUD the central serves."""
import asyncio
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from celia.core import minecraft_hud  # noqa: E402
from celia.core.cognitive_loop import cognitive_loop, describe_hud_for_thought  # noqa: E402


@pytest.fixture(autouse=True)
def isolate_pending_game_event_context():
    previous = (cognitive_loop._mc_event_context, cognitive_loop._mc_event_key, cognitive_loop._mc_event_expires)
    cognitive_loop._mc_event_context, cognitive_loop._mc_event_key, cognitive_loop._mc_event_expires = "", "", 0.0
    yield
    cognitive_loop._mc_event_context, cognitive_loop._mc_event_key, cognitive_loop._mc_event_expires = previous

HUD = {"available": True, "celia": {"health": 13.5, "hunger": 17, "held": "iron_pickaxe", "position": {"x": 0, "y": 64, "z": 0}},
       "clock": {"hour": 20, "minute": 31, "phase": "night"}, "criador": {"online": True, "position": {"x": 30, "y": 64, "z": 40}}}


def test_the_real_facts_are_written_in_one_short_line():
    line = describe_hud_for_thought(HUD)
    assert "vida 13.5/20" in line and "fome 17/20" in line and "iron pickaxe" in line and "20:31 (noite)" in line and "Criador a 50 blocos" in line
    assert len(line) < 160


def test_v6_grounded_item_and_mob_awareness_without_inventing_memories():
    live = dict(HUD)
    live["celia"] = {**HUD["celia"], "held": "iron_axe"}
    live["inventory"] = {"poppy": 1, "iron_axe": 1}
    live["threats"] = [{"name": "creeper", "distance": 4}]
    live["nearby_blocks"] = ["oak_log"]
    line = describe_hud_for_thought(live)
    assert "iron axe (machado de ferro)" in line
    assert "poppy x1" in line
    assert "creeper a 4 blocos" in line
    assert "oak log" in line
    assert "espada" not in line and "lava de ontem" not in line


def test_unavailable_or_malformed_data_is_empty_never_invented():
    for bad in (None, {}, {"available": False}, {"available": True, "celia": None}, "x", []):
        assert describe_hud_for_thought(bad) == "", bad


def test_owner_out_of_sight_is_said_as_such_not_as_a_distance():
    hud = dict(HUD, criador={"online": True, "position": None})
    assert "Criador fora do meu campo de visão" in describe_hud_for_thought(hud) and "blocos" not in describe_hud_for_thought(hud)


def test_the_loop_context_follows_the_hud_and_is_cleared_when_it_goes_away(monkeypatch):
    monkeypatch.delenv("CELIA_MC_SNAPSHOT_URL", raising=False)
    monkeypatch.setattr(minecraft_hud, "fetch_hud", lambda *a, **k: HUD)
    asyncio.run(cognitive_loop._refresh_minecraft_context())
    assert "vida 13.5/20" in cognitive_loop._mc_context
    monkeypatch.setattr(minecraft_hud, "fetch_hud", lambda *a, **k: {"ok": True, "available": False, "reason": "jev_daemon_offline"})
    asyncio.run(cognitive_loop._refresh_minecraft_context())
    assert cognitive_loop._mc_context == "", "stale game facts must not reach the persona"

    def boom(*a, **k):
        raise OSError("down")
    cognitive_loop._mc_context = "vida 1/20"
    monkeypatch.setattr(minecraft_hud, "fetch_hud", boom)
    asyncio.run(cognitive_loop._refresh_minecraft_context())
    assert cognitive_loop._mc_context == ""


def test_an_explicit_snapshot_url_still_wins_for_anyone_who_runs_one(monkeypatch):
    monkeypatch.setenv("CELIA_MC_SNAPSHOT_URL", "http://127.0.0.1:9/never")
    monkeypatch.setattr(minecraft_hud, "fetch_hud", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not be called")))
    cognitive_loop._mc_context = "antigo"
    asyncio.run(cognitive_loop._refresh_minecraft_context())
    assert cognitive_loop._mc_context == "antigo"


def test_body_event_enters_existing_context_once_and_expires(monkeypatch):
    monkeypatch.delenv("CELIA_MC_SNAPSHOT_URL", raising=False)
    monkeypatch.setattr(minecraft_hud, "fetch_hud", lambda *a, **k: HUD)
    cognitive_loop.note_minecraft_event("body:session:seq-8", {"events": ["inventory_change"],
                                         "inventory_delta": {"oak_log": 6}, "donor": "unknown"}, ttl_sec=2)
    assert cognitive_loop.note_minecraft_event("body:session:seq-8", {"events": ["inventory_change"]}) is False
    asyncio.run(cognitive_loop._refresh_minecraft_context())
    assert "body:session:seq-8" in cognitive_loop._mc_context and '"oak_log":6' in cognitive_loop._mc_context
    cognitive_loop._mc_event_expires = 0
    asyncio.run(cognitive_loop._refresh_minecraft_context())
    assert "body:session:seq-8" not in cognitive_loop._mc_context
