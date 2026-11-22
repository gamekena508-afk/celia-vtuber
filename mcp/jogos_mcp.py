#!/usr/bin/env python3
"""
MCP dos jogos da Celia — abrir a dama, o xadrez e o jogo da velha por comando.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
Medido em 2026-08-27, a pedido do Pai: as tres paginas estao no ar
(HTTP 200 em /games/checkers/, /games/chess/ e /games/tic-tac-toe/) e a
jogada dela JA aparece no tabuleiro, porque a pagina pergunta a acao em
/bridge/game/action e executa. O que NAO existia era comando para ela ABRIR
o jogo: nenhuma ferramenta de jogo estava registrada nos MCPs dela.

Existia so a generica `open_browser` do system-automation, e nada no prompt
dela ensina a porta nem o caminho — ela nao teria como adivinhar
`http://127.0.0.1:12393/games/checkers/`. Este MCP fecha esse buraco: os
enderecos ficam AQUI, e ela so precisa dizer qual jogo.

CUSTO ZERO DE VRAM: so HTTP e `xdg-open`. Nada de modelo.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

BASE = os.environ.get("CELIA_BRIDGE_URL", "http://127.0.0.1:12393").rstrip("/")
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from celia.game_browser import game_url, open_game_browser

# Um jogo, varios jeitos de o Pai pedir. A chave e o caminho; os apelidos sao
# como ele fala de verdade — "abre a dama", "bota o xadrez", "jogo da velha".
JOGOS: dict[str, dict] = {
    "checkers": {
        "nome": "Damas",
        "caminho": "/games/checkers/",
        "apelidos": ["dama", "damas", "checkers", "jogo de damas"],
    },
    "chess": {
        "nome": "Xadrez",
        "caminho": "/games/chess/",
        "apelidos": ["xadrez", "chess", "jogo de xadrez"],
    },
    "tic-tac-toe": {
        "nome": "Jogo da velha",
        "caminho": "/games/tic-tac-toe/",
        "apelidos": ["velha", "jogo da velha", "tic tac toe", "tic-tac-toe", "galo"],
    },
}


def _resolver(pedido: str) -> str | None:
    """Aceita a chave, o nome ou qualquer apelido, sem ligar para maiuscula."""
    alvo = (pedido or "").strip().lower()
    if not alvo:
        return None
    for chave, dados in JOGOS.items():
        if alvo == chave or alvo == dados["nome"].lower() or alvo in dados["apelidos"]:
            return chave
    # tolerancia: "abre a dama pra mim" -> acha o apelido dentro da frase
    for chave, dados in JOGOS.items():
        for apelido in [chave, dados["nome"].lower()] + dados["apelidos"]:
            if apelido in alvo:
                return chave
    return None


def _no_ar(caminho: str, timeout: int = 4) -> tuple[bool, str]:
    url = f"{BASE}{caminho}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return (r.status == 200), f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        return False, f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, repr(e)


def _abrir(chave: str, url: str, modo: str = "criador-vs-celia") -> tuple[bool, str]:
    """Abre o tabuleiro pelo mesmo launcher de navegador usado pelo painel."""
    result = open_game_browser(
        chave,
        modo,
        base_url=BASE,
        dry_run=os.environ.get("CELIA_JOGOS_SEM_ABRIR") == "1",
    )
    if result.get("ok"):
        return True, f"aberto em janela normal do navegador ({result.get('launcher', 'browser')})"
    return False, str(result.get("error") or f"falha ao abrir {url}")


def _fechar() -> tuple[bool, str]:
    """Pede somente à página de jogo ativa para fechar; nunca mata o navegador."""
    if os.environ.get("CELIA_JOGOS_SEM_ABRIR") == "1":
        return True, "modo de teste: nao fechei janela"
    payload = json.dumps({"action": "close"}).encode("utf-8")
    request = urllib.request.Request(
        f"{BASE}/games/api/control-active",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            if response.status == 200:
                return True, "pedido de fechamento enviado somente ao jogo ativo"
            return False, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return False, f"HTTP {exc.code}"
    except Exception as exc:  # noqa: BLE001
        return False, repr(exc)


server = Server("jogos-celia")


@server.list_tools()
async def listar() -> list[Tool]:
    apelidos = ", ".join(
        f"{d['nome']} ({'/'.join(d['apelidos'][:3])})" for d in JOGOS.values()
    )
    return [
        Tool(
            name="abrir_jogo",
            description=(
                "Abre na tela do Pai um dos jogos que a Celia joga com ele e "
                f"ja entra na partida. Jogos: {apelidos}. Use quando ele disser "
                "'abre a dama', 'bota o xadrez', 'vamos jogar da velha' e afins."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "jogo": {
                        "type": "string",
                        "description": "dama | xadrez | velha (aceita apelidos e a frase inteira)",
                    },
                    "modo": {
                        "type": "string",
                        "enum": ["criador-vs-celia", "chat-vs-celia", "chat-vs-criador"],
                        "description": "Modo/adversário. Padrão: criador-vs-celia.",
                    }
                },
                "required": ["jogo"],
            },
        ),
        Tool(
            name="fechar_jogo",
            description=(
                "Tira da tela a janela do jogo atual sem fechar a Célia. Use quando o Pai disser "
                "'tira o jogo da tela', 'fecha o jogo', 'sai do jogo' ou equivalente."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="listar_jogos",
            description="Diz quais jogos existem e se cada um esta no ar agora.",
            inputSchema={"type": "object", "properties": {}},
        ),
    ]


@server.call_tool()
async def chamar(name: str, arguments: dict) -> list[TextContent]:
    if name == "fechar_jogo":
        ok, nota = _fechar()
        if ok:
            return [TextContent(type="text", text=f"Pedido de fechamento enviado ao jogo ativo ({nota}).")]
        return [TextContent(type="text", text=f"Nao consegui tirar o jogo da tela: {nota}")]

    if name == "listar_jogos":
        linhas = []
        for chave, d in JOGOS.items():
            ok, nota = _no_ar(d["caminho"])
            linhas.append(f"{'[OK]' if ok else '[FORA]'} {d['nome']} — {BASE}{d['caminho']} ({nota})")
        return [TextContent(type="text", text="\n".join(linhas))]

    if name == "abrir_jogo":
        pedido = str((arguments or {}).get("jogo", ""))
        chave = _resolver(pedido)
        if not chave:
            nomes = ", ".join(d["nome"] for d in JOGOS.values())
            return [TextContent(
                type="text",
                text=f"Nao reconheci o jogo {pedido!r}. Eu tenho: {nomes}.",
            )]
        d = JOGOS[chave]
        modo = str((arguments or {}).get("modo") or "criador-vs-celia")
        try:
            url = game_url(chave, modo, base_url=BASE)
        except ValueError:
            return [TextContent(type="text", text=f"Modo de jogo inválido: {modo}.")]
        # Honestidade primeiro: se a pagina nao responde, dizer isso em vez de
        # abrir uma aba morta e deixar o Pai achando que e culpa dele.
        ok, nota = _no_ar(d["caminho"])
        if not ok:
            return [TextContent(
                type="text",
                text=(f"O {d['nome']} nao respondeu ({nota}). A Celia precisa estar "
                      f"no ar em {BASE} para o jogo abrir. Nao abri nada."),
            )]
        aberto, como = _abrir(chave, url, modo)
        if not aberto:
            return [TextContent(type="text", text=f"Nao consegui abrir: {como}. URL: {url}")]
        return [TextContent(type="text", text=f"Pedido para abrir {d['nome']} aceito pelo navegador ({como}). {url}")]

    return [TextContent(type="text", text=f"Ferramenta desconhecida: {name}")]


async def main() -> None:
    print(f"--- MCP dos jogos pronto (base {BASE}) ---", file=sys.stderr)
    async with stdio_server() as (leitura, escrita):
        await server.run(
            leitura,
            escrita,
            InitializationOptions(
                server_name="jogos-celia",
                server_version="1.0.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    asyncio.run(main())
