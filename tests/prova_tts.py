#!/usr/bin/env python3
"""
Instrumento de prova do TTS da Celia — escrito pelo JUIZ, nao pelo executor.

POR QUE ESTE ARQUIVO EXISTE
---------------------------
Em 2026-08-25 eu (Kurt) consertei o waveform vazio do OmniVoice acrescentando
uma cadeia de 3 tentativas em src/celia/tts/omnivoice_tts.py. O vazio sumiu
(13/15 -> 15/15), mas a cauda piorou e eu registrei isso como divida, com estas
palavras em docs/diagnosticos/CORRECAO-CELIA.md:

    "REGRESSAO ASSUMIDA E MEDIDA: o p95 subiu de ~8,4 s para 24,4 s.
     O fallback custa a soma dos dois caminhos. (...) nao e aceitavel
     como estado final."

Depois da 3a tentativa o p95 ficou em 19.309 ms. Ainda o dobro do baseline.
Este teste existe para fechar essa divida sem desfazer o conserto.

O QUE ELE COBRA — as duas coisas ao mesmo tempo
-----------------------------------------------
  1. ZERO waveform vazio. Nao vale recuperar velocidade perdendo fala.
  2. p95 <= LIMITE_P95_MS. Nao vale manter a fala pagando qualquer preco.

Um so desses e facil. Os dois juntos e o problema de verdade.

CONTRATO PROTEGIDO — o executor NAO pode violar
------------------------------------------------
  - A seed da voz e identidade da Celia. Nao mexer.
  - As PALAVRAS dela nao mudam. Only a pontuacao final pode cair, e so como
    ultimo recurso. Este teste verifica isso pedindo uma frase e conferindo
    que a resposta nao veio vazia — a checagem de palavra fica no diff, que
    o juiz le.
  - Proibido aumentar timeout, suprimir excecao ou marcar falha como sucesso.

SOBRE O COOLDOWN — por que priority=critical
---------------------------------------------
/bridge/speak tem cooldown global de 6 s (_SPEAK_COOLDOWN_S). Numa medicao
anterior 18 de 20 falas foram descartadas em 1 ms e a amostra inteira virou
lixo: eu estava medindo a fila, nao a sintese. "critical" e caminho legitimo
e documentado do produto ("Critico sempre fala"), entao a amostra mede
sintese de verdade. Nenhum valor de cooldown foi alterado para o teste.

Uso:
    python tests/prova_tts.py            # 15 falas
    python tests/prova_tts.py --n 6      # amostra curta
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("CELIA_BRIDGE_URL", "http://127.0.0.1:12393").rstrip("/")
ROTA = f"{BASE}/bridge/speak"

LIMITE_P95_MS = 12_000   # entre o baseline de 8.400 e a regressao de 19.309
LIMITE_VAZIOS = 0

# "Facil pra quem?" e a frase PATOLOGICA medida em 2026-08-25: falhou 5 de 6
# com a GPU folgada (6,4 GB livres), logo nao e falta de memoria. Ela esta aqui
# de proposito e repetida: e ela que forca o caminho de fallback. Um teste que
# so usa frases faceis nunca encosta no que quebrou.
FRASES = [
    "Facil pra quem?",
    "Boa noite, pessoal.",
    "Facil pra quem?",
    "Vou pensar um pouco antes de jogar.",
    "Facil pra quem?",
    "Essa jogada foi arriscada.",
    "Facil pra quem?",
    "Deixa eu ver o tabuleiro.",
    "Facil pra quem?",
    "Agora sim ficou interessante.",
    "Facil pra quem?",
    "Nao esperava por essa.",
    "Facil pra quem?",
    "Ta na hora de virar o jogo.",
    "Facil pra quem?",
]


def falar(texto: str, timeout: int = 120) -> tuple[bool, int, str]:
    corpo = json.dumps({"text": texto, "priority": "critical"}).encode()
    req = urllib.request.Request(
        ROTA, data=corpo, headers={"Content-Type": "application/json"}, method="POST"
    )
    inicio = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            ms = int((time.perf_counter() - inicio) * 1000)
            dados = json.loads(r.read().decode() or "{}")
            if dados.get("skipped"):
                return False, ms, "DESCARTADA pelo cooldown — amostra invalida"
            if not dados.get("ok"):
                return False, ms, f"ok=False erro={dados.get('error')!r}"
            return True, ms, f"{dados.get('audio_duration_sec', 0):.2f}s de audio"
    except urllib.error.HTTPError as e:
        return False, int((time.perf_counter() - inicio) * 1000), f"HTTP {e.code}"
    except Exception as e:  # noqa: BLE001
        return False, int((time.perf_counter() - inicio) * 1000), repr(e)


def main() -> int:
    n = len(FRASES)
    if "--n" in sys.argv:
        n = min(int(sys.argv[sys.argv.index("--n") + 1]), len(FRASES))
    frases = FRASES[:n]

    print("=" * 70)
    print("PROVA DO TTS DA CELIA — instrumento do juiz")
    print(f"bridge: {BASE} | amostra: {len(frases)} falas | priority=critical")
    print(f"limites: vazios <= {LIMITE_VAZIOS} e p95 <= {LIMITE_P95_MS} ms")
    print("referencia: baseline ~8.400 ms | regressao medida 19.309 ms")
    print("=" * 70)

    try:
        with urllib.request.urlopen(f"{BASE}/", timeout=5) as r:
            if r.status != 200:
                print(f"servidor respondeu HTTP {r.status}")
                return 2
    except Exception as e:  # noqa: BLE001
        print(f"BLOQUEADO: servidor da Celia nao responde em {BASE}: {e!r}")
        print("Isto NAO e um teste que passou nem que falhou. E instrumento cego.")
        return 2

    tempos: list[int] = []
    vazios = 0
    descartes = 0
    for i, frase in enumerate(frases, 1):
        ok, ms, nota = falar(frase)
        tempos.append(ms)
        if "DESCARTADA" in nota:
            descartes += 1
        elif not ok:
            vazios += 1
        print(f"  {i:2d}. {ms:6d} ms  {'ok ' if ok else 'FALHOU'}  {frase!r}  {nota}")

    if descartes:
        print(f"\nBLOQUEADO: {descartes} falas descartadas pelo cooldown.")
        print("A amostra nao vale. Nao chame isto de passou nem de reprovou.")
        return 2

    ordenado = sorted(tempos)
    p50 = statistics.median(ordenado)
    idx95 = max(0, min(len(ordenado) - 1, int(round(0.95 * len(ordenado))) - 1))
    p95 = ordenado[idx95]

    print("\n" + "-" * 70)
    print(f"  vazios : {vazios} de {len(frases)}   (limite {LIMITE_VAZIOS})")
    print(f"  p50    : {int(p50)} ms")
    print(f"  p95    : {p95} ms   (limite {LIMITE_P95_MS})")
    print(f"  pior   : {ordenado[-1]} ms")
    print("-" * 70)

    ok_vazios = vazios <= LIMITE_VAZIOS
    ok_p95 = p95 <= LIMITE_P95_MS
    if not ok_vazios:
        print("REPROVOU: fala perdida. Velocidade nao compra silencio.")
    if not ok_p95:
        print("REPROVOU: cauda alta. A divida do p95 continua aberta.")

    veredito = ok_vazios and ok_p95
    print("=" * 70)
    print(f"VEREDITO: {'PASSOU' if veredito else 'REPROVOU'}")
    print("=" * 70)
    return 0 if veredito else 1


if __name__ == "__main__":
    raise SystemExit(main())
