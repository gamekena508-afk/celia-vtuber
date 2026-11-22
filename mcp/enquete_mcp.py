#!/usr/bin/env python3
"""
MCP Server - Enquetes de Gameplay
Cria e gerencia enquetes com resultado via Browser Source no OBS.
Estado salvo em estado_enquete.json (lido pelo HTML do Browser Source).
"""

import json
import time
import threading
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("enquete_mcp")

CELIA_ROOT = Path(__file__).parent.parent
ESTADO_FILE = CELIA_ROOT / "estado_enquete.json"

_lock = threading.Lock()

_enquete = {
    "ativa": False,
    "pergunta": "",
    "opcoes": [],
    "votos": {},
    "ts_inicio": 0.0,
}


def _salvar():
    ESTADO_FILE.write_text(
        json.dumps(_enquete, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


@mcp.tool()
async def criar_enquete(pergunta: str, opcoes: str) -> str:
    """
    Cria uma nova enquete.
    pergunta: texto da pergunta
    opcoes: opções separadas por vírgula (ex: "Minecraft, Fortnite, GTA")
    """
    with _lock:
        lista = [o.strip() for o in opcoes.split(",") if o.strip()]
        if len(lista) < 2:
            return "[ERR] Forneça pelo menos 2 opções separadas por vírgula"
        _enquete["ativa"] = True
        _enquete["pergunta"] = pergunta
        _enquete["opcoes"] = lista
        _enquete["votos"] = {o: 0 for o in lista}
        _enquete["ts_inicio"] = time.time()
        _salvar()
        return f"[OK] Enquete criada: '{pergunta}'\nOpções: {', '.join(lista)}"


@mcp.tool()
async def registrar_voto(opcao: str) -> str:
    """
    Registra um voto em uma opção da enquete ativa.
    opcao: texto exato da opção (ou prefixo único)
    """
    with _lock:
        if not _enquete["ativa"]:
            return "[WRN] Nenhuma enquete ativa. Use criar_enquete() primeiro."
        opcao = opcao.strip()
        # Busca exata primeiro, depois prefixo
        alvo = None
        if opcao in _enquete["votos"]:
            alvo = opcao
        else:
            matches = [o for o in _enquete["opcoes"] if o.lower().startswith(opcao.lower())]
            if len(matches) == 1:
                alvo = matches[0]
            elif len(matches) > 1:
                return f"[WRN] Opção ambígua. Possíveis: {', '.join(matches)}"
        if alvo is None:
            return f"[ERR] Opção '{opcao}' não encontrada. Disponíveis: {', '.join(_enquete['opcoes'])}"
        _enquete["votos"][alvo] += 1
        _salvar()
        return f"[OK] Voto registrado em '{alvo}' (total: {_enquete['votos'][alvo]})"


@mcp.tool()
async def resultado_enquete() -> str:
    """Retorna o resultado atual da enquete ativa."""
    with _lock:
        if not _enquete["pergunta"]:
            return "[WRN] Nenhuma enquete criada ainda"
        total = sum(_enquete["votos"].values())
        linhas = [f"📊 {_enquete['pergunta']}", f"Total de votos: {total}", ""]
        for opcao, votos in sorted(_enquete["votos"].items(), key=lambda x: -x[1]):
            pct = (votos / total * 100) if total > 0 else 0
            barra = "█" * int(pct / 5)
            linhas.append(f"  {opcao}: {votos} votos ({pct:.0f}%) {barra}")
        estado = "✅ ATIVA" if _enquete["ativa"] else "🔒 ENCERRADA"
        linhas.append(f"\nStatus: {estado}")
        return "\n".join(linhas)


@mcp.tool()
async def fechar_enquete() -> str:
    """Encerra a enquete atual e exibe o resultado final."""
    with _lock:
        if not _enquete["ativa"]:
            return "[WRN] Nenhuma enquete ativa"
        _enquete["ativa"] = False
        _salvar()
        total = sum(_enquete["votos"].values())
        if total == 0:
            return "[OK] Enquete encerrada (sem votos)"
        vencedor = max(_enquete["votos"], key=lambda k: _enquete["votos"][k])
        return f"[OK] Enquete encerrada!\nVencedor: {vencedor} ({_enquete['votos'][vencedor]} votos)"


if __name__ == "__main__":
    if not ESTADO_FILE.exists():
        _salvar()
    mcp.run()
