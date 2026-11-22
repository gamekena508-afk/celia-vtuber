#!/usr/bin/env python3
"""
Discord Text Bot — Integração Célia/Luna
Fluxo: Discord → /api/luna/chat (porta 5050) → resposta + debug emoção
Variáveis: DISCORD_TOKEN, DISCORD_CHAT_CHANNEL_ID (opcional, para restringir canal)
"""

import os
import asyncio
import aiohttp
from pathlib import Path

LUNA_CHAT_URL = os.environ.get("LUNA_CHAT_URL", "http://localhost:5050/api/luna/chat")
DISCORD_TOKEN = os.environ.get("DISCORD_TOKEN", "")
DISCORD_CHAT_CHANNEL_ID = int(os.environ.get("DISCORD_CHAT_CHANNEL_ID", "0"))
SHOW_EMOTION_DEBUG = os.environ.get("SHOW_EMOTION_DEBUG", "1") == "1"


async def _chamar_celia(mensagem: str, usuario: str = "Discord") -> tuple[str, str]:
    """Chama /api/luna/chat e retorna (resposta, debug_emocao)."""
    payload = {"message": mensagem, "user": usuario}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(LUNA_CHAT_URL, json=payload, timeout=aiohttp.ClientTimeout(total=30)) as r:
                if r.status == 200:
                    data = await r.json()
                    resposta = data.get("response") or data.get("text") or str(data)
                    emocao = data.get("emotion", "")
                    return resposta, emocao
                return f"[ERR] Servidor retornou {r.status}", ""
    except aiohttp.ClientConnectorError:
        return "[ERR] Luna não está rodando (porta 5050)", ""
    except Exception as e:
        return f"[ERR] {e}", ""


def _debug_emocao() -> str:
    """Retorna debug de emoção formatado."""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))
        from celia.core.emotion_debug import format_emotion_debug
        return format_emotion_debug()
    except Exception:
        return ""


async def iniciar_bot():
    try:
        import discord
        from discord.ext import commands
    except ImportError:
        print("[BotChat] discord.py não instalado. Execute: pip install discord.py")
        return

    if not DISCORD_TOKEN:
        print("[BotChat] DISCORD_TOKEN não definido!")
        return

    intents = discord.Intents.default()
    intents.message_content = True
    bot = commands.Bot(command_prefix="!", intents=intents)

    @bot.event
    async def on_ready():
        print(f"[BotChat] Conectado como {bot.user}")
        canal_info = f" | Canal restrito: {DISCORD_CHAT_CHANNEL_ID}" if DISCORD_CHAT_CHANNEL_ID else " | Todos os canais"
        print(f"[BotChat] Modo: texto{canal_info}")

    @bot.event
    async def on_message(message):
        if message.author.bot:
            return
        if DISCORD_CHAT_CHANNEL_ID and message.channel.id != DISCORD_CHAT_CHANNEL_ID:
            return
        if message.content.startswith("!"):
            await bot.process_commands(message)
            return

        async with message.channel.typing():
            resposta, emocao = await _chamar_celia(message.content, str(message.author))

        if not resposta:
            return

        # Monta resposta com debug opcional
        conteudo = resposta
        if SHOW_EMOTION_DEBUG:
            debug = _debug_emocao()
            if debug:
                conteudo += f"\n\n```\n{debug}\n```"
            elif emocao:
                conteudo += f"\n\n🎭 Emoção: `{emocao}`"

        # Discord limita a 2000 chars por mensagem
        for i in range(0, len(conteudo), 1990):
            await message.reply(conteudo[i:i+1990])

    @bot.command(name="debug")
    async def cmd_debug(ctx):
        """Mostra o estado emocional atual da Célia."""
        debug = _debug_emocao()
        await ctx.send(f"```\n{debug or 'Debug indisponível'}\n```")

    @bot.command(name="celia")
    async def cmd_celia(ctx, *, texto: str):
        """Envia mensagem diretamente para a Célia."""
        async with ctx.typing():
            resposta, emocao = await _chamar_celia(texto, str(ctx.author))
        await ctx.reply(resposta or "[sem resposta]")

    await bot.start(DISCORD_TOKEN)


if __name__ == "__main__":
    asyncio.run(iniciar_bot())
