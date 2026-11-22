#!/usr/bin/env python3
"""
Prova de funcionamento da Célia — conversa real pelo WebSocket.

Diferente de um teste de fumaça, este script GRAVA EVIDÊNCIA:
  * o texto que a Célia respondeu;
  * o áudio TTS que ela gerou (WAV decodificado do base64), salvo em disco;
  * quais ferramentas MCP ela chamou;
  * a emoção/expressão enviada pro Live2D.

Uso:
    python prova_celia.py --out /caminho/das/provas [--url ws://127.0.0.1:12393/client-ws]
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import sys
import time
from pathlib import Path

import websockets

PERGUNTAS = [
    "Oi Célia! Se apresenta rapidinho em uma frase, por favor.",
    "Célia, que horas são agora? Use a ferramenta de sistema se precisar.",
]

TIMEOUT_POR_TURNO = 180.0


async def um_turno(ws, pergunta: str, indice: int, out_dir: Path) -> dict:
    """Envia uma pergunta e coleta tudo que a Célia devolver até o fim da cadeia."""
    await ws.send(json.dumps({"type": "text-input", "text": pergunta, "speaker_name": "Criador"}))
    inicio = time.perf_counter()

    evidencia: dict = {
        "pergunta": pergunta,
        "texto": [],
        "tipos_recebidos": [],
        "ferramentas": [],
        "expressoes": [],
        "audios": [],
        "primeira_resposta_s": None,
    }

    while True:
        restante = TIMEOUT_POR_TURNO - (time.perf_counter() - inicio)
        if restante <= 0:
            evidencia["erro"] = "timeout aguardando conversation-chain-end"
            return evidencia
        try:
            bruto = await asyncio.wait_for(ws.recv(), timeout=restante)
        except asyncio.TimeoutError:
            evidencia["erro"] = "timeout na recepção"
            return evidencia

        msg = json.loads(bruto)
        tipo = msg.get("type")
        if tipo not in evidencia["tipos_recebidos"]:
            evidencia["tipos_recebidos"].append(tipo)

        if tipo == "text" and msg.get("text"):
            if evidencia["primeira_resposta_s"] is None:
                evidencia["primeira_resposta_s"] = round(time.perf_counter() - inicio, 2)
            evidencia["texto"].append(msg["text"])

        # Áudio TTS: o backend manda base64; gravamos como arquivo de verdade.
        if tipo == "audio" and msg.get("audio"):
            dados = base64.b64decode(msg["audio"])
            destino = out_dir / f"celia_turno{indice}_audio{len(evidencia['audios']) + 1}.wav"
            destino.write_bytes(dados)
            evidencia["audios"].append({"arquivo": str(destino), "bytes": len(dados)})
            if msg.get("display_text", {}).get("text"):
                texto = msg["display_text"]["text"]
                if texto not in evidencia["texto"]:
                    evidencia["texto"].append(texto)
            for exp in (msg.get("actions") or {}).get("expressions") or []:
                evidencia["expressoes"].append(exp)

        if tipo in ("tool-call-start", "tool_call", "mcp-tool-call"):
            evidencia["ferramentas"].append(msg.get("name") or msg.get("tool") or str(msg)[:120])

        if tipo == "backend-synth-complete":
            await ws.send(json.dumps({"type": "frontend-playback-complete"}))

        if tipo == "control" and msg.get("text") == "conversation-chain-end":
            evidencia["duracao_s"] = round(time.perf_counter() - inicio, 2)
            return evidencia


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:12393/client-ws?role=source")
    ap.add_argument("--out", default="var/provas-celia")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    resultados = []
    async with websockets.connect(args.url, max_size=64 * 1024 * 1024) as ws:
        print(f"conectado: {args.url}")
        for i, pergunta in enumerate(PERGUNTAS, start=1):
            print(f"\n--- turno {i}: {pergunta}")
            ev = await um_turno(ws, pergunta, i, out_dir)
            resultados.append(ev)
            print("  texto      :", " ".join(ev["texto"])[:400] or "(vazio)")
            print("  áudios     :", ev["audios"])
            print("  ferramentas:", ev["ferramentas"])
            print("  expressões :", ev["expressoes"])
            print("  tipos      :", ev["tipos_recebidos"])
            if ev.get("erro"):
                print("  ERRO       :", ev["erro"])

    destino = out_dir / "30_prova_celia.json"
    destino.write_text(json.dumps(resultados, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nevidência salva em {destino}")

    ok = all(r["texto"] and not r.get("erro") for r in resultados)
    print("RESULTADO:", "PASSOU" if ok else "FALHOU")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
