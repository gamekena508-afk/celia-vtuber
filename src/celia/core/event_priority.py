from __future__ import annotations

import math
from enum import Enum, IntEnum

from ..bridge.schemas import EventType
from .turn_arbiter import VerifiedActor


class AdmissionRejectCode(str, Enum):
    UNKNOWN_EVENT_TYPE = "unknown_event_type"
    VERIFIED_ACTOR_REQUIRED = "verified_actor_required"
    BOT_ID_BINDING_REQUIRED = "bot_id_binding_required"
    BOT_ID_MISMATCH = "bot_id_mismatch"
    BOT_TYPE_MISMATCH = "bot_type_mismatch"
    BOT_SCOPE_REQUIRED = "bot_scope_required"
    VERIFIED_OWNER_REQUIRED = "verified_owner_required"
    STOP_REQUIRES_CONTROL_PATH = "stop_requires_control_path"


class AdmissionError(Exception):
    """Typed, safe-to-return rejection from event admission policy."""

    def __init__(self, code: AdmissionRejectCode, message: str):
        super().__init__(message)
        self.code = code


class Priority(IntEnum):
    """P3 event classes from the V02 contract; values are internal only."""

    AMBIENT = 100
    LIVE_CHAT = 150
    LIVE_SELECTED = 250
    GAME_REACTION = 600
    GAME_CRITICAL_SPEECH = 700
    DISCORD_DIRECT = 900
    OWNER_DIRECT = 950
    STOP_OVERRIDE = 1000


# Initial policy budgets from plan §4.3. A caller TTL can only shorten these.
TTL_MS: dict[Priority, int] = {
    Priority.AMBIENT: 4000,
    Priority.LIVE_CHAT: 5000,
    Priority.LIVE_SELECTED: 20000,
    Priority.GAME_REACTION: 8000,
    Priority.GAME_CRITICAL_SPEECH: 5000,
    Priority.DISCORD_DIRECT: 10000,
    Priority.OWNER_DIRECT: 10000,
}

# One compatibility mapping to the existing ConversationEventQueue [0, 100] scale.
# It preserves class ordering; STOP_OVERRIDE is an out-of-band control action.
LEGACY_QUEUE_PRIORITY: dict[Priority, int] = {
    Priority.AMBIENT: 35,
    Priority.LIVE_CHAT: 45,
    Priority.LIVE_SELECTED: 50,
    Priority.GAME_REACTION: 80,
    Priority.GAME_CRITICAL_SPEECH: 85,
    Priority.DISCORD_DIRECT: 90,
    Priority.OWNER_DIRECT: 95,
}

_EVENT_PRIORITY: dict[EventType, Priority] = {
    EventType.AMBIENT: Priority.AMBIENT,
    EventType.LIVE_CHAT: Priority.LIVE_CHAT,
    EventType.LIVE_SELECTED: Priority.LIVE_SELECTED,
    EventType.GAME_REACTION: Priority.GAME_REACTION,
    EventType.GAME_CRITICAL_SPEECH: Priority.GAME_CRITICAL_SPEECH,
    EventType.DISCORD_DIRECT: Priority.DISCORD_DIRECT,
    EventType.OWNER_DIRECT: Priority.OWNER_DIRECT,
    EventType.STOP_OVERRIDE: Priority.STOP_OVERRIDE,
}
_BOT_EVENT_SCOPES: dict[EventType, tuple[str, frozenset[str] | None]] = {
    EventType.AMBIENT: ("events:ambient", None),
    EventType.LIVE_CHAT: ("events:live_chat", frozenset({"tiktok", "twitch", "youtube"})),
    EventType.LIVE_SELECTED: ("events:live_selected", frozenset({"tiktok", "twitch", "youtube"})),
    EventType.GAME_REACTION: ("events:game_reaction", frozenset({"minecraft"})),
    EventType.GAME_CRITICAL_SPEECH: ("events:game_critical_speech", frozenset({"minecraft"})),
    EventType.DISCORD_DIRECT: ("events:discord_direct", frozenset({"discord"})),
}


