"""Deterministic voice/text intent for the Minecraft HUD: ONE interface, ONE visibility state.

"inventário", "mapa", "bússola", "HUD" and "interface Minecraft" are aliases of the whole HUD, never of a part.
Only an explicit imperative counts ("Célia, liga o mapa"); a mere mention or a generic request ("me mostra um mapa")
does not act. No language model is involved. This module controls presentation only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_SHOW = (
    "liga", "ligue", "ligar", "mostra", "mostre", "mostrar", "exibe", "exiba", "exibir",
    "ativa", "ative", "ativar", "acende", "acenda", "acender", "abre", "abra", "abrir",
)
_HIDE = (
    "desliga", "desligue", "desligar", "des liga", "esconde", "esconda", "esconder",
    "oculta", "oculte", "ocultar", "some com", "fecha", "feche", "fechar",
)
_NOUN = (
    r"(?:inventario|inventorio|mini ?mapa|mapa topografico|mapa|bussola|busola|bussula|bossola|"
    r"h\.? ?u\.? ?d|hud|aga u de|haud|raude|"
    r"(?:a |o )?interface (?:do |de |da )?(?:minecraft|mine craft|jogo)|"
    r"hud (?:do |de )?(?:minecraft|mine craft|jogo))"
)
_ARTICLE = r"(?:o |a |os |as )?"
_LIST = rf"{_ARTICLE}{_NOUN}(?:(?:,| e) {_ARTICLE}{_NOUN})*"
_TAIL = r"(?: (?:do|de|no|na|dentro do) (?:minecraft|mine craft|jogo))?(?: (?:agora|por favor|pra mim|para mim))*"
_PREFIX = (
    r"(?:(?:celia|selia|cela|sylia)[ ,!]+)?(?:por favor[ ,]+)?"
    r"(?:(?:pode|poderia|consegue)(?: me)? |quero que (?:voce |tu |ela )?)?"
)


@dataclass(frozen=True)
class HudIntent:
    action: str  # "show" | "hide" | "mention" | "none"
    matched_text: str = ""
    reason: str = ""

    @property
    def should_switch(self) -> bool:
        return self.action in {"show", "hide"}

    @property
    def enabled(self) -> bool | None:
        return {"show": True, "hide": False}.get(self.action)


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.casefold().replace("–", "-").replace("—", "-")
    value = re.sub(r"[!?.]+$", "", value.strip())
    value = re.sub(r"\s+", " ", value).strip()
    return value


def _verbs(words: tuple[str, ...]) -> str:
    return "|".join(re.escape(word) for word in sorted(words, key=len, reverse=True))


_SHOW_RE = re.compile(rf"{_PREFIX}(?P<verb>{_verbs(_SHOW)}) {_LIST}{_TAIL}")
_HIDE_RE = re.compile(rf"{_PREFIX}(?P<verb>{_verbs(_HIDE)}) {_LIST}{_TAIL}")
_NEGATED_RE = re.compile(r"\b(?:nao|nunca|nem)\b")
_ANY_NOUN = re.compile(rf"(?<!\w){_NOUN}(?!\w)")


def detect_hud_intent(text: str) -> HudIntent:
    folded = _fold(text)
    if not folded:
        return HudIntent("none", reason="empty")
    # hide first: "desliga" must never be read as "liga"
    for action, pattern in (("hide", _HIDE_RE), ("show", _SHOW_RE)):
        match = pattern.fullmatch(folded)
        if match and not _NEGATED_RE.search(folded):
            return HudIntent(action, match.group(0), "explicit_imperative")
    if _ANY_NOUN.search(folded):
        return HudIntent("mention", reason="hud_noun_without_command")
    return HudIntent("none", reason="no_hud_word")
