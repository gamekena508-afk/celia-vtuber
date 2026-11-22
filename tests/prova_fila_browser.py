"""Prova em navegador real: uma fala nunca toca por cima de outra.

Carrega frontend/external-playback-bridge.js num Chromium de verdade, cria WAVs
reais (tom de 0,9 s) e toca com o HTMLAudioElement de verdade - o relógio de
mídia é o do navegador, não simulado. Reproduz o traço de 25/09 20:29:
  A toca -> B chega na fila -> conversation-chain-start (o bundle zera a fila
  com A ainda tocando) -> C chega e o bundle a inicia na hora.

uso: python tests/prova_fila_browser.py [--original CAMINHO_DO_BRIDGE_ANTIGO]
Sem --original: só valida o bridge atual (exit 0 = uma voz por vez).
Com --original: roda também o antigo e mostra o antes/depois.
"""
import argparse
import base64
import io
import json
import math
import struct
import sys
import wave
from pathlib import Path

from playwright.sync_api import sync_playwright

RAIZ = Path(__file__).resolve().parents[1]
ATUAL = RAIZ / "frontend" / "external-playback-bridge.js"


def tom_wav_b64(segundos: float, hz: float) -> str:
    taxa = 24000
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(taxa)
        w.writeframes(b"".join(
            struct.pack("<h", int(8000 * math.sin(2 * math.pi * hz * i / taxa)))
            for i in range(int(taxa * segundos))))
    return base64.b64encode(buf.getvalue()).decode()


DRIVER = """
async ({audios}) => {
  const ws = new WebSocket("ws://127.0.0.1:9/nunca-abre");    // só recebe eventos sintéticos
  const t0 = performance.now(), stamp = () => Math.round(performance.now() - t0);
  const live = new Set(), tempo = [];
  let pico = 0;
  const emit = p => ws.dispatchEvent(new MessageEvent("message", {data: JSON.stringify(p)}));
  const dormir = ms => new Promise(r => setTimeout(r, ms));
  // igual à tarefa do bundle: queueSpeech -> waitForPlayable -> new Audio -> play no canplaythrough
  async function tarefa(id) {
    if (!celiaPlayback.queueSpeech(id)) return;
    if (!(await celiaPlayback.waitForPlayable(id))) return;
    await new Promise(fim => {
      const a = new Audio("data:audio/wav;base64," + audios[id], id);
      a.addEventListener("playing", () => { live.add(id); pico = Math.max(pico, live.size); tempo.push([stamp(), "toca", id, [...live].join("+")]); });
      a.addEventListener("ended", () => { live.delete(id); tempo.push([stamp(), "fim", id, [...live].join("+")]); fim(); });
      a.addEventListener("canplaythrough", () => a.play().catch(() => fim()));
      a.load();
    });
  }
  const chega = id => { emit({type: "audio", speech_id: id, audio: audios[id]}); return tarefa(id); };

  chega("A");                                                  // toca (0,9 s)
  await dormir(250);
  emit({type: "audio", speech_id: "B", audio: audios.B});      // B está na fila do bundle...
  celiaPlayback.queueSpeech("B");                              // ...sem tarefa iniciada
  emit({type: "control", text: "conversation-chain-start"});   // o bundle zera a fila aqui
  chega("C");                                                  // e inicia a próxima na hora
  await dormir(2600);
  return {pico, tempo, eventos: celiaPlayback.events.map(e => [e.event, e.speech_id, e.reason])};
}
"""


def rodar(bridge: Path) -> dict:
    audios = {"A": tom_wav_b64(0.9, 440), "B": tom_wav_b64(0.9, 550), "C": tom_wav_b64(0.9, 660)}
    with sync_playwright() as p:
        nav = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required", "--mute-audio"])
        pagina = nav.new_page()
        pagina.set_content("<html><body></body></html>")
        pagina.add_script_tag(path=str(bridge))
        resultado = pagina.evaluate(DRIVER, {"audios": audios})
        nav.close()
    return resultado


def mostrar(nome: str, r: dict) -> None:
    print(f"--- {nome}: pico de vozes = {r['pico']}")
    for t, ev, i, ao_vivo in r["tempo"]:
        print(f"   {t:5d} ms  {ev:4s} {i}   (tocando agora: {ao_vivo or '-'})")
    for ev, i, motivo in r["eventos"]:
        if ev in ("AUDIO_CANCELLED", "AUDIO_DEFERRED", "AUDIO_DROPPED"):
            print(f"   {ev} {i} {motivo}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--original", type=Path, help="bridge antigo, para mostrar o antes")
    args = ap.parse_args()
    if args.original:
        antes = rodar(args.original)
        mostrar("ANTES (bridge original)", antes)
    depois = rodar(ATUAL)
    mostrar("DEPOIS (bridge atual)", depois)
    ok = depois["pico"] == 1 and ["AUDIO_CANCELLED", "B", "new_turn"] in depois["eventos"] \
        and any(e[1] == "toca" and e[2] == "C" for e in depois["tempo"])
    print("RESULTADO:", "PASS - uma voz por vez, B cancelada com motivo, C tocou" if ok else "REPROVADO")
    sys.exit(0 if ok else 1)
