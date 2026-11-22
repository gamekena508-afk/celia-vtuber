#!/usr/bin/env python3
"""
MCP Server - Agente OpenCode Go
Chama agente especializado do OpenCode Go para problemas técnicos complexos.
Usa API OpenCode Zen com modelo deepseek-v4-flash (Go pago) ou deepseek-v4-flash-free.
"""

import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("opencode_agent_mcp")

OPENCODE_API_KEY = os.getenv("OPENCODE_API_KEY", "")
OPENCODE_BASE_URL = "https://opencode.ai/zen/v1"
OPENCODE_MODEL_FREE = "deepseek-v4-flash-free"
OPENCODE_MODEL_PAID = "deepseek-v4-flash"


@mcp.tool()
async def call_opencode_agent(task: str, context: str = "") -> str:
    """
    Chama agente especializado do OpenCode Go para resolver problemas técnicos complexos.
    Use para: debugging, refatoração, arquitetura de código, análise de performance,
    otimização, revisão de código, migração de frameworks, etc.

    task: Descrição clara do problema técnico a resolver
    context: Contexto adicional (linguagem, framework, estrutura do projeto, etc)
    """
    if not OPENCODE_API_KEY:
        return "[ERR] OPENCODE_API_KEY não configurada no .env"

    system_prompt = """Você é um agente técnico especializado em engenharia de software.
Sua função é resolver problemas técnicos complexos com precisão e eficiência.

Diretrizes:
- Responda em PT-BR
- Seja direto e técnico, sem enrolação
- Forneça código quando aplicável
- Explique o raciocínio por trás da solução
- Se precisar de mais informação, pergunte especificamente o que falta
- Priorize soluções práticas e testáveis

Formato de resposta:
1. Diagnóstico do problema (1-2 frases)
2. Solução proposta (código + explicação)
3. Próximos passos (se aplicável)
"""

    user_message = f"**Tarefa:** {task}"
    if context:
        user_message += f"\n\n**Contexto:** {context}"

    payload = {
        "model": OPENCODE_MODEL_FREE,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 4096,
        "temperature": 0.2
    }

    headers = {
        "Authorization": f"Bearer {OPENCODE_API_KEY}",
        "Content-Type": "application/json"
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{OPENCODE_BASE_URL}/chat/completions",
                headers=headers,
                json=payload
            )

            if response.status_code == 429:
                payload["model"] = OPENCODE_MODEL_PAID
                response = await client.post(
                    f"{OPENCODE_BASE_URL}/chat/completions",
                    headers=headers,
                    json=payload
                )

            response.raise_for_status()
            data = response.json()

            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0]["message"].get("content", "")
                if content:
                    return f"[OK] Agente OpenCode Go respondeu:\n\n{content}"
                else:
                    return "[ERR] Resposta vazia do agente"
            else:
                return f"[ERR] Formato de resposta inválido: {data}"

    except httpx.HTTPStatusError as e:
        return f"[ERR] HTTP {e.response.status_code}: {e.response.text[:200]}"
    except httpx.RequestError as e:
        return f"[ERR] Erro de conexão: {e}"
    except Exception as e:
        return f"[ERR] {type(e).__name__}: {e}"


if __name__ == "__main__":
    mcp.run()
