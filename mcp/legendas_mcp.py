#!/usr/bin/env python3
"""
MCP Server - Legendas tipo Neuro-sama
Atualiza arquivo legenda.json que é lido por assets/live/legendas/index.html (Browser Source OBS).
"""

import json
import time
from pathlib import Path

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("legendas_mcp")

CELIA_ROOT = Path(__file__).parent.parent
LEGENDA_FILE = CELIA_ROOT / "legenda.json"


def _salvar_legenda(texto: str, visivel: bool = True):
    LEGENDA_FILE.write_text(
        json.dumps({"texto": texto, "visivel": visivel, "ts": time.time()}, ensure_ascii=False),
        encoding="utf-8",
    )


@mcp.tool()
async def atualizar_legenda(texto: str) -> str:
    """
    Atualiza o texto da legenda exibida no OBS (Browser Source).
    O texto aparece na tela em estilo Neuro-sama — fundo escuro semitransparente.
    Deixe texto vazio para limpar a legenda.
    """
    try:
        _salvar_legenda(texto, visivel=bool(texto.strip()))
        if texto.strip():
            return f"[OK] Legenda atualizada: {texto[:60]}"
        return "[OK] Legenda limpa"
    except Exception as e:
        return f"[ERR] {e}"


@mcp.tool()
async def limpar_legenda() -> str:
    """Remove o texto da legenda da tela."""
    try:
        _salvar_legenda("", visivel=False)
        return "[OK] Legenda limpa"
    except Exception as e:
        return f"[ERR] {e}"


if __name__ == "__main__":
    # Garante que o arquivo existe ao iniciar
    if not LEGENDA_FILE.exists():
        _salvar_legenda("", visivel=False)
    mcp.run()
