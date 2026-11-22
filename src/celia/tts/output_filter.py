"""Fail-closed speech blacklist for every Célia TTS engine."""
import json
from pathlib import Path
from loguru import logger
from ..utils.chat_filter import match_rules

RULES_PATH = Path(__file__).resolve().parents[3] / "integrations/tiktok_chat/bloqueio_saida.json"

def speech_blocked(text: str) -> bool:
    try:
        reason = match_rules(text, json.loads(RULES_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError, AttributeError):
        reason = "filter_unavailable"
    if reason:
        logger.warning("[saida-bloqueada] {} -> {}", reason, text)
        return True
    return False
