"""
test_tools.py - Testa cada MCP tool individualmente, sem passar pelo LLM.
Confirma se os servidores MCP estão rodando e as tools funcionam.
"""
import asyncio
import sys
from pathlib import Path
import json
sys.path.insert(0, str(Path(__file__).parent))

# Configuração para Windows (evitar erro de encoding com emojis)
if sys.platform == "win32":
    try:
        import os
        # Força UTF-8 no console se possível
        sys.stdout.reconfigure(encoding='utf-8')
    except (AttributeError, ImportError):
        pass

GREEN = "\033[92m"
RED   = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"
BOLD  = "\033[1m"

async def test_mcp_tools():
    from src.celia.mcpp.server_registry import ServerRegistry
    from src.celia.mcpp.mcp_client import MCPClient

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}[MCP] DIAGNOSTICO DE TOOLS MCP{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")

    registry = ServerRegistry()
    servers = list(registry.servers.keys())
    print(f"Servidores registrados: {servers}\n")

    results = {}

    async with MCPClient(registry) as client:
        # ─── 1. Listar tools por servidor ─────────────────────────────
        print(f"{BOLD}📋 TOOLS DISPONÍVEIS:{RESET}")
        for server_name in servers:
            try:
                tools = await client.list_tools(server_name)
                names = [t.name for t in tools]
                print(f"  {GREEN}✅ {server_name}{RESET}: {names}")
                results[server_name] = {"tools": names, "ok": True}
            except Exception as e:
                print(f"  {RED}❌ {server_name}{RESET}: ERRO ao listar tools — {e}")
                results[server_name] = {"tools": [], "ok": False, "error": str(e)}

        print()

        def get_text_from_result(result):
            if not result or "content_items" not in result:
                return "(sem resposta)"
            items = result["content_items"]
            if not items:
                return "(vazio)"
            # Pega o texto do primeiro item que tiver 'text'
            for item in items:
                if "text" in item:
                    return item["text"]
            return str(items[0])

        # ─── 2. Testar open_browser ───────────────────────────────────
        print(f"{BOLD}🌐 TESTE: open_browser (Google){RESET}")
        try:
            result = await client.call_tool(
                "system-automation",
                "open_browser",
                {"url": "https://www.google.com"}
            )
            text = get_text_from_result(result)
            if "❌" in text:
                print(f"  {RED}❌ FALHOU:{RESET} {text}")
            else:
                print(f"  {GREEN}✅ OK:{RESET} {text}")
        except Exception as e:
            print(f"  {RED}❌ EXCEÇÃO:{RESET} {e}")

        print()

        # ─── 3. Testar ddg-search ─────────────────────────────────────
        print(f"{BOLD}🔍 TESTE: search (presidente dos EUA){RESET}")
        try:
            result = await client.call_tool(
                "ddg-search",
                "search",
                {"query": "presidente dos EUA 2025", "max_results": "3"}
            )
            text = get_text_from_result(result)
            if len(text) > 300:
                text = text[:300] + "..."
            print(f"  {GREEN}✅ Resultado:{RESET}\n    {text}")
        except Exception as e:
            print(f"  {RED}❌ EXCEÇÃO:{RESET} {e}")

        print()

        # ─── 4. Testar take_screenshot ───────────────────────────────
        print(f"{BOLD}📸 TESTE: take_screenshot{RESET}")
        try:
            result = await client.call_tool(
                "system-automation",
                "take_screenshot",
                {}
            )
            text = get_text_from_result(result)
            if "❌" in text:
                print(f"  {RED}❌ FALHOU:{RESET} {text}")
            else:
                print(f"  {GREEN}✅ OK:{RESET} {text}")
        except Exception as e:
            print(f"  {RED}❌ EXCEÇÃO:{RESET} {e}")

        print()

        # ─── 5. Testar execute_command ───────────────────────────────
        print(f"{BOLD}⚡ TESTE: execute_command (echo){RESET}")
        try:
            result = await client.call_tool(
                "system-automation",
                "execute_command",
                {"command": "echo test_tools_ok"}
            )
            text = get_text_from_result(result)
            if "❌" in text:
                print(f"  {RED}❌ FALHOU:{RESET} {text}")
            else:
                print(f"  {GREEN}✅ OK:{RESET} {text}")
        except Exception as e:
            print(f"  {RED}❌ EXCEÇÃO:{RESET} {e}")

    print(f"\n{BOLD}{'='*60}{RESET}")
    all_ok = all(v["ok"] for v in results.values())
    if all_ok:
        print(f"{GREEN}{BOLD}✅ TODOS OS SERVIDORES OK — As tools estão funcionando!{RESET}")
    else:
        failed = [k for k, v in results.items() if not v["ok"]]
        print(f"{RED}{BOLD}❌ SERVIDORES COM PROBLEMA: {failed}{RESET}")
    print(f"{BOLD}{'='*60}{RESET}\n")


if __name__ == "__main__":
    asyncio.run(test_mcp_tools())
