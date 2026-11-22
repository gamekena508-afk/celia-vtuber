"""Pré-filtro de ferramenta por turno de voz: só RESTRINGE, nunca concede.

Medido na tentativa de 2026-10-21 (14:21-14:25): fragmentos soltos de ASR dispararam ferramenta com efeito real:
  * ``minecraft_parar`` às 14:22:56, a partir de "Zera Minecraft." (a fala declarava o objetivo; o corpo parou);
  * ``minecraft_objetivo("fazer uma série de Minecraft")`` a partir de "Eu vou fazer uma série. Calma, calma. Tô perdido." (``unknown_goal``);
  * ``search("tio Criador situação tio notícia")`` a partir de "o que que tá acontecendo com o tio?" (ASR de "tudo"): 1 dos 4 termos da consulta
    estava na fala; a notícia ficou 2 min na ficha.

Este módulo NÃO é autorização. A política (``core/tool_authorization.py``, domínio do Max) continua decidindo o que é permitido; aqui só se NEGA, depois
dela, uma chamada permitida que não veio de um pedido claro. Sem informação de turno (Jev, série, possessão, texto digitado) o guard fica INATIVO.

Regras (todas medidas nos casos acima):
  * ``minecraft_parar`` só roda se a fala inteira for uma ordem de parada/retenção (as mesmas frases deterministas do controle da série);
  * ferramentas do corpo exigem fala dirigida à Célia ou ordem no imperativo, e não fragmento (inacabado, interjeição, palavra solta);
  * ``search`` exige que os termos da consulta venham da fala (>= 50%);
  * qualquer outra ferramenta com efeito não roda sobre fragmento;
  * a mesma chamada (ferramenta + argumentos) não executa duas vezes na mesma fala (ordem repetida por reentrega).
"""
from __future__ import annotations

import contextvars
import json
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from .asr_turn_gate import is_backchannel_only, is_urgent_stop_text, looks_unfinished  # uma definição só de fragmento/interjeição/parada
from .tool_authorization import HIGH_RISK_TOOLS, MEDIUM_CAPABILITIES  # constantes só de leitura: este módulo não altera a política

SIDE_EFFECT = frozenset(MEDIUM_CAPABILITIES) | frozenset(HIGH_RISK_TOOLS)
BODY_TOOLS = frozenset(name for name in MEDIUM_CAPABILITIES if name.startswith("minecraft_"))
EXTERNAL_SEARCH = frozenset({"search"})
QUERY_OVERLAP_MIN = 0.5

_IMPERATIVE_START = frozenset({
    "pega", "pegue", "vai", "va", "vem", "venha", "anda", "ande", "corta", "corte", "minera", "mine", "cava", "cave", "constroi", "construa",
    "coloca", "coloque", "pula", "pule", "ataca", "ataque", "segue", "siga", "mata", "mate", "olha", "olhe", "abre", "abra", "fecha", "feche",
    "liga", "ligue", "desliga", "desligue", "troca", "troque", "usa", "use", "come", "coma", "dorme", "durma", "volta", "volte", "para", "pare",
})


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", value.casefold())).strip()


def _words(text: str) -> list[str]:
    return _fold(text).split()


is_urgent_stop = is_urgent_stop_text


def _addressed(text: str) -> bool:
    return bool(re.search(r"\bcelia\b", _fold(text)))


@dataclass
class TurnInfo:
    text: str
    utterance_id: str = ""
    fragment: bool = False                      # veio de fragmento liberado por tempo / inacabado / interjeição
    executed: set[str] = field(default_factory=set)


_turn: contextvars.ContextVar[TurnInfo | None] = contextvars.ContextVar("celia_turn_tool_guard", default=None)


def begin_turn(text: str, *, utterance_id: str = "", fragment: bool = False) -> TurnInfo:
    info = TurnInfo(text=str(text or ""), utterance_id=str(utterance_id or ""), fragment=bool(fragment))
    _turn.set(info)
    return info


def clear_turn() -> None:
    """Turno sem ASR (texto digitado, ponte, máquina): guard inativo. Chamar a cada turno evita herdar o do anterior."""
    _turn.set(None)


def current_turn() -> TurnInfo | None:
    return _turn.get()


def _signature(tool_name: str, args: Any) -> str:
    try:
        return tool_name + "|" + json.dumps(args, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return tool_name + "|" + str(args)


def _query_overlap(args: Any, text: str) -> float | None:
    from .jev import _salient  # import tardio: jev importa módulos que importam este

    if not isinstance(args, dict):
        return None
    query = str(args.get("query") or args.get("q") or "").strip()
    terms = _salient(query)
    if not terms:
        return None
    return len(terms & _salient(text)) / len(terms)


def check(tool_name: str, args: Any, *, turn: TurnInfo | None = None) -> str | None:
    """None = não bloqueia. String = motivo curto do bloqueio (vai para o trace como ``turn_guard:<motivo>``)."""
    info = turn if turn is not None else current_turn()
    if info is None:
        return None
    text = info.text
    signature = _signature(tool_name, args)
    if tool_name in SIDE_EFFECT and info.utterance_id and signature in info.executed:
        return "duplicate_order_same_utterance"
    fragment = info.fragment or looks_unfinished(text) or is_backchannel_only(text)
    reason: str | None = None
    if tool_name == "minecraft_parar":
        if not is_urgent_stop(text):
            reason = "not_an_explicit_stop"
    elif tool_name in BODY_TOOLS:
        first = (_words(text) or [""])[0]
        if fragment or (len(_words(text)) < 2 and not is_urgent_stop(text)):
            reason = "fragment_not_an_order"
        elif not (_addressed(text) or first in _IMPERATIVE_START):
            reason = "not_addressed_to_her"
    elif tool_name in EXTERNAL_SEARCH:
        overlap = _query_overlap(args, text)
        if overlap is not None and overlap < QUERY_OVERLAP_MIN:
            reason = "query_not_from_what_he_said"
    elif tool_name in SIDE_EFFECT:
        if fragment:
            reason = "fragment_not_an_order"
    if reason is None and tool_name in SIDE_EFFECT and info.utterance_id:
        info.executed.add(signature)
    return reason
