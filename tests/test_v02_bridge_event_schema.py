from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from celia.bridge.schemas import BotHeartbeatV2, BridgeEventV2, EventReceiptV2, EventType
from celia.core.event_priority import AdmissionError, AdmissionRejectCode, Priority, classify_priority
from celia.core.turn_arbiter import VerifiedActor


def valid_event(**updates):
    event = {
        "version": "2.0",
        "event_id": "evt-00000001",
        "bot_id": "discord",
        "session_id": "session-a",
        "event_type": "discord_direct",
        "occurred_at": datetime(2026, 11, 19, tzinfo=timezone.utc),
        "ttl_ms": 10000,
        "payload": {"text": "Oi"},
    }
    event.update(updates)
    return event


def test_bridge_event_accepts_allowlisted_wire_contract():
    event = BridgeEventV2.model_validate(valid_event())
    assert event.version == "2.0"
    assert event.event_type == EventType.DISCORD_DIRECT.value
    assert event.payload == {"text": "Oi"}


def test_bridge_event_rejects_untrusted_priority_and_unknown_fields():
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(priority=950))
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(payload={"text": "Oi", "priority": 950}))
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(unexpected=True))


def test_bridge_event_rejects_unknown_ids_types_and_ttl_outside_contract():
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(bot_id="vrchat"))
    future_event = BridgeEventV2.model_validate(valid_event(event_type="future.integrator.event"))
    assert future_event.event_type == "future.integrator.event"
    actor = VerifiedActor("process:discord", "discord", False, frozenset({"events:discord_direct"}))
    with pytest.raises(AdmissionError) as rejected:
        classify_priority(future_event.event_type, actor, declared_bot_id=future_event.bot_id)
    assert rejected.value.code is AdmissionRejectCode.UNKNOWN_EVENT_TYPE
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(event_type="x"))
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(event_type="x" * 81))
    with pytest.raises(ValidationError):
        BridgeEventV2.model_validate(valid_event(occurred_at=datetime(2026, 11, 19)))
    for ttl in (249, 120001):
        with pytest.raises(ValidationError):
            BridgeEventV2.model_validate(valid_event(ttl_ms=ttl))


def test_receipt_and_heartbeat_keep_the_planned_status_shapes():
    receipt = EventReceiptV2.model_validate(
        {"event_id": "evt-00000001", "accepted": False, "status": "duplicate"}
    )
    assert receipt.status == "duplicate"
    heartbeat = BotHeartbeatV2.model_validate(
        {
            "version": "2.0",
            "bot_id": "minecraft",
            "instance_id": "instance-1",
            "state": "connected",
            "queue_depth": 0,
            "capabilities": ["chat"],
        }
    )
    assert heartbeat.queue_depth == 0
    with pytest.raises(ValidationError):
        BotHeartbeatV2.model_validate({**heartbeat.model_dump(), "priority": 1000})
