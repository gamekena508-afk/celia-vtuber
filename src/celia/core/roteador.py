"""Deterministic, local-only selector for the prepared response bank."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from .memoria import Memoria, normalizar_texto


BANK_HIT = "BANK_HIT"
BANK_MISS = "BANK_MISS"
BANK_REJECTED_LOW_CONFIDENCE = "BANK_REJECTED_LOW_CONFIDENCE"
BANK_REJECTED_CONTEXT = "BANK_REJECTED_CONTEXT"
RESULTADOS = frozenset({
    BANK_HIT,
    BANK_MISS,
    BANK_REJECTED_LOW_CONFIDENCE,
    BANK_REJECTED_CONTEXT,
})


@dataclass(frozen=True)
class Decisao:
    """Auditable result; shadow decisions never carry playback audio."""

    resultado: str
    ativo_id: str | None = None
    candidatos: list[dict[str, Any]] = field(default_factory=list)
    regra: dict[str, Any] | None = None
    confianca: float = 0.0
    restricoes: dict[str, Any] = field(default_factory=dict)
    razao: str = ""
    duracao_ms: float = 0.0
    audio_path: None | str = None
    aplicar: bool = False


class Roteador:
    """Select an active using only indexed local memory and explicit rules."""

    def __init__(self, memoria: Memoria, personagem: str, modo: str = "shadow"):
        if modo not in {"shadow", "ativo"}:
            raise ValueError("modo precisa ser shadow ou ativo")
        self.memoria = memoria
        self.personagem = str(personagem or "").strip()
        if not self.personagem:
            raise ValueError("personagem obrigatorio")
        self.modo = modo

    @staticmethod
    def _context_value(contexto: Any) -> str | None:
        if isinstance(contexto, dict):
            for key in ("contexto", "tipo", "nome", "name"):
                value = contexto.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()
            return None
        if contexto is None:
            return None
        value = str(contexto).strip()
        return value or None

    @staticmethod
    def _candidate_view(ativo: dict[str, Any]) -> dict[str, Any]:
        return {
            key: ativo[key]
            for key in ("id", "texto", "categoria", "personagem", "schema", "estado", "selecao")
            if key in ativo
        }

    def _evaluate_restrictions(
        self, restrictions: Any, contexto: Any
    ) -> tuple[bool, dict[str, Any]]:
        if not restrictions:
            return True, {"restricoes": "nenhuma"}
        if not isinstance(restrictions, dict) or not isinstance(contexto, dict):
            return False, {"restricoes": "contexto_estruturado_obrigatorio", "passou": False}
        evaluated: dict[str, Any] = {}
        passed = True
        for key, expected in restrictions.items():
            actual = contexto.get(key)
            item_passed = actual in expected if isinstance(expected, list) else actual == expected
            evaluated[str(key)] = {
                "esperado": expected,
                "recebido": actual,
                "passou": bool(item_passed),
            }
            passed = passed and bool(item_passed)
        return passed, evaluated

    def decidir(self, entrada: str, contexto: Any = None) -> Decisao:
        started = time.perf_counter()
        normalized = normalizar_texto(entrada) if isinstance(entrada, str) else ""
        if not normalized:
            return self._decision(
                started, BANK_MISS, [], None, 0.0, {}, "entrada vazia ou invalida"
            )

        # The trigger index applies the character barrier before payloads are read.
        ativos = self.memoria.buscar(
            personagem=self.personagem,
            estado="publicado",
            gatilho=normalized,
        )
        candidatos = [self._candidate_view(ativo) for ativo in ativos]
        if not candidatos:
            return self._decision(
                started, BANK_MISS, [], None, 0.0, {}, "nenhum gatilho indexado bateu"
            )

        context_value = self._context_value(contexto)
        context_rejections: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        confidence_rejections: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        valid: list[tuple[dict[str, Any], dict[str, Any], float]] = []
        for ativo in ativos:
            regra = ativo.get("selecao")
            if not isinstance(regra, dict):
                continue
            permitidos = [normalizar_texto(item) for item in regra.get("contextos_permitidos", [])]
            restrictions_ok, evaluated = self._evaluate_restrictions(
                regra.get("restricoes"), contexto
            )
            evaluated["contexto"] = context_value
            evaluated["contextos_permitidos"] = permitidos
            if permitidos and (context_value is None or normalizar_texto(context_value) not in permitidos):
                evaluated["passou"] = False
                context_rejections.append((ativo, evaluated, 1.0))
                continue
            if not restrictions_ok:
                evaluated["passou"] = False
                context_rejections.append((ativo, evaluated, 1.0))
                continue
            confidence = 1.0
            minimum = float(regra.get("confianca_minima", 0.0))
            if confidence < minimum:
                evaluated["passou"] = False
                confidence_rejections.append((ativo, evaluated, confidence))
                continue
            evaluated["passou"] = True
            valid.append((ativo, evaluated, confidence))

        if len(valid) == 1:
            ativo, restrictions, confidence = valid[0]
            return self._decision(
                started,
                BANK_HIT,
                candidatos,
                ativo,
                confidence,
                restrictions,
                "gatilho exato, contexto e restricoes aprovados",
            )
        if len(valid) > 1:
            return self._decision(
                started,
                BANK_MISS,
                candidatos,
                None,
                0.0,
                {"ambiguidade": len(valid)},
                "mais de um ativo elegivel; ambiguidade conservadora",
            )
        if context_rejections:
            ativo, restrictions, confidence = context_rejections[0]
            return self._decision(
                started,
                BANK_REJECTED_CONTEXT,
                candidatos,
                ativo,
                confidence,
                restrictions,
                "contexto ou restricao nao permitidos",
            )
        ativo, restrictions, confidence = confidence_rejections[0]
        return self._decision(
            started,
            BANK_REJECTED_LOW_CONFIDENCE,
            candidatos,
            ativo,
            confidence,
            restrictions,
            "confianca abaixo do minimo da regra",
        )

    def _decision(
        self,
        started: float,
        resultado: str,
        candidatos: list[dict[str, Any]],
        ativo: dict[str, Any] | None,
        confianca: float,
        restricoes: dict[str, Any],
        razao: str,
    ) -> Decisao:
        ativo_id = str(ativo["id"]) if ativo and ativo.get("id") else None
        aplicar = self.modo == "ativo" and resultado == BANK_HIT and ativo_id is not None
        return Decisao(
            resultado=resultado,
            ativo_id=ativo_id if resultado == BANK_HIT else None,
            candidatos=candidatos,
            regra=ativo.get("selecao") if ativo else None,
            confianca=float(confianca),
            restricoes=restricoes,
            razao=razao,
            duracao_ms=(time.perf_counter() - started) * 1000,
            audio_path=(str(ativo.get("audio_path")) if aplicar and ativo else None),
            aplicar=aplicar,
        )


__all__ = [
    "BANK_HIT",
    "BANK_MISS",
    "BANK_REJECTED_LOW_CONFIDENCE",
    "BANK_REJECTED_CONTEXT",
    "Decisao",
    "Roteador",
]
