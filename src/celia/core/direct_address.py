"""Off-script address to Célia during a recording (decision 3, 2026-10-02).

Owner's rule, written in docs/planos/PLANO-CELIA-GRAVACAO-RESPOSTA-2026-10-02.md:
in gravacao, a line that is NOT in the script and is a question, or carries
"tá vendo / viu / olha / que isso", is sent straight to Célia with the last
screen-vision analysis. Lines that follow the script never fire, because 13 of
the 64 scripted lines talk to the audience with "você / tá vendo / olha".

Measured on 2026-10-02: "Tá vendo o site?" was routed ROUTING_CONTEXT_ONLY and
never reached her. The keyboard gesture from the same decision is pending an
owner choice (Ctrl+S already saves the Pet pose in frontend/celia-pet.js).
"""
from __future__ import annotations

import re
import time
import unicodedata
from typing import Any, Iterable

from .recording_reply_shape import is_question

ADDRESS_PROFILES = {"gravacao"}
_TRIGGER_RE = re.compile(r"\b(ta vendo|viu|olha|que isso)\b")
_WORD_RE = re.compile(r"[a-z0-9]+")
_STOP = {
    "a", "o", "e", "de", "da", "do", "que", "eu", "um", "uma", "pra", "pro", "na", "no",
    "em", "se", "ta", "ne", "isso", "aqui", "esse", "essa", "mas", "com", "nao", "me",
    "te", "ja", "so", "ai", "la", "os", "as", "ele", "ela", "voce", "tu", "foi", "ser",
}
SCRIPTED_OVERLAP = 0.5
VISION_MAX_AGE_SEC = 15.0


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(folded.lower().split())


def _tokens(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(_plain(text)) if len(w) > 2 and w not in _STOP}


def criador_script_lines(beats: Iterable[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for beat in beats or []:
        for line in (beat or {}).get("lines") or []:
            if isinstance(line, dict) and _plain(line.get("speaker")) == "criador" and line.get("text"):
                lines.append(str(line["text"]))
    return lines


def follows_script(text: str, script_lines: Iterable[str]) -> bool:
    words = _tokens(text)
    if not words:
        return True  # nothing to judge: treat as scripted, never fire
    for line in script_lines:
        shared = words & _tokens(line)
        if len(shared) >= min(2, len(words)) and len(shared) / len(words) >= SCRIPTED_OVERLAP:
            return True
    return False


def should_address_celia(text: str, profile: str, script_lines: Iterable[str]) -> tuple[bool, str]:
    if profile not in ADDRESS_PROFILES:
        return False, "profile"
    plain = _plain(text)
    question = is_question(text)
    trigger = bool(_TRIGGER_RE.search(plain))
    if not (question or trigger):
        return False, "no_question_or_trigger"
    if follows_script(text, list(script_lines)):
        return False, "follows_script"
    return True, "off_script_question" if question else "off_script_trigger"


def vision_note(last_result: dict[str, Any] | None, age_sec: float | None) -> str:
    """Honest screen note: the cached analysis, or an explicit 'none'."""
    summary = str((last_result or {}).get("summary") or "").strip()
    if summary and age_sec is not None and age_sec <= VISION_MAX_AGE_SEC:
        return f"[VISAO - ultima analise da tela, ha {int(age_sec)}s: {summary[:320]}]"
    return "[VISAO - sem analise recente da tela; nao descreva o que nao viu]"


# ── Meta+N gesture (owner, 2026-10-02; redefined 2026-10-04) ──────────────
# A KDE custom command shortcut (bin/celia-olha-isso, installed by
# bin/instalar-atalho-meta-n.sh) posts to /bridge/direct-address.
#
# Since 2026-10-04 the gesture means ONLY "Célia, olha isso": an explicit visual
# attention trigger. It does NOT depend on a pending line and does NOT mean
# "answer the last thing you ignored". The route attaches the cached visual
# observation with its REAL age, never starts a new analysis by itself (stale cache
# -> "ask": Célia decides whether to request a new look) and lets Célia judge
# whether it relates to the conversation.
GESTURE_MARKER = "[GESTO — o dono apertou Meta+N]"
GESTURE_LOOK_PROMPT = (
    f"{GESTURE_MARKER}\n"
    "Célia, olha isso agora e veja se isso tem alguma relação com o que está "
    "acontecendo na conversa. Se não tiver relação, não precisa falar; se tiver, "
    "responde normalmente."
)


def is_look_gesture(text: str) -> bool:
    """True for a Meta+N turn. Such a turn NEVER enables tools.

    The prompt and the visual context contain words ("tela", "agora", "análise")
    that the keyword triggers (needs_tool, _is_visual_request) read as a request for
    the live vision tool. The gesture must not cause a GPU cascade by itself, so
    the router and the tool filter check this marker first (owner, 2026-10-04).
    """
    return GESTURE_MARKER in str(text or "")


def select_for_gesture(max_age_ms: int | None = None):
    """Cache selection for Meta+N: policy is forced to 'ask' (owner, 2026-10-04).

    The text passed to the selector is empty on purpose: the gesture prompt contains
    the word "agora", which the selector reads as "wants a fresh look".
    """
    from .vision_cache import select_vision_for_request

    return select_vision_for_request("", policy="ask", max_age_ms=max_age_ms)


def build_look_request(selection, max_age_ms: int | None = None) -> str:
    """The text Célia receives: the gesture prompt + the visual evidence with honest age."""
    from .vision_cache import format_vision_context

    return f"{GESTURE_LOOK_PROMPT}\n\n{format_vision_context(selection, max_age_ms)}"


# ── Pending unanswered line (kept for other purposes; NOT the Meta+N meaning) ──
# The last line Célia chose not to answer (ROUTING_CONTEXT_ONLY) is remembered at
# most UNANSWERED_MAX_AGE_SEC, once.
UNANSWERED_MAX_AGE_SEC = 30.0
_unanswered: dict[str, Any] = {}


def remember_unanswered(text: str, utterance_id: str = "") -> None:
    if str(text or "").strip():
        _unanswered.clear()
        _unanswered.update(text=str(text).strip(), utterance_id=str(utterance_id or ""), at=time.monotonic())


def forget_unanswered() -> None:
    _unanswered.clear()


def take_unanswered() -> dict[str, Any] | None:
    if not _unanswered:
        return None
    item = dict(_unanswered)
    _unanswered.clear()
    if time.monotonic() - float(item.get("at") or 0.0) > UNANSWERED_MAX_AGE_SEC:
        return None
    return item
