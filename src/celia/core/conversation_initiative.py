"""When may Célia take the initiative in the Conversa Autônoma profile? (plan V2 §5)

Pure decision, no I/O: it says "an opportunity is OPEN" (three seconds of real shared silence by default) — never "speak now". Whether she
actually speaks is still decided by the existing arbiter and by what the persona has to say; "three seconds" is a chance, not an obligation.
The same gate is asked a second time right before audio would start, so a line prepared during a pause that Criador just ended is dropped.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InitiativePolicy:
    silence_seconds: float = 3.0
    cooldown_seconds: float = 15.0
    max_per_10m: int = 8
    max_per_hour: int = 40
    max_per_minute: int | None = None  # set: continuous mode, this REPLACES the 10-minute and hourly caps
    respect_concentration: bool = True


@dataclass(frozen=True)
class InitiativeFacts:
    silent_for: float
    since_last_spontaneous: float | None  # None: she has not taken the initiative in this session
    spontaneous_10m: int
    spontaneous_hour: int
    human_active: bool
    celia_busy: bool  # generating, synthesising or playing
    has_context: bool  # a fresh fact exists: recent speech, a Minecraft/Jev fact, the screen or the chat
    spontaneous_minute: int = 0
    concentrating: bool = False  # Criador is in a delicate moment (lava, bridge, combat, building)


def decide(policy: InitiativePolicy, facts: InitiativeFacts) -> tuple[bool, str]:
    """(open, reason). The reason is stable text used by logs and tests."""
    if facts.human_active:
        return False, "human_speaking"
    if facts.celia_busy:
        return False, "celia_busy"
    if facts.silent_for < policy.silence_seconds:
        return False, "silence_too_short"
    if not facts.has_context:
        return False, "no_fresh_context"
    if policy.respect_concentration and facts.concentrating:
        return False, "owner_concentrating"
    if policy.max_per_minute is not None:
        if policy.max_per_minute <= 0:
            return False, "initiative_disabled"
        if facts.spontaneous_minute >= policy.max_per_minute:
            return False, "budget_minute"
    else:
        if policy.max_per_10m <= 0 or policy.max_per_hour <= 0:
            return False, "initiative_disabled"
        if facts.spontaneous_10m >= policy.max_per_10m:
            return False, "budget_10m"
        if facts.spontaneous_hour >= policy.max_per_hour:
            return False, "budget_hour"
    if facts.since_last_spontaneous is not None and facts.since_last_spontaneous < policy.cooldown_seconds:
        return False, "cooldown"
    return True, "opportunity_open"


def still_valid_before_playback(policy: InitiativePolicy, facts: InitiativeFacts, *, generated_at_revision: int, current_revision: int) -> tuple[bool, str]:
    """The line was generated while the conversation had revision R. Before playing it, the world must still look like the one it was written for.

    ``facts`` must be built with the silence measured IGNORING Célia's own generation (``SharedSilenceClock.silent_for(ignore=("celia_generating",))``)
    and ``celia_busy`` meaning "another line is already playing". The line being checked is never a reason to refuse itself.
    """
    if generated_at_revision != current_revision:
        return False, "conversation_moved_on"
    if facts.human_active:
        return False, "human_started_speaking"
    open_now, reason = decide(policy, facts)
    return (True, "ok") if open_now else (False, reason)
