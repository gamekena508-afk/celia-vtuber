"""Strong voice-stop intent detection for recording/roteiro mode.

Roteiro must not yield merely because the microphone heard speech. Voice
interruption is reserved for short, explicit owner commands that address Célia
(or a known Whisper phonetic rendering of her name) and contain a stop action.
"""

from __future__ import annotations

import re
import unicodedata

_WORD_RE = re.compile(r"[a-z0-9]+")

_CELIA_ALIASES = frozenset({
    "celia", "selia", "seria", "serie", "series", "cena",
    "selha", "celio", "selio", "celya", "selya",
})

_STOP_SINGLE = frozenset({
    "para", "pare", "parou", "calma", "espera", "espere", "segura",
    "segure", "corta", "corte", "silencio", "chega", "pausa", "quieta",
    "volta", "parei",
})
_STOP_PHRASES = (
    ("cala", "a", "boca"),
    ("fica", "quieta"),
    ("para", "de", "falar"),
    ("pare", "de", "falar"),
    ("pode", "parar"),
)

_FILLERS = frozenset({"ai", "agora", "por", "favor", "um", "pouco"})


def _tokens(text: str) -> list[str]:
    decomposed = unicodedata.normalize("NFKD", text or "")
    normalized = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _WORD_RE.findall(normalized.casefold())


def is_explicit_roteiro_stop(text: str) -> bool:
    """Return True only for a short, explicit voice command to stop Célia."""
    tokens = _tokens(text)
    if not tokens or len(tokens) > 9:
        return False

    alias_positions = [
        i for i, token in enumerate(tokens)
        if token == "celia" or (token in _CELIA_ALIASES and i in {0, len(tokens) - 1})
    ]
    if not alias_positions:
        return False

    # Match the entire utterance, not an action embedded in ordinary speech.
    # In particular, "Celia, para mim ..." is not a stop command.
    for alias_pos in alias_positions:
        command = tuple(token for i, token in enumerate(tokens)
                        if i != alias_pos and token not in _FILLERS)
        if command in _STOP_PHRASES or (len(command) == 1 and command[0] in _STOP_SINGLE):
            return True
    return False
