#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MCP de imagem da Célia — roteador unificado GPT / Meta / Flow / Grok Build."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from mcp.server.fastmcp import FastMCP

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from celia.integrations.image_generation import ImageRequest, ImageRouter

mcp = FastMCP("edicao_de_imagem")
router = ImageRouter()


async def _payload(result, requested_provider: str) -> str:
    data = result.to_dict()
    data["requested_provider"] = requested_provider
    if result.ok:
        data["mensagem_para_celia"] = (
            "Pronto, Criador. Terminei. "
            "Os caminhos em output_paths são metadados internos, não devem ser narrados."
        )
    else:
        health = await router.health_all()
        attempted = {
            str(attempt.provider)
            for attempt in (result.attempts or [])
            if getattr(attempt, "provider", None)
        }
        alternatives = [
            name for name, status in health.items()
            if name != requested_provider and name not in attempted and status.get("ok")
        ]
        data["alternativas_disponiveis"] = alternatives
        if requested_provider != "auto":
            data["mensagem_para_celia"] = (
                f"O gerador {requested_provider} falhou: {result.error}. "
                + (f"Alternativas disponíveis: {', '.join(alternatives)}." if alternatives else "Nenhuma alternativa saudável foi detectada agora.")
            )
        else:
            data["mensagem_para_celia"] = f"A geração falhou depois das tentativas automáticas: {result.error}."
    return json.dumps(data, ensure_ascii=False, indent=2)


@mcp.tool()
async def criar_ou_editar_imagem(
    pedido: str,
    provider: str = "auto",
    referencias: Optional[list[str]] = None,
    identidades: Optional[list[str]] = None,
    usar_clipboard: bool = False,
    proporcao: str = "1:1",
    abrir: bool = True,
    permitir_fallback: bool = True,
) -> str:
    """Cria ou edita imagem com GPT, Meta, Flow ou Grok Build.

    O próprio pedido pode nomear o provider (GPT, Meta, Flow, Grok). Se não nomear,
    o roteador escolhe automaticamente. `referencias` aceita até 4 caminhos locais.
    `identidades` pode conter criador_slime, criador_human e/ou celia.
    `usar_clipboard=True` torna a imagem do clipboard a base principal da edição.
    """
    provider = provider.lower().strip()
    if provider not in {"auto", "gpt", "meta", "flow", "grok"}:
        return json.dumps({"ok": False, "error_code": "provider_unknown", "error": f"Provider desconhecido: {provider}"}, ensure_ascii=False)
    request = ImageRequest(
        prompt=pedido,
        provider=provider,
        identities=list(identidades or []),
        reference_paths=list(referencias or []),
        use_clipboard=usar_clipboard,
        aspect_ratio=proporcao,
        open_after=abrir,
        allow_auto_fallback=permitir_fallback,
    )
    result = await router.execute(request)
    return await _payload(result, provider)


@mcp.tool()
async def gerar_imagem(
    prompt: str,
    proporcao: str = "1:1",
    resolucao: str = "1k",
    qualidade: str = "basico",
    provider: str = "auto",
) -> str:
    """Compatibilidade: gera imagem nova pelo roteador multi-provider."""
    pedido = prompt
    if resolucao or qualidade:
        pedido += f"\nPreferência técnica: resolução {resolucao}; qualidade {qualidade}."
    result = await router.execute(ImageRequest(
        prompt=pedido,
        provider=provider if provider in {"auto", "gpt", "meta", "flow", "grok"} else "auto",
        aspect_ratio=proporcao,
        open_after=True,
    ))
    return await _payload(result, provider)


@mcp.tool()
async def editar_imagem(
    prompt: str,
    proporcao: str = "1:1",
    qualidade: str = "basico",
    provider: str = "auto",
) -> str:
    """Compatibilidade: edita a imagem do clipboard pelo roteador multi-provider."""
    pedido = prompt + f"\nPreferência técnica: qualidade {qualidade}."
    result = await router.execute(ImageRequest(
        prompt=pedido,
        provider=provider if provider in {"auto", "gpt", "meta", "flow", "grok"} else "auto",
        use_clipboard=True,
        aspect_ratio=proporcao,
        open_after=True,
    ))
    return await _payload(result, provider)


@mcp.tool()
async def status_geradores_imagem() -> str:
    """Retorna health check dos quatro geradores sem fazer geração."""
    return json.dumps(await router.health_all(), ensure_ascii=False, indent=2)


if __name__ == "__main__":
    mcp.run()
