"""Effect confirmation is specific to the objective: progress, completion, failure and 'not verified' are different outcomes."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from celia.core.possession_trace import expected_items, observed_effect  # noqa: E402


def test_sneak_posture_is_verified_from_observed_state_and_not_from_acceptance():
    action = {"type": "sneak", "params": {"enabled": True}}
    assert observed_effect(action, {"sneaking": False}, {"sneaking": True})["confirmed"] is True
    assert observed_effect(action, {"sneaking": False}, {"sneaking": False})["confirmed"] is False


def pos(x, z, y=64.0):
    return {"position": {"x": float(x), "y": y, "z": float(z)}}


NAV = {"type": "navigate", "params": {"x": 20, "y": 64, "z": 0, "distance": 2}}


def test_arriving_at_the_destination_is_confirmed():
    e = observed_effect(NAV, pos(0, 0), pos(19, 0))
    assert e["outcome"] == "arrived" and e["confirmed"] is True and e["target_distance_after"] <= 2.5


def test_walking_toward_the_destination_without_arriving_is_progress_not_completion():
    e = observed_effect(NAV, pos(0, 0), pos(10, 0))
    assert e["outcome"] == "progress" and e["confirmed"] is False and e["progress"] is True


def test_walking_AWAY_from_the_destination_never_confirms_a_navigation():
    e = observed_effect(NAV, pos(10, 0), pos(0, 0))  # she moved 10 blocks, in the wrong direction
    assert e["outcome"] == "away" and e["confirmed"] is False and e["progress"] is False and e["moved_m"] == 10.0


def test_standing_still_is_no_change_and_missing_data_is_not_measured():
    assert observed_effect(NAV, pos(0, 0), pos(0.1, 0))["outcome"] == "no_change"
    assert observed_effect(NAV, None, pos(1, 1))["kind"] == "not_measured"
    assert observed_effect({"type": "navigate", "params": {"distance": 2}}, pos(0, 0), pos(1, 1))["kind"] == "not_measured"


def test_follow_is_judged_by_the_distance_to_the_owner():
    follow = {"type": "follow", "params": {"player": "PlayerOne", "distance": 3}}
    assert observed_effect(follow, {"owner_distance": 12.0}, {"owner_distance": 3.2})["outcome"] == "arrived"
    assert observed_effect(follow, {"owner_distance": 12.0}, {"owner_distance": 8.0})["outcome"] == "progress"
    assert observed_effect(follow, {"owner_distance": 5.0}, {"owner_distance": 11.0})["outcome"] == "away"
    assert observed_effect(follow, {"owner_distance": 5.0}, {})["kind"] == "not_measured"


def test_gaining_dirt_does_not_confirm_a_wood_collection():
    mine = {"type": "mine", "params": {"block": "oak_log", "count": 4, "radius": 16}}
    e = observed_effect(mine, {"inventory": {}}, {"inventory": {"dirt": 3}})
    assert e["outcome"] == "other_items_only" and e["confirmed"] is False, "earth is not wood"
    assert observed_effect(mine, {"inventory": {"dirt": 3}}, {"inventory": {"dirt": 3}})["outcome"] == "no_change"
    for log in ("oak_log", "birch_log", "spruce_log"):
        assert observed_effect(mine, {"inventory": {}}, {"inventory": {log: 4}}) ["confirmed"] is True, log


def test_ores_are_confirmed_by_what_they_actually_drop():
    iron = {"type": "mine", "params": {"block": "iron_ore", "count": 1, "radius": 16}}
    assert observed_effect(iron, {"inventory": {}}, {"inventory": {"raw_iron": 1}})["confirmed"] is True
    assert observed_effect(iron, {"inventory": {}}, {"inventory": {"cobblestone": 2}})["confirmed"] is False
    assert expected_items("stone") == {"cobblestone"} and expected_items("coal_ore") == {"coal"}


def test_mining_without_an_inventory_reading_is_not_verified_never_guessed():
    mine = {"type": "mine", "params": {"block": "oak_log", "count": 1, "radius": 8}}
    assert observed_effect(mine, None, {"inventory": {"oak_log": 1}})["kind"] == "not_measured"
    assert observed_effect(mine, {"inventory": {}}, {})["kind"] == "not_measured"


def test_crafting_needs_the_crafted_item_to_appear():
    craft = {"type": "craft", "params": {"item": "stick", "count": 4}}
    assert observed_effect(craft, {"inventory": {"oak_planks": 2}}, {"inventory": {"stick": 4}})["confirmed"] is True
    assert observed_effect(craft, {"inventory": {"oak_planks": 2}}, {"inventory": {"oak_planks": 2, "dirt": 1}})["confirmed"] is False
