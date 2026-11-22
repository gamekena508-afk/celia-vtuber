"""Speech triggers for vision, SHADOW MODE ONLY (owner, 2026-10-04).

The owner's rule: vision is not consulted on every utterance. It is a perception
available to Célia, used when there is a reason. Three reasons: Meta+N (explicit
gesture), a semantic trigger in speech ("olha aí", "e aí, Célia?", "o que tu acha?",
"fala sobre isso") and an internal decision. This module is the second one, and for
now it only RECORDS "this line would have triggered vision". It never calls vision,
never touches the GPU and never changes the reply. The false positives are reviewed
later from var/vision_trigger_shadow.jsonl before anything becomes active.

A generic "olha" alone is NOT a trigger: it is too common in speech ("olha, eu acho
que...") and would send an expensive analysis without context.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# (category, pattern). Patterns run on accent-folded lowercase text.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "olhar",
        re.compile(
            r"\b(olha (ai|isso|so|aqui|aquilo)|da (uma|so uma) olhada|"
            r"(veja|ve|repara|confere|checa) (isso|ai|aqui))\b"
        ),
    ),
    (
        "opiniao",
        re.compile(
            r"\b(o que (tu|voce|vc|ce) (acha|achou|acha disso)|"
            r"e ai,? celia|e entao,? celia|celia,? o que (tu|voce|vc|ce) acha)\b"
        ),
    ),
    (
        "comentar",
        re.compile(
            r"\b(fala (sobre )?(isso|disso|ai)|comenta (isso|ai|sobre isso)|"
            r"fala alguma coisa (sobre|disso)|diz ai (sobre|o que))\b"
        ),
    ),
)


@dataclass(frozen=True)
class TriggerMatch:
    category: str
    phrase: str


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(folded.lower().replace("?", " ").replace("!", " ").split())


def detect_vision_trigger(text: str) -> TriggerMatch | None:
    """Deterministic, pure and cheap. Returns the first matching trigger or None."""
    plain = _plain(text)
    if not plain:
        return None
    for category, pattern in _RULES:
        found = pattern.search(plain)
        if found:
            return TriggerMatch(category=category, phrase=found.group(0))
    return None


def shadow_enabled() -> bool:
    return os.getenv("CELIA_VISION_TRIGGER_SHADOW", "1").strip().lower() not in {"0", "false", "no"}


_lock = threading.Lock()
_counters = {"seen": 0, "would_trigger": 0}


def _shadow_path() -> Path:
    override = os.getenv("CELIA_VISION_TRIGGER_SHADOW_FILE", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "var" / "vision_trigger_shadow.jsonl"


def observe(text: str, *, origin: str = "owner", turn_id: str = "") -> TriggerMatch | None:
    """Shadow observation: log the decision, call nothing.

    Meta+N turns are skipped (they already are the explicit trigger).
    The record also says what the vision cache would have offered, so the review
    can tell "trigger + fresh cache" from "trigger + stale cache".
    """
    if not shadow_enabled():
        return None
    from .direct_address import is_look_gesture

    if is_look_gesture(text):
        return None
    match = detect_vision_trigger(text)
    with _lock:
        _counters["seen"] += 1
        if match is not None:
            _counters["would_trigger"] += 1
    if match is None:
        return None
    cache: dict[str, Any] = {}
    try:
        from .vision_cache import select_vision_for_request

        # Empty text: the shadow must not let the "agora" rule distort what it reports.
        selection = select_vision_for_request("", policy="ask")
        cache = {"action": selection.action, "analysis_age_ms": selection.age_ms}
    except Exception as exc:  # shadow never breaks a turn
        cache = {"error": type(exc).__name__}
    record = {
        "ts": time.time(),
        "turn_id": str(turn_id or ""),
        "origin": str(origin or ""),
        "category": match.category,
        "phrase": match.phrase,
        "text": str(text or "")[:240],
        "would_trigger_vision": True,
        "vision_called": False,
        "cache": cache,
    }
    logger.info(
        "[Gatilho visão SOMBRA] esta fala teria acionado visão: "
        f"{match.category}/{match.phrase!r} cache={cache.get('action')} (nada foi chamado)"
    )
    try:
        path = _shadow_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.debug(f"vision trigger shadow not written: {type(exc).__name__}")
    return match


def stats() -> dict[str, int]:
    with _lock:
        return dict(_counters)
