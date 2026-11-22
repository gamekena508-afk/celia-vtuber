"""Evidencia de repeticao na sessao atual (contrato f45e).

Conta falas iguais, normalizadas, da sessao em curso. Texto relembrado e falas da
Celia ficam inertes. Modulo extraido de core/output_judge.py quando o Juiz de
saida foi removido (2026-10-08); estas funcoes nao julgam nem bloqueiam fala.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any


def _normalize(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or "")).casefold()
    value = "".join(char for char in value if not unicodedata.combining(char))
    return " ".join(value.split())


def _normalize_repetition_text(text: str) -> str:
    """Normalize only enough for conservative exact repetition matching."""
    value = _normalize(text)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split())


def calcular_evidencia_repeticao(
    texto: str,
    atual: list[str],
    relembrado: list[str],
    respostas_celia: list[str] | None = None,
) -> dict[str, Any]:
    """Count exact current-session matches; recalled and assistant text is inert."""
    del relembrado, respostas_celia
    target = _normalize_repetition_text(texto)
    if not target:
        occurrences = 0
    else:
        occurrences = sum(
            _normalize_repetition_text(item) == target
            for item in (atual or [])
        )
    return {
        "current_session_occurrences": occurrences,
        "repetition_evidence": occurrences > 0,
    }


def repeticao_na_sessao_atual(
    texto: str, atual: list[str], relembrado: list[str]
) -> bool:
    """Return true only for an exact normalized match in current-session text."""
    return calcular_evidencia_repeticao(texto, atual, relembrado)["repetition_evidence"]
