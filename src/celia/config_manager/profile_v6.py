"""V6 profile preferences. Policies adjust behavior, never tool authority."""
from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROFILE_KEYS = ("conversation_autonomous", "chat", "roteiro", "gravacao", "cohost", "autonomous")
TRIGGER_KEYS = (
    "direct_question", "achievement", "danger", "combat", "damage", "death",
    "respawn", "discovery", "goal_complete", "goal_failed", "world_change",
    "audience", "script_beat", "silence",
)


class TriggerV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = True
    priority: int = Field(50, ge=0, le=100)
    cooldown_seconds: int = Field(25, ge=0, le=3600)


class SocialV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    participation: int = Field(50, ge=0, le=100)
    interruption: int = Field(30, ge=0, le=100)
    silence_seconds: int = Field(20, ge=2, le=120)
    max_spontaneous_per_10m: int = Field(8, ge=0, le=60)
    # Continuous profiles (Autônoma, Conversa Autônoma): the quota is per MINUTE and replaces the 10-minute/hour caps.
    # None = profile default (autonomous 6, conversation_autonomous 4); 0 = never speaks alone. Ignored by the other profiles.
    max_spontaneous_per_minute: int | None = Field(None, ge=0, le=30)
    response_words: int = Field(60, ge=5, le=300)
    screen_awareness: bool = True
    script_tracking: bool = False
    audience_chat: bool = False
    respect_concentration: bool = True
    inference_budget_per_hour: int = Field(40, ge=0, le=240)
    # Conversa Autônoma: minimum gap between two spontaneous lines, and whether Criador speaking cuts Célia's current line
    spontaneous_cooldown_seconds: int = Field(15, ge=0, le=600)
    barge_in: bool = True


class GameplayV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["follow", "nearby", "free"] = "follow"
    ideal_distance: int | None = Field(8, ge=2, le=128)
    max_distance: int | None = Field(24, ge=4, le=256)
    collection_radius: int = Field(12, ge=0, le=128)
    return_delay_seconds: int = Field(6, ge=0, le=120)
    spontaneous_collection: bool = False
    defensive_combat: bool = False
    allow_exploration: bool = False
    return_after_goal: bool = True
    pause_on_owner_call: bool = True
    resume_authorized_goal: bool = False
    spectator_camera: bool = False
    # Companion zones (conversation_autonomous): enforced by the Jev's CompanionController, never by the persona
    # None = "this profile does not use zones" (every profile except conversation_autonomous); validated only when they are set
    work_radius: int | None = Field(None, ge=4, le=24)
    exceptional_radius: int | None = Field(None, ge=4, le=24)
    lease_seconds: int | None = Field(None, ge=2, le=30)
    chase_limit: int | None = Field(None, ge=4, le=24)
    band_low: int | None = Field(None, ge=1, le=16)
    band_high: int | None = Field(None, ge=2, le=24)
    auto_follow_idle: bool = True
    stop_on_intro: bool = True

    @model_validator(mode="after")
    def validate_follow_limits(self):
        if self.mode != "free":
            if self.ideal_distance is None or self.max_distance is None:
                raise ValueError("follow mode requires finite distance limits")
            if self.max_distance < self.ideal_distance:
                raise ValueError("max_distance must be >= ideal_distance")
        if self.mode == "free" and self.spectator_camera is False:
            # Free exploration without a camera is valid.
            pass
        zones = (self.band_low, self.band_high, self.work_radius, self.exceptional_radius, self.chase_limit, self.lease_seconds)
        if any(value is not None for value in zones):
            ideal = self.ideal_distance if self.ideal_distance is not None else 5
            low, high = self.band_low if self.band_low is not None else 4, self.band_high if self.band_high is not None else 7
            work, exceptional = self.work_radius if self.work_radius is not None else 9, self.exceptional_radius if self.exceptional_radius is not None else 12
            chase = self.chase_limit if self.chase_limit is not None else 9
            if not (low <= ideal <= high <= work <= exceptional):
                raise ValueError("zones must satisfy band_low <= ideal <= band_high <= work_radius <= exceptional_radius")
            if chase > exceptional:
                raise ValueError("chase_limit must not exceed exceptional_radius")
        return self


class ProfileSettingsV6(BaseModel):
    model_config = ConfigDict(extra="forbid")
    social: SocialV6 = Field(default_factory=SocialV6)
    gameplay: GameplayV6 = Field(default_factory=GameplayV6)
    triggers: dict[str, TriggerV6] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_triggers(self):
        unknown = set(self.triggers) - set(TRIGGER_KEYS)
        if unknown:
            raise ValueError(f"unknown triggers: {', '.join(sorted(unknown))}")
        return self


def preset(profile: str) -> ProfileSettingsV6:
    if profile not in PROFILE_KEYS:
        raise ValueError("invalid_profile")
    voice = {
        "conversation_autonomous": (45, 35, 3, False, False),
        "chat": (22, 10, 30, False, False),
        "roteiro": (48, 22, 25, True, False),
        "gravacao": (56, 30, 20, True, False),
        "cohost": (60, 40, 18, False, True),
        "autonomous": (73, 55, 15, False, True),
    }[profile]
    ideal = {"conversation_autonomous": 5, "chat": 6, "roteiro": 8, "gravacao": 12, "cohost": 16, "autonomous": None}[profile]
    game = GameplayV6(
        mode="free" if profile == "autonomous" else "follow",
        ideal_distance=ideal,
        max_distance=None if ideal is None else (12 if profile == "conversation_autonomous" else ideal + 10),
        collection_radius={"conversation_autonomous": 9, "chat": 3, "roteiro": 6, "gravacao": 10, "cohost": 14, "autonomous": 32}[profile],
        spontaneous_collection=profile in {"gravacao", "cohost", "autonomous"},
        defensive_combat=profile in {"conversation_autonomous", "cohost", "autonomous"},
        allow_exploration=profile == "autonomous",
        spectator_camera=profile == "autonomous",
        **({"band_low": 4, "band_high": 7, "work_radius": 9, "exceptional_radius": 12, "lease_seconds": 8, "chase_limit": 9}
           if profile == "conversation_autonomous" else {}),
    )
    return ProfileSettingsV6(
        social=SocialV6(
            participation=voice[0], interruption=voice[1], silence_seconds=voice[2],
            script_tracking=voice[3], audience_chat=voice[4],
            screen_awareness=profile in {"gravacao", "cohost", "autonomous"},
        ),
        gameplay=game,
        triggers={k: TriggerV6(enabled=k != "audience" or voice[4]) for k in TRIGGER_KEYS},
    )


def all_presets() -> dict[str, ProfileSettingsV6]:
    return {name: preset(name) for name in PROFILE_KEYS}


def validated_settings(value: dict) -> ProfileSettingsV6:
    return ProfileSettingsV6.model_validate(deepcopy(value))
