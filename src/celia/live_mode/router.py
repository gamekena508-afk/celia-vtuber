"""Quem responde cada mensagem da live.

Precedência (documentada em docs/LIVE-MODE.md):
  1. Direcionamento EXPLÍCITO a um personagem (vocativo/@/começo da frase) vence tudo,
     inclusive a posição no ranking.
  2. Sem direcionamento: autor Top1 -> slime, autor Top2 -> ursinho
     (se o ranking de personagens e o personagem estiverem ligados).
  3. Caso contrário: Célia.
Menção sem vocativo ("o slime da Célia é fofo") NÃO é direcionamento.

Nota de manutenção: as regexes usam lookarounds (_INI/_FIM) em vez de barra-b,
porque a sequência barra-b digitada em ferramentas vira um caractere de controle.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

_ALIASES = {
    "slime": ("slime", "slimezinho", "slimezinha", "tamaneko", "gosma"),
    "ursinho": ("ursinho", "ursinha", "urso", "ursao", "pelucia", "bear"),
    "celia": ("celia", "cecelia", "celinha"),
}
_INI = "(?<![a-z0-9_])"
_FIM = "(?![a-z0-9_])"
_SAUDACAO_ITENS = "oi|oii+|ola|e ?ai|eai|ei|ey|hey|oh|fala|salve|opa"
_GREETING = "(?:(?:" + _SAUDACAO_ITENS + "|bom dia|boa tarde|boa noite)[ ]+)*"
# marcadores de 2ª pessoa/pedido: "ursinho você gosta de mel?" é conversa COM ele; "ursinho gente fina" não é.
_SECOND_PERSON = re.compile(
    _INI + "(?:voce|vc|tu|te|ti|teu|tua|seu|sua|me|mim|pode|podia|consegue|quer|gosta|fala|diz|canta|faz|mostra|conta|responde)"
    + _FIM + "|[?]")


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", text).strip()


def _alt(names: tuple[str, ...]) -> str:
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


_PATTERNS: dict[str, list[re.Pattern]] = {}
_START: dict[str, re.Pattern] = {}
for _key, _names in _ALIASES.items():
    _a = "(?:" + _alt(_names) + ")"
    _PATTERNS[_key] = [
        re.compile("^@?[ ]*" + _GREETING + "@?" + _a + "[ ]*(?:[,:;!?-]|$)"),                       # "slime, ..." / "oi ursinho!"
        re.compile("^@?[ ]*(?:(?:" + _SAUDACAO_ITENS + ")[ ]+)+@?" + _a + _FIM),                    # "oi ursinho você..."
        re.compile("(?:^|[ ,;:.!?])@" + _a + _FIM),                                                   # "@slime" em qualquer ponto
        re.compile("[,;:][ ]*" + _a + "[ ]*[?!.]*$"),                                                 # "... tudo bem, slime?"
        re.compile(_INI + _a + "[ ]*[?!]+$"),                                                         # "... ursinho?" no fim
    ]
    _START[_key] = re.compile("^@?[ ]*" + _a + _FIM)                                                  # nome no começo


@dataclass(frozen=True)
class RouteDecision:
    character: str
    reason: str          # explicit | rank | default | rank_disabled
    rank: int | None = None


def detect_explicit(text: str) -> str | None:
    """Personagem endereçado de forma explícita, ou None. Em empate vence o que aparece primeiro."""
    # "Ô slime" (vocativo) != "o slime" (artigo): decidir antes de tirar o acento
    text = re.sub("^[ ]*[ôÔ][ ]+", "oh ", text or "")
    folded = _fold(text)
    best: tuple[int, str] | None = None
    for key, patterns in _PATTERNS.items():
        for pattern in patterns:
            match = pattern.search(folded)
            if match and (best is None or match.start() < best[0]):
                best = (match.start(), key)
        # nome no começo, sem pontuação nem saudação ("ursinho você gosta de mel?"): só vale com 2ª pessoa
        loose = _START[key].match(folded)
        if loose and _SECOND_PERSON.search(folded[loose.end():]) and (best is None or loose.start() < best[0]):
            best = (loose.start(), key)
    return best[1] if best else None


def route(text: str, user_id: str, top_user_ids: list[str], *, ranking_enabled: bool = True,
          enabled: dict[str, bool] | None = None) -> RouteDecision:
    enabled = enabled or {}
    explicit = detect_explicit(text)
    if explicit and enabled.get(explicit, True):
        return RouteDecision(explicit, "explicit")
    if ranking_enabled and user_id:
        for index, character in ((0, "slime"),):
            if index < len(top_user_ids) and top_user_ids[index] == user_id and enabled.get(character, True):
                return RouteDecision(character, "rank", index + 1)
    return RouteDecision("celia", "default" if ranking_enabled else "rank_disabled")
