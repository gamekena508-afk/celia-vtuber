"""Keep unsolicited implementation labels out of public persona speech.

This is a local text boundary, not a model judge. Raw diagnostic events stay
in their journal. Explicit technical discussion is allowed per named component.
"""
from __future__ import annotations

import re
from loguru import logger

NARRATION_PREFIXES = ("[FICHA DO JEV", "[FICHA DO JOGO", "[FICHA DA SÉRIE")
_COMPONENTS = re.compile(r"\b(?:Jev|Mineflayer|jev_daemon|HOLD_SAFE|return_stalled)\b", re.I)
_LABELS = re.compile(r"FICHA DO (?:JEV|JOGO)|CONTEXTO DO SISTEMA", re.I)
FALLBACK = "Me expressei mal. Não tenho confirmação disso."


def is_game_narration(text: str) -> bool:
    return str(text or "").lstrip().startswith(NARRATION_PREFIXES)


def public_persona_text(text: str, user_text: str = "") -> str:
    """Validate a complete reply BEFORE memory, sentence splitting and TTS."""
    automatic = is_game_narration(user_text)
    discussed = set() if automatic else {m.group().casefold() for m in _COMPONENTS.finditer(user_text)}
    leaks = {m.group().casefold() for m in _COMPONENTS.finditer(text)} - discussed
    if leaks or (_LABELS.search(text) and (automatic or not _LABELS.search(user_text))):
        logger.warning("[Persona boundary] internal_label suppressed before speech")
        return FALLBACK
    return text


async def public_persona_stream(stream, user_text: str = ""):
    """Buffer final persona text so labels split across deltas cannot escape.

Tool routing/execution is NOT passed here. This wraps only the final persona
round; non-text protocol events are preserved for its existing parser.
"""
    parts = []
    async for event in stream:
        if isinstance(event, str):
            parts.append(event)
        elif isinstance(event, dict) and event.get("type") == "text_delta":
            parts.append(str(event.get("text") or ""))
        else:
            yield event
    if parts:
        yield public_persona_text("".join(parts), user_text)
