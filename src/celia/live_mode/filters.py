"""Filtros de entrada e saída do modo Live.

Reutiliza as regras do leitor antigo (integrations/tiktok_chat/bloqueio*.json) e falha FECHADO:
se o arquivo de regras não puder ser lido, a mensagem é retida.
Também barra respostas dos personagens que peçam presente/seguir/curtir (regra do dono, 25/10/2026).
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from celia.core.raizes import RAIZ
from celia.utils.chat_filter import match_rules

REGRAS_DIR = RAIZ / "integrations" / "tiktok_chat"
_INI = "(?<![a-z0-9_])"
_FIM = "(?![a-z0-9_])"

_PALAVRAS_PEDIDO = (
    "presente", "presentes", "rosa", "rosas", "gift", "gifts", "moeda", "moedas", "doacao", "doar", "doe",
    "doem", "doa", "diamante", "diamantes", "leao", "universo", "perfume", "tiktok coins",
)
_VERBOS_PEDIDO = (
    "manda", "mande", "mandem", "envia", "envie", "enviem", "joga", "jogue", "joguem",
    "solta", "solte", "ganha", "compra", "compre", "quero", "queria", "preciso", "troca", "em troca",
    "doa", "doe", "doem", "traz", "traga", "bota", "ranking", "top 1", "top1", "primeiro lugar",
)
_ACAO_SOCIAL = re.compile(
    _INI + "(?:segue|seguir|segue-me|sigam|siga|segui|curte|curtir|curtam|curta|compartilha|compartilhe|compartilhem|"
    "inscreva|inscreve|se inscreve|deixa o like|deixe o like|like na live|ativa o sino)" + _FIM)


_DA_ALGO = re.compile(_INI + "(?:da|de|dem) (?:uma?|umas|uns|seu|teu|sua|o|a|os|as)? ?(?:" + "|".join(_PALAVRAS_PEDIDO) + ")" + _FIM)


def _fold(text: str) -> str:
    t = unicodedata.normalize("NFKD", text or "")
    return re.sub(r"\s+", " ", "".join(c for c in t if not unicodedata.combining(c)).lower()).strip()


def pede_presente_ou_acao(text: str) -> str | None:
    """Motivo se a resposta pede/sugere presente, seguir, curtir ou ranking; senão None."""
    t = _fold(text)
    if _ACAO_SOCIAL.search(t):
        return "acao_social"
    palavras = [p for p in _PALAVRAS_PEDIDO if re.search(_INI + re.escape(p) + _FIM, t)]
    if palavras and _DA_ALGO.search(t):
        return "pedido_de_presente:" + palavras[0]
    if palavras:
        verbos = [v for v in _VERBOS_PEDIDO if re.search(_INI + re.escape(v) + _FIM, t)]
        if verbos or "?" in text:
            return "pedido_de_presente:" + palavras[0]
    if re.search(_INI + "(?:ranking|top ?[12]|doador|doadores)" + _FIM, t) and re.search(_INI + "(?:ser|virar|entrar|subir|chegar)" + _FIM, t):
        return "ranking_como_recompensa"
    return None


def _carregar(nome: str) -> dict:
    with open(Path(REGRAS_DIR) / nome, encoding="utf-8") as fonte:
        dados = json.load(fonte)
    if not isinstance(dados, dict):
        raise ValueError("regras invalidas")
    return dados


def entrada_bloqueada(autor: str, texto: str) -> str | None:
    """Motivo do bloqueio da mensagem de entrada, ou None. Falha fechada."""
    try:
        return match_rules((autor or "") + " " + (texto or ""), _carregar("bloqueio.json"))
    except (OSError, ValueError, TypeError, AttributeError):
        return "filtro_indisponivel"


def saida_bloqueada(texto: str) -> str | None:
    try:
        motivo = match_rules(texto or "", _carregar("bloqueio_saida.json"))
    except (OSError, ValueError, TypeError, AttributeError):
        return "filtro_indisponivel"
    return motivo or pede_presente_ou_acao(texto)


def limpar_resposta(texto: str, max_chars: int = 220) -> str:
    """Tira markdown, emoji e ações entre asteriscos; corta no fim de frase."""
    t = texto or ""
    t = re.sub(r"[*][^*]{0,80}[*]", " ", t)  # *sorri*: direção de cena não é fala
    t = re.sub(r"[*_`#>~]+", "", t)
    t = re.sub(r"\([^)]{0,80}\)", " ", t)  # (rindo), (suspira)
    t = "".join(c for c in t if unicodedata.category(c) not in ("So", "Sk", "Cs") and ord(c) < 0x1F000)
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) <= max_chars:
        return t
    corte = t[:max_chars]
    fim = max(corte.rfind("."), corte.rfind("!"), corte.rfind("?"))
    return (corte[: fim + 1] if fim >= max_chars // 3 else corte.rsplit(" ", 1)[0]).strip()
