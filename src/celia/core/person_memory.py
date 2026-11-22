"""Perfis sociais estáveis e pequenos para o prompt, por ID confirmado.
Não substitui a memória episódica de vídeo nem deduz IDs a partir do ASR.
"""
from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

from .raizes import VAR

DEFAULT_PATH = VAR / "memoria" / "perfis_pessoas.json"
MAX_CHARS = 520


def _norm(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def load_profiles(path: Path | None = None) -> list[dict]:
    try:
        raw = json.loads(Path(path or DEFAULT_PATH).read_text(encoding="utf-8"))
        return [x for x in raw.get("profiles", []) if isinstance(x, dict) and x.get("discord_user_id") and x.get("name")]
    except (OSError, ValueError, TypeError):
        return []


def context_block(
    text: str, *, speaker: str = "", discord_author_id: str = "",
    owner: bool = False, path: Path | None = None,
) -> str:
    """Dados validados por identidade; nomes citados só selecionam fichas já cadastradas.

    Nunca mapeia a identidade do emissor por semelhança de nome/ASR.
    """
    profiles = load_profiles(path)
    if not profiles:
        return ""
    by_id = {str(x["discord_user_id"]): x for x in profiles}
    selected: list[dict] = []
    # Quem está falando: apenas por ID vindo da plataforma (não de transcrição).
    current = by_id.get(str(discord_author_id or ""))
    if current is not None:
        selected.append(current)
    # Nomes explicitamente mencionados no texto selecionam fichas conhecidas,
    # mas NÃO validam a identidade do falante.
    normalized = _norm(text)
    for profile in profiles:
        name = str(profile["name"])
        if profile in selected:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(_norm(name)) + r"(?![a-z0-9])", normalized):
            selected.append(profile)
    if not selected:
        return ""
    lines = []
    for profile in selected[:2]:
        name = profile["name"]
        kind = "outra IA" if profile.get("kind") == "outra_ia" else "humano"
        relation = str(profile.get("relationship", "")).strip()
        facts = "; ".join(str(f) for f in (profile.get("facts") or [])[:1])
        line = f"PESSOA CONHECIDA: {name} ({kind}). {relation}"
        if facts:
            line += " " + facts
        available = MAX_CHARS - sum(len(x) + 1 for x in lines)
        if available < 65:
            break
        lines.append(line[:available])
    return "\n".join(lines)
