"""Correlated stage tracing for the possession's BODY path (the voice path is traced by `latency_trace.py`, which already records, per turn and with
monotonic_ns: fim_fala, asr_start/end, llm_request_sent, llm_first/last_text_token, tts_start/end, audio_sent, playback_started).

Correlation ids: ``utterance_id`` (ASR) ⟷ ``turn_id`` (persona turn, same value in most paths) ⟷ ``speech_id`` (exists only after synthesis) ⟷ ``action_id``
(minted here per body command). The ids are carried, never guessed: an event keeps the ids it knew at that moment and later events add the rest.

Clocks: ``mono_ns`` is ``time.monotonic_ns()`` of THIS process only; durations inside the process use it. Anything that crosses to the daemon is a DURATION
measured inside the daemon (its own monotonic clock) and returned as a number; two processes' monotonic values are never subtracted. ``wall`` is only
for ordering/audit and is as accurate as the system clock.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

BODY_STAGES = ("owner_final", "agent_polled", "act_emit", "act_daemon_returned", "effect_checked")


class PossessionTrace:
    def __init__(self, root: Path) -> None:
        self.root = Path(root) / "trace"
        self.root.mkdir(parents=True, exist_ok=True)

    def mark(self, session_id: str, stage: str, **fields: Any) -> None:
        if not session_id:
            return
        row = {"stage": stage, "wall": round(time.time(), 3), "mono_ns": time.monotonic_ns(), **{k: v for k, v in fields.items() if v not in (None, "")}}
        try:
            with (self.root / f"{session_id}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
        except OSError:
            pass  # tracing never breaks the session


_DROPS = {"stone": {"cobblestone"}, "deepslate": {"cobbled_deepslate"}, "coal_ore": {"coal"}, "iron_ore": {"raw_iron"}, "copper_ore": {"raw_copper"}, "gold_ore": {"raw_gold"},
          "diamond_ore": {"diamond"}, "grass_block": {"dirt"}, "gravel": {"gravel", "flint"}}


def expected_items(block: str) -> set[str]:
    """What mining ``block`` should put in the inventory. Specific to the objective: gaining dirt does not confirm a log."""
    if block.endswith("_log") or block.endswith("_stem") or block.endswith("_wood"):
        return {block, block.replace("_wood", "_log"), "__any_log__"}
    for ore, items in _DROPS.items():
        if block == ore or block == "deepslate_" + ore:
            return set(items)
    return {block}


def observed_effect(action: dict[str, Any] | None, before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    """What can really be CONFIRMED about an action from two perceptions (position / distance to the TARGET / inventory), specific to the objective.
    ``ok:true`` from the body only means the contract was met. Outcomes distinguish progress, completion, failure and "not verified"; moving AWAY from the
    destination is never a success, and gaining an unrelated item is never a collection."""
    kind = (action or {}).get("type")
    params = (action or {}).get("params") or {}
    before, after = before or {}, after or {}
    pa, pb = before.get("position"), after.get("position")
    moved = None
    if isinstance(pa, dict) and isinstance(pb, dict):
        moved = round(sum((pa[k] - pb[k]) ** 2 for k in ("x", "y", "z")) ** 0.5, 2)
    if kind == "navigate":
        if not isinstance(pb, dict) or not isinstance(pa, dict) or not all(k in params for k in ("x", "y", "z")):
            return {"kind": "not_measured", "reason": "no position/target to compare"}
        goal = (float(params["x"]), float(params["y"]), float(params["z"]))
        d0 = ((pa["x"] - goal[0]) ** 2 + (pa["y"] - goal[1]) ** 2 + (pa["z"] - goal[2]) ** 2) ** 0.5
        d1 = ((pb["x"] - goal[0]) ** 2 + (pb["y"] - goal[1]) ** 2 + (pb["z"] - goal[2]) ** 2) ** 0.5
        reach = float(params.get("distance", 2)) + 0.5
        outcome = "arrived" if d1 <= reach else ("progress" if d1 < d0 - 0.5 else ("away" if d1 > d0 + 0.5 else "no_change"))
        return {"kind": "position", "outcome": outcome, "moved_m": moved, "target_distance_before": round(d0, 2), "target_distance_after": round(d1, 2),
                "confirmed": outcome == "arrived", "progress": outcome in {"arrived", "progress"}}
    if kind == "follow":
        da, db = before.get("owner_distance"), after.get("owner_distance")
        if db is None:
            return {"kind": "not_measured", "reason": "owner distance unknown after the action"}
        reach = float(params.get("distance", 3)) + 0.5
        outcome = "arrived" if db <= reach else ("progress" if da is not None and db < da - 0.5 else ("away" if da is not None and db > da + 0.5 else "no_change"))
        return {"kind": "position", "outcome": outcome, "moved_m": moved, "owner_distance_before": da, "owner_distance_after": db, "confirmed": outcome == "arrived",
                "progress": outcome in {"arrived", "progress"}}
    if kind == "sneak":
        before_sneak, after_sneak = before.get("sneaking"), after.get("sneaking")
        expected = bool(params.get("enabled"))
        confirmed = isinstance(after_sneak, bool) and after_sneak is expected
        return {"kind": "posture", "outcome": "confirmed" if confirmed else "not_confirmed",
                "before": before_sneak, "after": after_sneak, "expected": expected, "confirmed": confirmed}
    if kind == "mine":
        ia, ib = before.get("inventory"), after.get("inventory")
        if ia is None or ib is None:
            return {"kind": "not_measured", "reason": "no inventory before/after"}
        delta = {k: ib.get(k, 0) - ia.get(k, 0) for k in set(ia) | set(ib) if ib.get(k, 0) != ia.get(k, 0)}
        wanted = expected_items(str(params.get("block", "")))
        gained = {k: v for k, v in delta.items() if v > 0 and (k in wanted or ("__any_log__" in wanted and k.endswith("_log")))}
        outcome = "collected" if gained else ("other_items_only" if any(v > 0 for v in delta.values()) else "no_change")
        return {"kind": "inventory", "outcome": outcome, "delta": delta, "expected": sorted(w for w in wanted if not w.startswith("__")), "confirmed": bool(gained)}
    if kind in {"craft", "smelt", "eat", "place"}:
        ia, ib = before.get("inventory"), after.get("inventory")
        if ia is None or ib is None:
            return {"kind": "not_measured", "reason": "no inventory before/after"}
        delta = {k: ib.get(k, 0) - ia.get(k, 0) for k in set(ia) | set(ib) if ib.get(k, 0) != ia.get(k, 0)}
        item = str(params.get("item") or params.get("block") or "")
        got = delta.get(item, 0) > 0 if kind in {"craft", "smelt"} else bool(delta)
        return {"kind": "inventory", "outcome": "done" if got else "no_change", "delta": delta, "confirmed": bool(got)}
    if kind == "look":
        return {"kind": "not_measured", "reason": "head direction is not in the perception"}
    return {"kind": "not_measured", "reason": f"no effect check for {kind}"}
