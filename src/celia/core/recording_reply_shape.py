"""Reply shape for recording profiles (decision 4, 2026-10-02).

Measured on 2026-10-02 at 12:33:43: the action "Ah, calma, deixa eu ativar a
visão da Célia." reached the 70B with "TAMANHO: 15-50 palavras" and
"Inicios possiveis: Então,, Bom,, ..." and came back as
"Então, você acha que a visão da Célia vai melhorar isso aqui?".

In a recording profile an action or statement from Criador asks for a short
reaction instead of an invented topic; a question keeps the normal template.
Chat and system-origin turns are untouched.
See docs/planos/PLANO-CELIA-GRAVACAO-RESPOSTA-2026-10-02.md.
"""
from __future__ import annotations

import re
import unicodedata

RECORDING_PROFILES = {"gravacao", "roteiro"}

_SIZE_RE = re.compile(r"^TAMANHO: \d+-\d+ palavras$", re.MULTILINE)
_STARTERS_RE = re.compile(r"^Inicios possiveis:.*\n?", re.MULTILINE)
_SHORT = (
    "TAMANHO: 3-12 palavras (reacao curta: Criador fez uma acao ou afirmacao, "
    "nao uma pergunta; reaja ao que esta acontecendo, sem inventar assunto e "
    "sem devolver pergunta)"
)
_INTERROGATIVE_START = (
    "que ", "o que", "qual", "quais", "quem", "como", "onde", "cade", "quando",
    "por que", "porque ", "pra que", "sera que", "voce acha", "tu acha",
)


def _plain(text: str) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return " ".join(folded.lower().split())


# Tag questions at the end of a clause are for the audience, not for Célia.
# Measured in the 2026-10-02 recording: "E também deixar bonitinho, né?",
# "vamos lá por onde eu comecei, né gente?", "Esse site aqui, sabe?".
_TAG_QUESTION_RE = re.compile(
    r"[,\s](ne|sabe|ta|certo|entendeu|beleza|ok|nao e|ne gente|sabe gente|gente)\s*\?",
)


def is_question(text: str) -> bool:
    raw = str(text or "")
    plain = _plain(raw).lstrip(" ,.-")
    if plain.startswith(_INTERROGATIVE_START):
        return True
    without_tags = _TAG_QUESTION_RE.sub(" ", " " + plain)
    return "?" in without_tags


def shape_emotion_prompt(prompt: str, user_text: str, profile: str, origin: str) -> str:
    if profile not in RECORDING_PROFILES or str(origin or "").lower() == "system":
        return prompt
    if is_question(user_text):
        return prompt
    shaped = _SIZE_RE.sub(_SHORT, prompt, count=1)
    return _STARTERS_RE.sub("", shaped, count=1)
