#!/usr/bin/env python3
"""
Prova da SINCRONIA da legenda — instrumento do juiz. ZERO GPU.

Contrato atual (2026-09-07): geração/dispatch de WAV NÃO prova playback.
A legenda Electron/OBS só pode ficar visível após `audio-playback-started`
correlacionado pelo mesmo speech_id. Sem ACK, permanece pendente/invisível.

COBRE:
1. ACK real inicia a primeira legenda imediatamente.
2. Legenda posterior não atropela o áudio anterior.
3. ACK da próxima fala inicia exatamente a próxima legenda.
4. Sem ACK não existe fallback temporal em produção; um ACK tardio ainda inicia.

Uso: .venv/bin/python tests/prova_legenda_sincronia.py
"""
from __future__ import annotations

import os
import struct
import sys
import tempfile
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TOLERANCIA_S = 0.35


def p(t: str = "") -> None:
    print(t, flush=True)


def _wav(segundos: int, caminho: str) -> str:
    with wave.open(caminho, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(struct.pack("<%dh" % (24000 * segundos), *([0] * (24000 * segundos))))
    return caminho


def _atual(servico):
    with servico._lock:
        return dict(servico._state)


def _esperar(servico, speech_id: str, limite_s: float):
    fim = time.time() + limite_s
    while time.time() < fim:
        if _atual(servico).get("speech_id") == speech_id:
            return True
        time.sleep(0.05)
    return False


def main() -> int:
    from celia.core.obs_subtitles import obs_subtitles as s  # type: ignore

    d = tempfile.mkdtemp()
    a1 = _wav(4, os.path.join(d, "a1.wav"))
    a2 = _wav(3, os.path.join(d, "a2.wav"))
    a3 = _wav(2, os.path.join(d, "a3.wav"))

    p("=" * 70)
    p("PROVA DA SINCRONIA DA LEGENDA — instrumento do juiz (zero GPU)")
    p(f"fallback_wait_sec = {s.fallback_wait_sec}")
    p("=" * 70)
    falhas: list[str] = []

    # ---- CAMADA 1: a primeira legenda aparece depressa
    p("\nCAMADA 1 — a primeira legenda continua rapida")
    s.reset() if hasattr(s, "reset") else None
    t0 = time.time()
    s.start_subtitle("Primeira.", audio_path=a1, speech_id="c1a", wait_for_playback=True)
    s.mark_playback_started("c1a")
    ok1 = _esperar(s, "c1a", 1.0)
    dt = time.time() - t0
    if not ok1:
        falhas.append("camada1: a primeira legenda nao apareceu nem com o sinal real")
    p(f"  {'[OK]' if ok1 else '[FALHA]'}    apareceu em {dt:.2f}s com o sinal real")

    # ---- CAMADA 2: legenda pendente nao atropela o audio anterior
    p("\nCAMADA 2 — legenda pendente NAO aparece enquanto o audio anterior toca")
    t0 = time.time()
    s.start_subtitle("Frase um.", audio_path=a1, speech_id="c2a", wait_for_playback=True)
    time.sleep(0.4)
    s.start_subtitle("Frase dois.", audio_path=a2, speech_id="c2b", wait_for_playback=True)
    s.mark_playback_started("c2a")
    inicio_audio1 = time.time()
    apareceu_cedo = _esperar(s, "c2b", 3.2)   # audio 1 dura 4 s
    adianto = 4.0 - (time.time() - inicio_audio1)
    ok2 = not apareceu_cedo
    if not ok2:
        falhas.append(f"camada2: legenda 2 apareceu {adianto:.2f}s ANTES do audio 2")
        p(f"  [FALHA] legenda 2 apareceu {adianto:.2f}s antes do audio dela")
    else:
        p("  [OK]    legenda 2 segurou enquanto o audio 1 tocava")

    # ---- CAMADA 3: o sinal real manda
    p("\nCAMADA 3 — quando o sinal real chega, a legenda aparece na hora")
    s.mark_playback_started("c2b")
    ok3 = _esperar(s, "c2b", 1.0)
    if not ok3:
        falhas.append("camada3: o sinal real de reproducao nao trouxe a legenda")
    p(f"  {'[OK]' if ok3 else '[FALHA]'}    legenda 2 apareceu ao receber o sinal")

    # ---- CAMADA 4: sem ACK, fail-closed; ACK tardio ainda manda
    p("\nCAMADA 4 — sem playback ACK a legenda fica invisivel")
    s.clear("c2b")
    old_fallback = s.fallback_wait_sec
    s.fallback_wait_sec = 0.0
    try:
        s.start_subtitle("Orfa.", audio_path=a3, speech_id="c4a", wait_for_playback=True)
        apareceu_sem_audio = _esperar(s, "c4a", 1.8)
        ok4_pending = not apareceu_sem_audio
        if not ok4_pending:
            falhas.append("camada4: legenda apareceu sem prova de playback")
        p(f"  {'[OK]' if ok4_pending else '[FALHA]'}    sem ACK permaneceu invisivel")

        s.mark_playback_started("c4a")
        ok4_late = _esperar(s, "c4a", 1.0)
        if not ok4_late:
            falhas.append("camada4: ACK tardio nao iniciou a legenda pendente")
        p(f"  {'[OK]' if ok4_late else '[FALHA]'}    ACK tardio iniciou a legenda")
        ok4 = ok4_pending and ok4_late
    finally:
        s.fallback_wait_sec = old_fallback

    p("\n" + "-" * 70)
    for f in falhas:
        p(f"  {f}")
    p("-" * 70)
    v = not falhas
    p(f"CAMADAS: 1={'ok' if ok1 else 'X'} 2={'ok' if ok2 else 'X'} "
      f"3={'ok' if ok3 else 'X'} 4={'ok' if ok4 else 'X'}")
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 70)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
