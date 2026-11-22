import math

import pytest

from celia.bridge.schemas import EventType
from celia.core.event_priority import (
    AdmissionError,
    AdmissionRejectCode,
    LEGACY_QUEUE_PRIORITY,
    Priority,
    classify_priority,
    expiry_deadline_mono,
    legacy_queue_priority,
)
from celia.core.turn_arbiter import ScheduledTurn, VerifiedActor


def bot(bot_id="discord", *scopes):
    return VerifiedActor(
        actor_id=f"process:{bot_id}",
        bot_id=bot_id,
        is_owner=False,
        scopes=frozenset(scopes),
    )


def test_priority_is_derived_from_allowlisted_event_and_verified_scope():
    actor = bot("discord", "events:discord_direct")
    assert classify_priority(EventType.DISCORD_DIRECT, actor, declared_bot_id="discord") is Priority.DISCORD_DIRECT
    with pytest.raises(AdmissionError) as unknown:
        classify_priority("caller_supplied_950", actor, declared_bot_id="discord")
    assert unknown.value.code is AdmissionRejectCode.UNKNOWN_EVENT_TYPE
    with pytest.raises(AdmissionError) as scope:
        classify_priority(EventType.DISCORD_DIRECT, bot("discord"), declared_bot_id="discord")
    assert scope.value.code is AdmissionRejectCode.BOT_SCOPE_REQUIRED


def test_bot_identity_cannot_claim_owner_or_stop_authority():
    with pytest.raises(ValueError, match="cannot be marked as owner"):
        VerifiedActor("process:discord", "discord", True, frozenset({"conversation:owner"}))
    actor = bot("discord", "conversation:owner", "conversation:stop")
    with pytest.raises(AdmissionError):
        classify_priority(EventType.OWNER_DIRECT, actor)
    with pytest.raises(AdmissionError):
        classify_priority(EventType.STOP_OVERRIDE, actor)


def test_owner_events_require_non_bot_verified_actor_and_separate_scopes():
    owner = VerifiedActor("local-user:criador", None, True, frozenset({"conversation:owner"}))
    assert classify_priority(EventType.OWNER_DIRECT, owner) is Priority.OWNER_DIRECT
    with pytest.raises(AdmissionError):
        classify_priority(EventType.STOP_OVERRIDE, owner)
    stop_owner = VerifiedActor("local-user:criador", None, True, frozenset({"conversation:stop"}))
    assert classify_priority(EventType.STOP_OVERRIDE, stop_owner) is Priority.STOP_OVERRIDE


def test_only_expected_bot_families_can_claim_live_game_and_discord_event_types():
    assert classify_priority(EventType.LIVE_CHAT, bot("tiktok", "events:live_chat"), declared_bot_id="tiktok") is Priority.LIVE_CHAT
    with pytest.raises(AdmissionError):
        classify_priority(EventType.LIVE_CHAT, bot("discord", "events:live_chat"), declared_bot_id="discord")
    assert classify_priority(EventType.GAME_REACTION, bot("minecraft", "events:game_reaction"), declared_bot_id="minecraft") is Priority.GAME_REACTION
    with pytest.raises(AdmissionError):
        classify_priority(EventType.GAME_REACTION, bot("react", "events:game_reaction"), declared_bot_id="react")
    with pytest.raises(AdmissionError) as mismatch:
        classify_priority(EventType.AMBIENT, bot("discord", "events:ambient"), declared_bot_id="tiktok")
    assert mismatch.value.code is AdmissionRejectCode.BOT_ID_MISMATCH


def test_legacy_priority_mapping_preserves_order_and_stop_is_not_chat():
    values = [legacy_queue_priority(p) for p in Priority if p is not Priority.STOP_OVERRIDE]
    assert values == sorted(values)
    assert all(0 <= value <= 100 for value in LEGACY_QUEUE_PRIORITY.values())
    with pytest.raises(AdmissionError) as rejected:
        legacy_queue_priority(Priority.STOP_OVERRIDE)
    assert rejected.value.code is AdmissionRejectCode.STOP_REQUIRES_CONTROL_PATH


def test_expiry_deadline_is_a_pure_monotonic_calculation_with_priority_cap():
    start = 987654.25
    assert expiry_deadline_mono(start, 1000, Priority.LIVE_CHAT) == start + 1
    assert expiry_deadline_mono(start, 120000, Priority.LIVE_CHAT) == start + 5
    assert expiry_deadline_mono(start, 120000, Priority.LIVE_SELECTED) == start + 20
    with pytest.raises(ValueError):
        expiry_deadline_mono(start, 249, Priority.LIVE_CHAT)
    with pytest.raises(ValueError):
        expiry_deadline_mono(math.nan, 1000, Priority.LIVE_CHAT)
    with pytest.raises(ValueError):
        expiry_deadline_mono(start, 1000, Priority.STOP_OVERRIDE)


def test_scheduled_turn_keeps_actor_and_handles_out_of_traced_payload():
    actor = bot("discord", "events:discord_direct")
    valid = dict(
        event_id="evt-00000001", source="discord", session_id="session-1",
        priority=int(Priority.DISCORD_DIRECT), received_mono=10.0, expires_mono=20.0,
        payload={"text": "Oi"}, actor=actor,
    )
    turn = ScheduledTurn(**valid)
    assert turn.actor is actor
    for key, value in (("priority", 950), ("actor", {"actor_id": actor.actor_id}),
                       ("future", object())):
        with pytest.raises((TypeError, ValueError)):
            ScheduledTurn(**{**valid, "payload": {"text": "Oi", key: value}})

