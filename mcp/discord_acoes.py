#!/usr/bin/env python3
from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from celia.integrations.discord.outbound import (
    discord_status,
    latest_generated_image,
    post_image,
    post_message,
    start_discord_bot,
)

mcp = FastMCP("discord_acoes")


def _json(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)

@mcp.tool()
async def discord_status_bot() -> str:
    """Mostra se o bot da Célia está ligado e se a API Discord está acessível."""
    return _json(await discord_status())


@mcp.tool()
async def discord_ligar_bot() -> str:
    """Liga o bot Discord real da Célia, sem criar uma segunda identidade/bot."""
    started = await start_discord_bot()
    if started.get("ok"):
        status = await discord_status()
        started["status"] = status
    return _json(started)


@mcp.tool()
async def discord_postar_mensagem(
    mensagem: str = "",
    canal_id: str = "",
    marcar_todos: bool = False,
) -> str:
    """Posta no Discord. Use marcar_todos=True SOMENTE quando Criador pedir para marcar todos."""
    return _json(await post_message(mensagem, canal_id or None, mark_everyone=marcar_todos))

@mcp.tool()
async def discord_postar_imagem(
    caminho: str = "",
    mensagem: str = "",
    canal_id: str = "",
    usar_ultima_imagem: bool = True,
    marcar_todos: bool = False,
) -> str:
    """Posta uma imagem no Discord; por padrão usa a última imagem gerada pela Célia."""
    result = await post_image(
        caminho or None,
        mensagem,
        canal_id or None,
        use_latest=usar_ultima_imagem,
        mark_everyone=marcar_todos,
    )
    if result.get("ok") and not caminho and usar_ultima_imagem:
        latest = latest_generated_image()
        result["latest_image"] = str(latest) if latest else None
    return _json(result)


if __name__ == "__main__":
    mcp.run()
