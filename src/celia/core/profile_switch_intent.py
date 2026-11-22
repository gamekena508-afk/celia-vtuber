"""Deterministic intent gate for natural-language operational profile switches."""

from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

# The compound name comes FIRST: "ativa a conversa autônoma" must never be read as "conversa" (chat) or as "autônoma".
PROFILE_ALIASES: dict[str, tuple[str, ...]] = {
    "conversation_autonomous": (
        "conversa autonoma", "conversa autônoma", "modo conversa autonoma", "modo conversa autônoma",
        "perfil conversa autonoma", "perfil conversa autônoma", "conversa autonomo", "conversa autônomo",
    ),
    "chat": ("chat", "conversa", "modo chat", "modo conversa", "perfil chat", "perfil conversa"),
    "roteiro": ("roteiro", "modo roteiro", "perfil roteiro"),
    "gravacao": ("gravacao", "gravação", "modo gravacao", "modo gravação", "perfil gravacao", "perfil gravação"),
    "cohost": ("cohost", "co host", "co-host", "modo cohost", "modo co host", "modo co-host"),
    "autonomous": (
        "autonoma", "autonomo", "autônoma", "autônomo",
        "modo autonoma", "modo autonomo", "modo autônoma", "modo autônomo",
    ),
}

_EXPLICIT_PREFIXES = (
    "ativa", "ative", "ativar", "liga", "ligue", "ligar",
    "entra", "entre", "entrar", "muda", "mude", "mudar",
    "troca", "troque", "trocar", "coloca", "coloque", "colocar",
    "bota", "bote", "botar", "poe", "vai",
    "volta", "volte", "voltar",
)

@dataclass(frozen=True)
class ProfileSwitchIntent:
    action: str
    target: str | None = None
    matched_text: str = ""
    reason: str = ""

    @property
    def should_switch(self) -> bool:
        return self.action == "switch" and bool(self.target)


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("–", "-").replace("—", "-")
    value = re.sub(r"\s+", " ", value).strip()
    return value


_FOLDED_ALIASES = {
    target: tuple(sorted({_fold(alias) for alias in aliases}, key=len, reverse=True))
    for target, aliases in PROFILE_ALIASES.items()
}

def _target_at_end(text: str) -> tuple[str | None, str]:
    for target, aliases in _FOLDED_ALIASES.items():
        for alias in aliases:
            match = re.search(rf"(?:^|\s)(?P<mode>{re.escape(alias)})(?:\s+agora)?[.!?]*$", text)
            if match:
                return target, match.group("mode")
    return None, ""


def _negated(prefix: str) -> bool:
    tail = prefix[-40:]
    return bool(re.search(
        r"\b(?:nao|nunca|nem)\s+(?:quero\s+)?(?:que\s+)?"
        r"(?:ativa|ative|ativar|liga|ligue|ligar|entra|entre|entrar|"
        r"muda|mude|mudar|troca|troque|trocar|coloca|coloque|colocar|"
        r"bota|bote|botar|poe|vai|volta|volte|voltar)\b",
        tail,
    ))


