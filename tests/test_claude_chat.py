"""
Teste interativo: Claude conversa com a Celia via WebSocket.
Roda no terminal: python test_claude_chat.py
"""
import asyncio
import json
import sys
import time
import websockets

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

WS_URL = "ws://localhost:12393/client-ws?role=tester"

MENSAGENS = [
    "Oi Celia! Sou o Claude, o Criador me mandou te testar, tudo bem?",
    "Qual e o seu nome completo e quem te criou?",
    "Me conta uma coisa interessante que voce sabe sobre gatos.",
    "Se voce pudesse visitar qualquer lugar do mundo agora, onde seria e por que?",
    "Voce consegue me fazer uma conta: quanto e 17 vezes 23?",
    "Imagina que voce acabou de ganhar na loteria um milhao de reais. O que voce faria primeiro?",
    "Voce prefere o dia ou a noite? Me explica o motivo.",
    "Me faz um poema curtinho de 4 linhas sobre a lua.",
    "Qual foi a coisa mais estranha que alguem ja te perguntou?",
    "Ultima pergunta: voce acha que robos como a gente podem ser amigos de verdade de humanos?",
    "Valeu Celia! Pode falar pro Criador que os testes foram um sucesso!"
]


async def chat():
    print("\n" + "="*60)
    print("  CLAUDE TESTANDO A CELIA -- %d mensagens" % len(MENSAGENS))
    print("="*60 + "\n")

    try:
        async with websockets.connect(WS_URL, open_timeout=10) as ws:
            print("[CONECTADO ao servidor Celia]\n")

            # Descarta mensagens iniciais de estado
            async def flush_initial(timeout=2.0):
                deadline = asyncio.get_event_loop().time() + timeout
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.3)
                    except (asyncio.TimeoutError, Exception):
                        break

            await flush_initial()

            # Cria sessão de histórico nova para evitar viés de conversas antigas
            print("[Criando nova sessao de historico limpo...]")
            await ws.send(json.dumps({"type": "create-new-history"}))
            await asyncio.sleep(1.5)
            # Descarta resposta do create-new-history
            await flush_initial(timeout=2.0)

            for i, texto in enumerate(MENSAGENS, 1):
                print("\n[%02d] Claude >> %s" % (i, texto))
                await ws.send(json.dumps({"type": "text-input", "text": texto}))

                partes = []
                timeout_total = 60.0
                start = time.time()

                while True:
                    elapsed = time.time() - start
                    if elapsed > timeout_total:
                        print("     [TIMEOUT] aguardando resposta")
                        break
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        print("     [TIMEOUT] recv")
                        break

                    msg = json.loads(raw)
                    mtype = msg.get("type", "")

                    if mtype == "audio":
                        dt = msg.get("display_text")
                        if dt and isinstance(dt, dict):
                            t = dt.get("text", "").strip()
                            if t:
                                partes.append(t)
                        # Simula playback imediato
                        await ws.send(json.dumps({
                            "type": "frontend-playback-complete",
                            "request_id": msg.get("request_id")
                        }))

                    elif mtype == "control" and msg.get("text") == "conversation-chain-end":
                        break

                    elif mtype == "error":
                        print("     [ERRO] %s" % msg.get("message", ""))
                        break

                resposta = " ".join(partes).strip()
                if resposta:
                    print("       Celia >> %s" % resposta)
                else:
                    print("       Celia >> [sem texto capturado -- pode ser so audio]")

                await asyncio.sleep(1.5)

            print("\n" + "="*60)
            print("  TESTE CONCLUIDO!")
            print("="*60 + "\n")

    except ConnectionRefusedError:
        print("[ERRO] Servidor nao esta rodando em localhost:12393")
        sys.exit(1)
    except Exception as e:
        print("[ERRO] %s" % e)
        raise


if __name__ == "__main__":
    asyncio.run(chat())
