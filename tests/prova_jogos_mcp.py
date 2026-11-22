#!/usr/bin/env python3
"""
Prova do MCP dos jogos — instrumento do juiz.

Cobra o que o Pai perguntou, em ordem:
  0  as 3 paginas respondem no servidor da Celia
  1  o MCP expoe as 3 ferramentas por stdio real
  2  `listar_jogos` diz a verdade sobre quem esta no ar
  3  `abrir_jogo` entende como o Pai FALA — "dama", "xadrez", "jogo da velha",
     e tambem a frase inteira "abre a dama pra mim"
  4  pedido desconhecido nao abre nada e diz o que existe
  5  quando a pagina esta fora, ele AVISA em vez de abrir aba morta

As chamadas usam CELIA_JOGOS_SEM_ABRIR=1: aqui se prova o roteamento, nao se
enche a tela do Pai de janela. A abertura de verdade e feita a parte, uma vez,
para ele VER acontecer.
"""
from __future__ import annotations

import asyncio
import os
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
MCP = RAIZ / "mcp" / "jogos_mcp.py"
BASE = os.environ.get("CELIA_BRIDGE_URL", "http://127.0.0.1:12393").rstrip("/")

CAMINHOS = ["/games/checkers/", "/games/chess/", "/games/tic-tac-toe/"]

# como ele fala -> o que tem de sair
FALAS = [
    ("dama", "Damas"),
    ("damas", "Damas"),
    ("checkers", "Damas"),
    ("abre a dama pra mim", "Damas"),
    ("xadrez", "Xadrez"),
    ("bota o xadrez ai", "Xadrez"),
    ("velha", "Jogo da velha"),
    ("jogo da velha", "Jogo da velha"),
    ("vamos jogar o jogo da velha", "Jogo da velha"),
    ("TIC-TAC-TOE", "Jogo da velha"),
]


def p(t: str = "") -> None:
    print(t, flush=True)


def camada0() -> tuple[bool, str]:
    fora = []
    for c in CAMINHOS:
        try:
            with urllib.request.urlopen(f"{BASE}{c}", timeout=5) as r:
                if r.status != 200:
                    fora.append(f"{c} HTTP {r.status}")
        except Exception as e:  # noqa: BLE001
            fora.append(f"{c} {e!r}")
    if fora:
        return False, "paginas fora: " + "; ".join(fora)
    return True, f"as 3 paginas responderam HTTP 200 em {BASE}"


async def resto() -> tuple[bool, bool, bool, bool, list[str]]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP)],
        env={**os.environ, "CELIA_JOGOS_SEM_ABRIR": "1", "CELIA_BRIDGE_URL": BASE},
    )
    notas: list[str] = []
    ok1 = ok2 = ok3 = ok4 = False
    async with stdio_client(params) as (le, esc):
        async with ClientSession(le, esc) as s:
            await asyncio.wait_for(s.initialize(), timeout=30)
            nomes = {t.name for t in (await s.list_tools()).tools}
            ok1 = {"abrir_jogo", "fechar_jogo", "listar_jogos"} <= nomes
            notas.append(f"ferramentas expostas: {sorted(nomes)}")

            r = await s.call_tool("listar_jogos", {})
            txt = " ".join(getattr(c, "text", "") or "" for c in (r.content or []))
            ok2 = txt.count("[OK]") == 3
            notas.append(f"listar_jogos -> {txt.count('[OK]')} no ar, {txt.count('[FORA]')} fora")

            erros = []
            for fala, esperado in FALAS:
                r = await s.call_tool("abrir_jogo", {"jogo": fala})
                t = " ".join(getattr(c, "text", "") or "" for c in (r.content or []))
                if "Pedido para abrir" not in t or esperado not in t:
                    erros.append(f"{fala!r} -> {t[:90]!r} (esperava roteamento para {esperado!r})")
            ok3 = not erros
            notas.append(f"falas entendidas: {len(FALAS) - len(erros)} de {len(FALAS)}")
            for e in erros:
                notas.append(f"  [FALHA] {e}")

            r = await s.call_tool("abrir_jogo", {"jogo": "pebolim"})
            t = " ".join(getattr(c, "text", "") or "" for c in (r.content or []))
            ok4 = ("Nao reconheci" in t) and ("Damas" in t)
            notas.append(f"pedido desconhecido -> {t[:80]!r}")
    return ok1, ok2, ok3, ok4, notas


async def camada5() -> tuple[bool, str]:
    """Servidor fora do ar: tem de AVISAR, nunca abrir aba morta."""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    params = StdioServerParameters(
        command=sys.executable,
        args=[str(MCP)],
        # porta deliberadamente morta
        env={**os.environ, "CELIA_BRIDGE_URL": "http://127.0.0.1:1"},
    )
    async with stdio_client(params) as (le, esc):
        async with ClientSession(le, esc) as s:
            await asyncio.wait_for(s.initialize(), timeout=30)
            r = await s.call_tool("abrir_jogo", {"jogo": "dama"})
            t = " ".join(getattr(c, "text", "") or "" for c in (r.content or []))
    if "Nao abri nada" in t and "nao respondeu" in t:
        return True, t[:110]
    return False, f"esperava aviso honesto, veio: {t[:110]!r}"


def main() -> int:
    p("=" * 68)
    p("PROVA DO MCP DOS JOGOS — instrumento do juiz")
    p(f"bridge: {BASE} | interpretador: {sys.executable}")
    p("=" * 68)

    ok0, d0 = camada0()
    p(f"\nCAMADA 0 — as 3 paginas no ar: {'PASSOU' if ok0 else 'REPROVOU'}\n  {d0}")

    ok1, ok2, ok3, ok4, notas = asyncio.run(resto())
    p(f"\nCAMADA 1 — 3 ferramentas por stdio: {'PASSOU' if ok1 else 'REPROVOU'}")
    p(f"CAMADA 2 — listar_jogos diz a verdade: {'PASSOU' if ok2 else 'REPROVOU'}")
    p(f"CAMADA 3 — entende como o Pai fala: {'PASSOU' if ok3 else 'REPROVOU'}")
    p(f"CAMADA 4 — pedido desconhecido nao abre nada: {'PASSOU' if ok4 else 'REPROVOU'}")
    for n in notas:
        p(f"  {n}")

    ok5, d5 = asyncio.run(camada5())
    p(f"\nCAMADA 5 — servidor fora vira aviso, nao aba morta: {'PASSOU' if ok5 else 'REPROVOU'}\n  {d5}")

    v = all([ok0, ok1, ok2, ok3, ok4, ok5])
    p("\n" + "=" * 68)
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 68)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
