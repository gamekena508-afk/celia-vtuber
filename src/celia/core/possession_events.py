"""Bridge verified Jev journal facts into the active possession event queue."""

from __future__ import annotations

from typing import Any


def publish_jev_event(possession: Any, store: Any, event_id: int, mission_id: str, epoch: int) -> dict[str, Any]:
    """Queue only current-epoch, whitelisted facts; preserve the journal timestamp across retries."""
    if not possession.active():
        return {"queued": False, "reason": "possession_inactive"}
    event = store.event(int(event_id))
    mission = store.mission(str(mission_id))
    if event is None or event["mission_id"] != mission_id or int(event["epoch"]) != int(epoch):
        return {"queued": False, "reason": "unknown_event"}
    if mission is None or int(mission["epoch"]) != int(epoch):
        return {"queued": False, "reason": "stale_epoch"}

    from .jev_narration import event_facts

    facts = event_facts(str(event["kind"]), event.get("payload") or {}, mission)
    if not facts:
        return {"queued": False, "reason": "no_verified_facts"}

    session_id = possession.s.session_id
    key = f"jev:{session_id}:{mission_id}:{epoch}:{event_id}"
    seq = possession.post_event(
        "game_event",
        dedupe_key=key,
        source="jev_journal",
        event_key=key,
        event_id=int(event_id),
        mission_id=str(mission_id),
        mission_epoch=int(epoch),
        world_id=str(mission.get("world_id") or ""),
        observed_at=float(event["ts"]),
        facts=facts,
    )
    return {"queued": True, "seq": seq, "event_key": key, "observed_at": float(event["ts"])}
