"""Retrato fixo de uma tentativa real, usado SOMENTE pelo replay e por testes.

Fora de src/: nenhum caminho de producao deve importar isto.
"""

from __future__ import annotations

from typing import Any


def retrato_da_tentativa() -> dict[str, Any]:
    """Estado CONFIRMADO no momento da tentativa de 2026-10-21 14:21-14:25, reconstruído de registros reais:
    ``var/minecraft/series_state.json`` (prepare_intro às 13:54:xx, ``INTRO_READY``, mundo ``celia-serie-s02``), ``evidence/.../monitor_gravacao.jsonl``
    (companheiro ``AT_OWNER_SIDE``, ``held: false``, fase ``presentation``) e o resultado real de ``minecraft_parar`` às 14:22:56. Só para o replay."""
    return {"serie": "INTRO_READY", "fase": "presentation", "mundo": "celia-serie-s02", "retido": False, "movimentos": 0, "acao_atual": "idle",
            "idade_s": 5.0}
