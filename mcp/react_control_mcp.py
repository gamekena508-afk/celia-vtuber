#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MCP Server - Controle do React
Ferramentas para ligar/desligar/monitorar o sistema de análise de vídeo em tempo real.
"""

import os
import json
import subprocess
from pathlib import Path

import psutil
from mcp.server.fastmcp import FastMCP

from celia_paths import REACT_DIR, is_available, missing_message, venv_python

mcp = FastMCP("react_control")

# Portable layout (see celia_paths.py). `react` is an OPTIONAL sibling project:
# when it is not installed, every tool returns an explicit "unavailable" message
# instead of a confusing FileNotFoundError.
REACT_PYTHON    = venv_python(REACT_DIR)
REACT_SCRIPT    = REACT_DIR / "react_av13.py"
REACT_STOP_FLAG = REACT_DIR / "STOP"
REACT_SUMMARY   = REACT_DIR / "summary.json"


def _unavailable() -> str | None:
    """Guard for every tool: None when usable, else the message to return."""
    if not is_available(REACT_DIR):
        return missing_message("react", REACT_DIR)
    if not REACT_SCRIPT.exists():
        return f"[INDISPONÍVEL] Script do react não encontrado: {REACT_SCRIPT}"
    return None


@mcp.tool()
async def ligar_react() -> str:
    """Liga o sistema de react (análise de vídeo em tempo real)."""
    if (msg := _unavailable()):
        return msg
    if not REACT_PYTHON.exists():
        return f"[ERR] Python do react nao encontrado: {REACT_PYTHON}"
    REACT_STOP_FLAG.unlink(missing_ok=True)
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    # CREATE_NEW_CONSOLE only exists on Windows; on Linux detach with a new
    # session so the child survives this MCP server restarting.
    spawn_kwargs = (
        {"creationflags": subprocess.CREATE_NEW_CONSOLE}
        if hasattr(subprocess, "CREATE_NEW_CONSOLE")
        else {"start_new_session": True}
    )
    subprocess.Popen(
        [str(REACT_PYTHON), str(REACT_SCRIPT)],
        cwd=str(REACT_DIR),
        env=env,
        **spawn_kwargs,
    )
    return "[OK] React iniciado"


@mcp.tool()
async def desligar_react() -> str:
    """Desliga o react graciosamente via arquivo STOP."""
    if (msg := _unavailable()):
        return msg
    REACT_STOP_FLAG.touch()
    return "[OK] Sinal de parada enviado ao react"


@mcp.tool()
async def status_react() -> str:
    """Verifica se o react está rodando e retorna o PID."""
    if (msg := _unavailable()):
        return msg
    for proc in psutil.process_iter(["pid", "cmdline"]):
        cmd = " ".join(proc.info.get("cmdline") or [])
        if "react_av13" in cmd:
            return f"[ON] Rodando (PID {proc.info['pid']})"
    return "[OFF] Não está rodando"


@mcp.tool()
async def get_react_summary() -> str:
    """Retorna o que o react está assistindo — tema, pessoa na tela, transcrições recentes e reações dadas."""
    if (msg := _unavailable()):
        return msg
    if not REACT_SUMMARY.exists():
        return "[INFO] React não está rodando ou ainda não gerou resumo"
    try:
        with open(REACT_SUMMARY, encoding="utf-8") as f:
            dados = json.load(f)
        return json.dumps(dados, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"[ERR] Falha ao ler resumo: {e}"


if __name__ == "__main__":
    mcp.run()
