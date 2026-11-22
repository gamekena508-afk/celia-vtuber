#!/usr/bin/env python3
"""
Prova do divisor em MODO STREAMING — instrumento do juiz. ZERO GPU.

POR QUE
-------
A Celia nao recebe a fala pronta: o modelo entrega em pedacos, e a classe
`SentenceDivider.process_stream` monta as frases enquanto os pedacos chegam.
**Nenhum teste do pytest cobre esse caminho** — medido em 2026-08-27, os 263
testes da suite passam com o defeito abaixo vivo.

DEFEITO MEDIDO em 2026-08-27, exercitando a classe a mao:

    'Oi Pai, tudo bem?'           pedaco=7   ->  ['... Oi Pai,', 'tudobem?']
    'Bom dia Pai, vamos jogar'    pedaco=25  ->  [..., 'vamosjogar', ...]
    'Escuta, eu queria muito'     pedaco=11  ->  ['escuta', 'euqueria', ...]

Quando a fronteira do pedaco cai EXATAMENTE num espaco, o espaco se perde e as
palavras GRUDAM. Na voz isso sai embolado.

Classificado antes de acusar qualquer um: varredura de 5 textos x 24 tamanhos
de pedaco deu **11 combinacoes ruins de 120** tanto no codigo novo quanto no
anterior. E defeito PRE-EXISTENTE, nao regressao — a correcao do divisor so
deslocou quais combinacoes batem nele.

CONTRATO
--------
Para todo texto e todo tamanho de pedaco, a sequencia de palavras emitida pelo
streaming e EXATAMENTE a sequencia de palavras do texto original. Nem palavra
grudada, nem palavra perdida, nem palavra a mais.

O corte em frases pode variar com o tamanho do pedaco — isso e legitimo. O que
nao pode variar e o CONTEUDO.

Uso:  .venv/bin/python tests/prova_tts_streaming.py
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

TEXTOS = [
    "(ela pensa. depois responde) Oi Pai, tudo bem?",
    "Oi Pai, tudo bem com voce hoje?",
    "Bom dia Pai, vamos jogar uma partida agora?",
    "Escuta, eu queria muito jogar contigo hoje a noite.",
    "Olha so (isso e serio. muito serio) o que aconteceu, tudo bem?",
    "Ganhei! Foi por pouco, mas ganhei. Bora de novo, coroa?",
]
PEDACOS = range(2, 26)


def p(t: str = "") -> None:
    print(t, flush=True)


def palavras(t: str) -> list[str]:
    return re.findall(r"\w+", t.lower())


async def _stream(texto: str, n: int) -> list[str]:
    from celia.utils.sentence_divider import SentenceDivider  # type: ignore

    divisor = SentenceDivider(faster_first_response=True, segment_method="pysbd")

    async def fonte():
        for i in range(0, len(texto), n):
            yield texto[i : i + n]

    return [s.text async for s in divisor.process_stream(fonte())]


def main() -> int:
    p("=" * 72)
    p("PROVA DO DIVISOR EM STREAMING — instrumento do juiz (zero GPU)")
    p(f"{len(TEXTOS)} textos x {len(list(PEDACOS))} tamanhos de pedaco")
    p("=" * 72)

    ruins: list[str] = []
    total = 0
    for texto in TEXTOS:
        esperado = palavras(texto)
        falhas_deste = []
        for n in PEDACOS:
            total += 1
            obtido: list[str] = []
            for pedaco in asyncio.run(_stream(texto, n)):
                obtido += palavras(pedaco)
            if obtido != esperado:
                grudadas = [w for w in obtido if w not in esperado]
                perdidas = [w for w in esperado if w not in obtido]
                falhas_deste.append((n, grudadas, perdidas))
                ruins.append(f"{texto[:38]!r} pedaco={n}: grudou {grudadas} perdeu {perdidas}")
        if falhas_deste:
            p(f"\n  [FALHA] {texto!r}")
            for n, g, pd in falhas_deste[:4]:
                p(f"          pedaco={n:2d} grudou {g} perdeu {pd}")
            if len(falhas_deste) > 4:
                p(f"          (+{len(falhas_deste) - 4} outros tamanhos)")
        else:
            p(f"  [OK]    {texto!r}")

    p("\n" + "-" * 72)
    p(f"  combinacoes ruins: {len(ruins)} de {total}")
    p("-" * 72)
    v = not ruins
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 72)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
