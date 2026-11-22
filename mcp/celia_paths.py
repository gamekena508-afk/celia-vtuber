"""
Portable filesystem layout for Célia's MCP servers.

WHY: the MCP servers were written with machine-specific absolute paths. On a
different host those paths do not exist, so tools stayed "enabled" in the
config while failing at every call — the agent believed it had capabilities it
did not have.

Everything is derived from this file's own location, so moving or renaming the
workspace keeps working. Individual entries can still be overridden with env vars
for unusual installs.

Layout assumed (arquitetura nova, 2026-08-11 — ordem do Pai):
    <raiz>/mcp/celia_paths.py     <- this file
    <raiz>/src/celia    o pacote
    <raiz>/conf.yaml
    <raiz>/integrations/minecraft/mineflayer
    <raiz>/extensions/react
    <raiz>/track
    <raiz>/var  <raiz>/vault  <raiz>/web  <raiz>/secrets  <raiz>/bin

`Célia/` perdeu o nome e virou a raiz. O corpo Minecraft agora mora em
`integrations/minecraft/mineflayer`; a raiz do código E a raiz da árvore são
o mesmo lugar: sobe um nível e acabou.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# <raiz>/mcp/celia_paths.py -> parents[0]=mcp, parents[1]=<raiz>
# Depois do achatamento a raiz do código e a do workspace são o mesmo lugar.
MCP_DIR = Path(__file__).resolve().parent
RAIZ = MCP_DIR.parent
WORKSPACE = Path(os.getenv("CELIA_WORKSPACE", RAIZ))

# Nomes herdados: código antigo importava `VTUBER_DIR` e `CELIA_DIR`. Os três
# apontam pro mesmo lugar agora — mesma verdade, nomes velhos preservados
# pra não quebrar quem importa.
VTUBER_DIR = RAIZ
CELIA_DIR = RAIZ


def _dir(env_name: str, default: Path) -> Path:
    override = os.getenv(env_name)
    return Path(override).expanduser() if override else default


MINECRAFT_BODY_DIR = _dir("CELIA_MINECRAFT_BODY_DIR", WORKSPACE / "integrations" / "minecraft" / "mineflayer")
# Compatibility import name for out-of-tree callers. The production path uses
# MINECRAFT_BODY_DIR and never starts the quarantined Mindcraft runtime.
MINDCRAFT_DIR = MINECRAFT_BODY_DIR
REACT_DIR = _dir("CELIA_REACT_DIR", WORKSPACE / "extensions" / "react")
TRACK_DIR = _dir("CELIA_TRACK_DIR", WORKSPACE / "track")

REFERENCES_DIRS = [RAIZ / "referencias", REACT_DIR / "referencias"]


def venv_python(project_dir: Path) -> Path:
    """
    Interpreter for a sibling project's virtualenv, on either platform.
    Falls back to the interpreter running this process so a caller always gets a
    usable path instead of a Windows-only guess.
    """
    for candidate in (project_dir / ".venv" / "bin" / "python",
                      project_dir / ".venv" / "Scripts" / "python.exe",
                      project_dir / "venv" / "bin" / "python"):
        if candidate.exists():
            return candidate
    return Path(sys.executable)


def is_available(project_dir: Path) -> bool:
    """True when the optional sibling project is actually installed here."""
    return project_dir.is_dir()


def missing_message(name: str, project_dir: Path) -> str:
    return (
        f"[INDISPONÍVEL] O projeto '{name}' não está instalado nesta máquina "
        f"(esperado em {project_dir}). Esta ferramenta não pode ser usada agora. "
        f"Descompacte o projeto ou aponte a variável de ambiente correspondente."
    )


def has_command(name: str) -> bool:
    """True when an external command exists on PATH (e.g. powershell, wmctrl)."""
    return shutil.which(name) is not None

# 2026-08-11: estado de execucao mora em var/. Os MCPs rodam como processo
# separado e nao importam o pacote, entao a constante vive aqui tambem.
VAR_DIR = _dir("CELIA_VAR_DIR", WORKSPACE / "var")
OUTPUT_DIR_PADRAO = VAR_DIR / "output"
