"""Typed, conservative profile policy for the in-process behavior runtime."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .i18n import I18nMixin


PROFILE_NAMES = frozenset({"conversation_autonomous", "chat", "roteiro", "gravacao", "cohost", "autonomous"})


def canonical_profile(profile: str) -> str:
    # Keep one stable internal identifier per operational mode.
    return str(profile or "").strip().lower()


class BehaviorProfileConfig(I18nMixin):
    """Cost and social policy only; this model cannot grant system authority."""

    prediction_level: Literal["off", "light", "strong"] = "light"
    proactive_speech: Literal["off", "light", "medium", "strong"] = "light"
    social_arbitration: Literal["light", "medium", "strong"] = "light"
    script_tracking: bool = False
    audience_chat: bool = False
    screen_awareness: Literal["off", "optional", "enabled"] = "optional"
    speaker_awareness: Literal["off", "direct", "medium", "high"] = "direct"
    interruption_management: Literal["off", "basic", "medium", "high"] = "basic"
    external_ingress: Literal["minimal", "enabled"] = "minimal"
    latency_priority: Literal["maximum", "high", "balanced"] = "maximum"
    speaking_threshold: float = Field(0.78, ge=0.0, le=1.0)


def _default_profiles() -> dict[str, BehaviorProfileConfig]:
    return {
        # Same low-latency conversation as "chat" (light prediction, maximum latency priority) plus a bounded spontaneous layer.
        "conversation_autonomous": BehaviorProfileConfig(
            prediction_level="light", proactive_speech="medium", social_arbitration="medium",
            screen_awareness="optional", speaker_awareness="medium", interruption_management="medium",
            external_ingress="enabled", latency_priority="maximum", speaking_threshold=0.66,
        ),
        "chat": BehaviorProfileConfig(),
        "roteiro": BehaviorProfileConfig(
            prediction_level="strong", proactive_speech="medium",
            social_arbitration="medium", script_tracking=True,
            speaker_awareness="direct", interruption_management="medium",
            latency_priority="high", external_ingress="enabled",
            speaking_threshold=0.66,
        ),
        "gravacao": BehaviorProfileConfig(
            prediction_level="strong", proactive_speech="strong",
            social_arbitration="strong", script_tracking=True, audience_chat=False,
            screen_awareness="enabled", speaker_awareness="high",
            interruption_management="high", external_ingress="enabled",
            latency_priority="high", speaking_threshold=0.62,
        ),
        "cohost": BehaviorProfileConfig(
            prediction_level="strong", proactive_speech="strong",
            social_arbitration="strong", script_tracking=False, audience_chat=True,
            screen_awareness="enabled", speaker_awareness="high",
            interruption_management="high", external_ingress="enabled",
            latency_priority="high", speaking_threshold=0.62,
        ),
        "autonomous": BehaviorProfileConfig(
            prediction_level="strong", proactive_speech="strong",
            social_arbitration="strong", script_tracking=False, audience_chat=True,
            screen_awareness="enabled", speaker_awareness="high",
            interruption_management="high", external_ingress="enabled",
            latency_priority="high", speaking_threshold=0.54,
        ),
    }


class ContinuousBehaviorConfig(I18nMixin):
    """Declarative behavior tuning loaded from ``character_config``."""

    profiles: dict[str, BehaviorProfileConfig] = Field(default_factory=_default_profiles)

    @model_validator(mode="after")
    def validate_profiles(self):
        unknown = set(self.profiles) - PROFILE_NAMES
        if unknown:
            raise ValueError(f"continuous_behavior profiles invalid: {', '.join(sorted(unknown))}")
        # A configuration written before a profile existed must keep working: missing profiles fall back to their defaults.
        for name, default in _default_profiles().items():
            self.profiles.setdefault(name, default)
        for name, profile in self.profiles.items():
            profile.script_tracking = name in {"roteiro", "gravacao"}
        self.profiles["roteiro"].external_ingress = "enabled"
        self.profiles["gravacao"].external_ingress = "enabled"
        return self
