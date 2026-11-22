#!/usr/bin/env python3
"""
MCP Server - Memória Persistente da Célia
Salva e recupera memórias entre sessões via arquivo JSON.
"""

import json
import time
from pathlib import Path
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memoria_mcp")

CELIA_ROOT = Path(__file__).parent.parent
MEMORIA_FILE = CELIA_ROOT / "memoria_persistente.json"


def _carregar() -> dict:
    if MEMORIA_FILE.exists():
        try:
            return json.loads(MEMORIA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _salvar(dados: dict):
    MEMORIA_FILE.write_text(json.dumps(dados, ensure_ascii=False, indent=2), encoding="utf-8")


@mcp.tool()
async def salvar_memoria(chave: str, valor: str) -> str:
    """
    Salva uma informação na memória persistente da Célia.
    A memória sobrevive entre sessões.
    chave: identificador único (ex: "nome_do_usuario", "jogo_favorito")
    valor: informação a salvar
    """
    try:
        dados = _carregar()
        dados[chave] = {"valor": valor, "ts": time.time()}
        _salvar(dados)
        return f"[OK] Memória salva: {chave} = {valor[:80]}"
    except Exception as e:
        return f"[ERR] {e}"


@mcp.tool()
async def ler_memoria(chave: str = "") -> str:
    """
    Lê memória persistente da Célia.
    Se chave vazia, retorna todas as memórias.
    """
    try:
        dados = _carregar()
        if not dados:
            return "[OK] Nenhuma memória salva ainda"
        if chave:
            item = dados.get(chave)
            if item is None:
                return f"[WRN] Chave '{chave}' não encontrada"
            return f"{chave} = {item['valor']}"
        linhas = []
        for k, v in dados.items():
            linhas.append(f"  • {k}: {v['valor']}")
        return "📚 Memórias da Célia:\n" + "\n".join(linhas)
    except Exception as e:
        return f"[ERR] {e}"


@mcp.tool()
async def esquecer_memoria(chave: str) -> str:
    """Remove uma entrada da memória persistente."""
    try:
        dados = _carregar()
        if chave not in dados:
            return f"[WRN] Chave '{chave}' não encontrada"
        del dados[chave]
        _salvar(dados)
        return f"[OK] Memória '{chave}' removida"
    except Exception as e:
        return f"[ERR] {e}"


if __name__ == "__main__":
    mcp.run()