def classify_priority(
    event_type: EventType | str,
    actor: VerifiedActor,
    *,
    declared_bot_id: str | None = None,
) -> Priority:
    """Classify an allowlisted type using authenticated actor data, never body claims."""
    if not isinstance(actor, VerifiedActor):
        raise AdmissionError(
            AdmissionRejectCode.VERIFIED_ACTOR_REQUIRED,
            "actor must be produced by the authentication layer",
        )
    try:
        kind = event_type if isinstance(event_type, EventType) else EventType(event_type)
    except (TypeError, ValueError) as exc:
        raise AdmissionError(
            AdmissionRejectCode.UNKNOWN_EVENT_TYPE,
            "event_type is not registered for admission",
        ) from exc

    if kind in {EventType.OWNER_DIRECT, EventType.STOP_OVERRIDE}:
        required = "conversation:owner" if kind is EventType.OWNER_DIRECT else "conversation:stop"
        if (
            declared_bot_id is not None
            or actor.bot_id is not None
            or not actor.is_owner
            or required not in actor.scopes
        ):
            raise AdmissionError(
                AdmissionRejectCode.VERIFIED_OWNER_REQUIRED,
                "verified non-bot owner scope required",
            )
        return _EVENT_PRIORITY[kind]

    scope, allowed_bots = _BOT_EVENT_SCOPES[kind]
    if actor.bot_id is None or actor.is_owner:
        raise AdmissionError(
            AdmissionRejectCode.VERIFIED_ACTOR_REQUIRED,
            "verified non-owner bot identity required",
        )
    if declared_bot_id is None:
        raise AdmissionError(
            AdmissionRejectCode.BOT_ID_BINDING_REQUIRED,
            "request bot_id must be bound to the authenticated actor",
        )
    if declared_bot_id != actor.bot_id:
        raise AdmissionError(
            AdmissionRejectCode.BOT_ID_MISMATCH,
            "request bot_id does not match authenticated actor",
        )
    if allowed_bots is not None and actor.bot_id not in allowed_bots:
        raise AdmissionError(
            AdmissionRejectCode.BOT_TYPE_MISMATCH,
            "authenticated bot identity is not authorized for this event_type",
        )
    if scope not in actor.scopes:
        raise AdmissionError(
            AdmissionRejectCode.BOT_SCOPE_REQUIRED,
            "authenticated actor lacks the required event scope",
        )
    return _EVENT_PRIORITY[kind]


def legacy_queue_priority(priority: Priority) -> int:
    """Map P3 priority to the existing queue scale; STOP is not conversational work."""
    try:
        return LEGACY_QUEUE_PRIORITY[Priority(priority)]
    except (KeyError, ValueError) as exc:
        raise AdmissionError(
            AdmissionRejectCode.STOP_REQUIRES_CONTROL_PATH,
            "STOP_OVERRIDE must use a separately authorized control path",
        ) from exc


def expiry_deadline_mono(
    received_mono: float, requested_ttl_ms: int, priority: Priority
) -> float:
    """Pure deadline calculation; uses the receiver's monotonic clock, never wall time."""
    if not math.isfinite(received_mono) or received_mono < 0:
        raise ValueError("received_mono must be a finite monotonic timestamp")
    if (
        isinstance(requested_ttl_ms, bool)
        or not isinstance(requested_ttl_ms, int)
        or not 250 <= requested_ttl_ms <= 120000
    ):
        raise ValueError("requested_ttl_ms must be between 250 and 120000")
    try:
        max_ttl_ms = TTL_MS[Priority(priority)]
    except (KeyError, ValueError) as exc:
        raise ValueError("priority has no conversational TTL") from exc
    effective_ms = min(requested_ttl_ms, max_ttl_ms)
    return received_mono + effective_ms / 1000.0

