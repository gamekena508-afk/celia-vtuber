from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


BOT_ID_VALUES = frozenset({"discord", "minecraft", "react", "tiktok", "twitch", "youtube", "retro"})


BotId = Literal[
    "discord",
    "minecraft",
    "react",
    "tiktok",
    "twitch",
    "youtube",
    "retro",
]


class EventType(str, Enum):
    """Allowlisted conversational categories; priority is always server-derived."""

    AMBIENT = "ambient"
    LIVE_CHAT = "live_chat"
    LIVE_SELECTED = "live_selected"
    GAME_REACTION = "game_reaction"
    GAME_CRITICAL_SPEECH = "game_critical_speech"
    DISCORD_DIRECT = "discord_direct"
    OWNER_DIRECT = "owner_direct"
    STOP_OVERRIDE = "stop_override"


class BridgeEventV2(BaseModel):
    """Wire contract for one normalized bot event. No caller priority is accepted."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: Literal["2.0"]
    event_id: str = Field(min_length=8, max_length=128)
    bot_id: BotId
    session_id: str = Field(min_length=1, max_length=128)
    event_type: str = Field(min_length=3, max_length=80)
    occurred_at: datetime
    ttl_ms: int = Field(ge=250, le=120000)
    payload: dict[str, Any] = Field(default_factory=dict)
    correlation_id: str | None = Field(default=None, min_length=1, max_length=128)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone for traceability")
        return value

    @field_validator("payload")
    @classmethod
    def reject_payload_priority(cls, payload: dict[str, Any]) -> dict[str, Any]:
        # Keep the authority boundary obvious even though payload is extensible.
        if "priority" in payload:
            raise ValueError("priority is calculated by the Bridge")
        return payload


class EventReceiptV2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=8, max_length=128)
    accepted: bool
    status: Literal["queued", "rejected", "duplicate", "expired", "busy"]
    reason: str | None = None


class BotHeartbeatV2(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    version: Literal["2.0"]
    bot_id: BotId
    instance_id: str = Field(min_length=1, max_length=128)
    state: Literal[
        "starting", "connected", "busy", "degraded", "disconnected", "stopping"
    ]
    queue_depth: int = Field(ge=0, le=100000)
    capabilities: list[str] = Field(default_factory=list, max_length=64)
    last_error_code: str | None = Field(default=None, max_length=128)