def detect_profile_switch_intent(text: str) -> ProfileSwitchIntent:
    normalized = _fold(text)
    if not normalized:
        return ProfileSwitchIntent("none", reason="empty")

    # A direct vocative followed by an exact mode is an owner's explicit command.
    # Standalone "modo autonoma" remains a mention, not an action.
    direct = re.fullmatch(
        r"(?:celia|selia|cela)[,! ]+"
        r"(?:modo|perfil)\s+(conversa autonoma|conversa autonomo|chat|conversa|roteiro|gravacao|cohost|co-host|co host|autonoma|autonomo)[.!?]*",
        normalized,
    )
    if direct:
        aliases = {
            "conversa autonoma": "conversation_autonomous", "conversa autonomo": "conversation_autonomous",
            "conversa": "chat", "gravacao": "gravacao", "co-host": "cohost",
            "co host": "cohost", "autonoma": "autonomous", "autonomo": "autonomous",
        }
        target = aliases.get(direct.group(1), direct.group(1))
        return ProfileSwitchIntent("switch", target, direct.group(0), "owner_vocative_mode")

    end_target, end_match = _target_at_end(normalized)
    if end_target and normalized in {end_match, f"{end_match} agora"}:
        return ProfileSwitchIntent("mention", end_target, end_match, "bare_profile_name")

    mentioned_target: str | None = None
    mentioned_text = ""
    for candidate, aliases in _FOLDED_ALIASES.items():
        for alias in aliases:
            if candidate == "chat" and alias == "conversa":
                continue
            if re.search(rf"(?<!\w){re.escape(alias)}(?!\w)", normalized):
                mentioned_target, mentioned_text = candidate, alias
                break
        if mentioned_target:
            break

    target_patterns: list[tuple[str, str]] = []

    for target, aliases in _FOLDED_ALIASES.items():
        alias_group = "|".join(re.escape(alias) for alias in aliases)
        target_patterns.append((target, alias_group))

    verb_group = "|".join(_EXPLICIT_PREFIXES)
    for target, alias_group in target_patterns:
        patterns = (
            rf"\b(?P<verb>{verb_group})\s+(?:(?:para|pro|pra|no|na|ao|a)\s+)?"
            rf"(?:(?:o|a)\s+)?(?P<mode>{alias_group})\b",
            rf"\bquero\s+(?:usar|ativar|ligar|entrar\s+(?:no|na)|mudar\s+(?:para|pro|pra)|"
            rf"trocar\s+(?:para|pro|pra)|voltar\s+(?:para|pro|pra))\s+"
            rf"(?:(?:o|a)\s+)?(?P<mode>{alias_group})\b",
            rf"\b(?:vamos|bora)\s+(?:para|pro|pra)\s+(?:(?:o|a)\s+)?"
            rf"(?P<mode>{alias_group})\b",
            rf"\b(?:muda|mude|troca|troque)\s+(?:de\s+)?(?:o\s+)?(?:modo|perfil)\s+"
            rf"(?:para|pro|pra)\s+(?P<mode>{alias_group})\b",
            rf"\b(?:coloca|coloque|bota|bote|poe)\s+(?:(?:a\s+)?celia|ela)\s+"
            rf"(?:no|na)\s+(?:(?:o|a)\s+)?(?P<mode>{alias_group})\b",
            rf"\bquero\s+que\s+(?:(?:a\s+)?celia|voce|tu|ela)\s+"
            rf"(?:entre|mude|troque|volte)\s+(?:(?:para|pro|pra|no|na)\s+)?"
            rf"(?:(?:o|a)\s+)?(?P<mode>{alias_group})\b",
        )
        for pattern in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            before = normalized[: match.start()]
            negative_prefix = re.search(
                r"\b(?:nao|nunca|nem)\s+(?:quero\s+)?(?:que\s+)?"
                r"(?:(?:voce|tu|ela|celia|a celia)\s+)?$",
                before,
            )
            if negative_prefix or _negated(normalized[: match.end()]):
                return ProfileSwitchIntent("mention", target, match.group(0), "negated_switch")
            return ProfileSwitchIntent("switch", target, match.group(0), "explicit_switch")

    if mentioned_target:
        return ProfileSwitchIntent(
            "mention", mentioned_target, mentioned_text, "profile_mentioned_without_switch"
        )
    return ProfileSwitchIntent("none", reason="no_profile_switch")

async def apply_profile_switch(target: str, *, singleton) -> dict:
    """Apply one already-authorized switch and publish the canonical event."""
    from .continuous_behavior import PROFILE_NAMES, canonical_profile, continuous_behavior

    resolved = canonical_profile(target)
    if resolved not in PROFILE_NAMES:
        raise ValueError("invalid_profile")

    if resolved == "roteiro" and continuous_behavior.profile != "roteiro":
        from .roteiro_live import roteiro_live
        roteiro_live.prepare_operational_script(continuous_behavior)

    previous = continuous_behavior.profile
    snapshot = continuous_behavior.select_profile(resolved)
    changed = previous != snapshot.get("profile")
    change = snapshot.get("profile_change") if changed else None
    if change:
        try:
            await singleton.broadcast({
                "type": "profile.changed",
                "profile": snapshot["profile"],
                "change": change,
            })
        except Exception:
            pass

    active_speech = snapshot.get("active_speech") or {}
    if changed and active_speech.get("playback_state") == "cancel_requested":
        try:
            await singleton.broadcast({
                "type": "playback.cancel",
                "speech_id": active_speech.get("speech_id"),
                "reason": "profile_changed",
            })
        except Exception:
            pass
    return snapshot
