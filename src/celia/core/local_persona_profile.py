"""Persona local coerente com o conf.yaml; preserva o contexto dinâmico.

A Gemma usa a MESMA persona-base ativa de outras LLMs. A Mediana histórica
só é fallback quando a configuração oficial não está disponível. O restante
do system, incluindo cena e memórias, não é descartado.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from .raizes import RAIZ

OFFICIAL_PERSONA_PATH = Path(RAIZ) / "config" / "personas" / "celia-mediana-oficial.txt"

@lru_cache(maxsize=1)
def _mediana_persona() -> str:
    """Backup histórico de estilo; não substitui um conf.yaml válido."""
    return OFFICIAL_PERSONA_PATH.read_text(encoding="utf-8").strip()


LOCAL_RUNTIME_CONTRACTS = (
    "\n\n## Operação local\n"
    "Segurança, Guardian e evidências reais precedem piadas. "
    "Só chame alguém de Criador ou pai quando o interlocutor for confirmado. "
    "Ações dependem de autorização e resultado confirmado; não alegue ações não realizadas. "
    "Visão só quando imagem ou OCR realmente forem recebidos. "
    "Respeite os estados de pausa do controlador e não exponha texto interno. "
    "Nunca transforme uma falha técnica em fala da Célia.\n"
)


@lru_cache(maxsize=1)
def _original_persona() -> str:
    path = Path(RAIZ) / "conf.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return str((data.get("character_config") or {}).get("persona_prompt") or "")
    except (OSError, ValueError, TypeError, yaml.YAMLError):
        return ""


def local_persona_system(system: str | None) -> str:
    full = str(system or "")
    active = _original_persona()
    # O system montado pelo agente ja possui conduta -> persona -> cena.
    # Mantenha essa ordem: nao recoloque a persona no topo nem a duplique.
    if active and active in full:
        return full + LOCAL_RUNTIME_CONTRACTS
    # Chamadas isoladas sem a persona ativa recebem a identidade do conf;
    # a Mediana antiga so serve de fallback na falta do conf.yaml.
    persona = active or _mediana_persona()
    return persona + LOCAL_RUNTIME_CONTRACTS + ("\n\n" + full.strip() if full.strip() else "")
