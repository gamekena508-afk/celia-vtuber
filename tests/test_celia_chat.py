"""
test_celia_chat.py - Testes de conversa automatizada com a Celia.
Uso: python test_celia_chat.py
Requer o servidor rodando: python run_server.py
"""

import asyncio
import io
import json
import sys
import time

import websockets

def _forcar_utf8_no_stdout() -> None:
    """Forca UTF-8 no stdout para evitar UnicodeEncodeError no Windows.

    Fica fora do import: sob pytest, sys.stdout e o arquivo de captura, e
    envolve-lo aqui fecha o buffer e derruba a sessao inteira com
    "ValueError: I/O operation on closed file" — nenhum teste chega a rodar.
    """
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

WS_URL = "ws://localhost:12393/client-ws?role=source"
SEGMENT_TIMEOUT = 8.0    # segundos sem novo segmento = fim da resposta
RESPONSE_TIMEOUT = 60.0  # timeout máximo total por teste

TESTES = [
    "oi Célia, criador mandou eu te testar, vamos conversar!",
    "quem você é e o que você faz?",
    "me conta sobre o Criador",
    "você tem irmã? me conta sobre ela",
    "qual é a capital do brasil?",
    "que horas são agora?",
    "me conta uma piada",
    "essa piada foi boa! conta outra",
    "você sabe programar? qual linguagem você gosta mais?",
    "qual seu filme favorito?",
    "você tá rodando bem hoje? sem travamentos?",
    "tudo bem, foi bom conversar. até mais!",
]


MENSAGENS_SISTEMA = {"Thinking...", "Connection established", "AI wants to speak something..."}

def _extrair_texto(data: dict) -> str:
    """Extrai texto de qualquer tipo de mensagem da Célia."""
    tipo = data.get("type", "")
    if tipo == "audio":
        dt = data.get("display_text") or {}
        return (dt.get("text") or "").strip()
    if tipo in ("full-text", "agent-message-segment"):
        return (data.get("text") or "").strip()
    return ""


async def coletar_resposta(ws, timeout_total: float, timeout_silencio: float) -> list:
    """
    Coleta segmentos de resposta da Célia.
    - backend-synth-complete indica que o LLM terminou, mas TTS ainda envia payloads
      em background — por isso continuamos ouvindo por mais `timeout_silencio` segundos.
    - Mensagens de sistema (Thinking...) são capturadas separadamente.
    """
    segmentos = []       # respostas reais da Célia
    synth_done = False   # backend-synth-complete recebido
    deadline = time.time() + timeout_total
    while time.time() < deadline:
        restante = deadline - time.time()
        # Após synth-complete, reduzimos o silêncio para capturar os áudios tardios
        espera = min(timeout_silencio / 2 if synth_done else timeout_silencio, restante)
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=espera)
            data = json.loads(raw)
            tipo = data.get("type", "")
            if tipo == "backend-synth-complete":
                synth_done = True
                continue
            texto = _extrair_texto(data)
            if texto and texto not in MENSAGENS_SISTEMA:
                segmentos.append(texto)
                print(f"     Célia: {texto}")
            elif texto in MENSAGENS_SISTEMA:
                print(f"     [sistema] {texto}")
        except asyncio.TimeoutError:
            # Silêncio após synth-complete = fim real da resposta
            if synth_done:
                break
            # Silêncio antes de qualquer synth-complete = ainda esperando LLM
    return segmentos


async def rodar_testes() -> bool:
    print(f"\n{'=' * 62}")
    print("   TESTES DE CONVERSA COM A CÉLIA")
    print(f"{'=' * 62}\n")

    resultados: list = []

    try:
        async with websockets.connect(WS_URL, open_timeout=10) as ws:
            print(f"Conectado em {WS_URL}\n")

            for i, mensagem in enumerate(TESTES, 1):
                input_final = f"Claude: {mensagem}"
                print(f"[TESTE {i:02d}] {input_final}")
                print(f"  {'-' * 56}")

                await ws.send(json.dumps({"type": "text-input", "text": input_final}))
                t0 = time.time()

                segmentos = await coletar_resposta(ws, RESPONSE_TIMEOUT, SEGMENT_TIMEOUT)
                elapsed = time.time() - t0

                passou = len(segmentos) > 0
                status = "PASS" if passou else "FAIL"
                resultados.append((i, mensagem[:45], passou, elapsed, segmentos))
                print(f"  [{status}] {elapsed:.1f}s — {len(segmentos)} segmento(s)\n")

                # Heartbeat entre testes para manter conexão
                await ws.send(json.dumps({"type": "heartbeat"}))
                await asyncio.sleep(1.5)

    except (OSError, websockets.exceptions.WebSocketException) as e:
        print(f"\n[ERRO] Não foi possível conectar ao servidor: {e}")
        print("  -> Certifique-se de que o servidor está rodando: python run_server.py")
        return False

    # Resumo final
    print(f"\n{'=' * 62}")
    print("   RESUMO")
    print(f"{'=' * 62}")
    falhas = 0
    for i, msg, passou, elapsed, _ in resultados:
        status = "OK" if passou else "XX"
        print(f"  [{status}] Teste {i:02d}: {msg}... ({elapsed:.1f}s)")
        if not passou:
            falhas += 1

    total = len(TESTES)
    print(f"\n  {total - falhas}/{total} testes passaram")

    if falhas > 0:
        print(f"\n  ATENCAO: {falhas} falha(s) detectada(s).")
        print("  Verifique: servidor rodando, NVIDIA_API_KEY valida, LLM acessivel.")
        return False

    print("\n  TODOS OS TESTES PASSARAM!")
    return True


if __name__ == "__main__":
    _forcar_utf8_no_stdout()
    ok = asyncio.run(rodar_testes())
    sys.exit(0 if ok else 1)
