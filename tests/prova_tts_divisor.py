#!/usr/bin/env python3
"""
Prova do DIVISOR de frases + filtro, em conjunto — instrumento do juiz. ZERO GPU.

A CAUSA RAIZ, medida em 2026-08-27 no caminho REAL (conf.yaml diz
`segment_method: pysbd`, e o pysbd se comporta igual ao regex aqui):

    '(ela pensa. depois responde) Oi Pai, tudo bem?'
      pedaco 0: '(ela pensa.'                    -> vira voz: ''
      pedaco 1: 'depois responde) Oi Pai...'     -> 'depois responde Oi Pai...'

    'Olha so (isso e serio. muito serio) o que aconteceu hoje.'
      pedaco 0: 'Olha so (isso e serio.'         -> 'Olha so'
      pedaco 1: 'muito serio) o que aconteceu.'  -> 'muito serio o que aconteceu.'

O divisor corta no ponto que esta DENTRO do parenteses. Cada pedaco fica com
um delimitador orfao, e o filtro entao come do orfao ate o fim. Dois sintomas
da MESMA causa, e sao exatamente os dois que o Pai relatou:

  - texto some  (a fala fica incompleta e cortada)
  - texto que era para ser MUDO e falado (a rubrica vaza para a voz)

CONTRATO QUE ESTE TESTE COBRA
-----------------------------
A. O divisor NAO parte um bloco delimitado ao meio. Nenhum pedaco sai com
   delimitador desbalanceado.
B. Conservacao da fala: as palavras faladas, somando todos os pedacos, sao
   EXATAMENTE as palavras que estavam FORA dos delimitadores no texto
   original. Nem palavra a menos (fala cortada), nem a mais (rubrica falada).
C. Texto normal continua sendo dividido em frases como antes (sem regressao).

Uso:  .venv/bin/python tests/prova_tts_divisor.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

FLAGS = dict(
    remove_special_char=True, ignore_brackets=True, ignore_parentheses=True,
    ignore_asterisks=True, ignore_angle_brackets=True,
)

PARES = [("(", ")"), ("[", "]"), ("<", ">")]

CASOS = [
    "(ela pensa. depois responde) Oi Pai, tudo bem?",
    "Olha so (isso e serio. muito serio) o que aconteceu hoje.",
    "[happy] Que bom te ver! Vamos jogar? [teasing] Vou ganhar.",
    "Bom dia! (ele acena. sorri) Vamos comecar a partida agora.",
    "Espera (calma. respira) que eu ja volto pra jogar contigo.",
]

NORMAIS = [
    "Oi Pai. Tudo bem com voce? Eu queria jogar hoje.",
    "Ganhei a partida! Foi por pouco. Bora de novo?",
]


def p(t: str = "") -> None:
    print(t, flush=True)


def palavras_faladas_esperadas(texto: str) -> list[str]:
    """As palavras que sobram quando se tira TODO bloco delimitado fechado."""
    fora = texto
    for a, b in PARES:
        fora = re.sub(re.escape(a) + r"[^" + re.escape(a + b) + r"]*" + re.escape(b),
                      " ", fora)
    fora = re.sub(r"\*[^*]*\*", " ", fora)
    return re.findall(r"\w+", fora.lower())


def desbalanceado(fragmento: str) -> str | None:
    for a, b in PARES:
        if fragmento.count(a) != fragmento.count(b):
            return f"{a}{b}"
    if fragmento.count("*") % 2:
        return "**"
    return None


def main() -> int:
    from celia.utils.sentence_divider import segment_text_by_pysbd  # type: ignore
    from celia.utils.tts_preprocessor import tts_filter  # type: ignore

    def dividir(t: str) -> list[str]:
        frases, resto = segment_text_by_pysbd(t)
        return [f for f in list(frases) + [resto] if f and f.strip()]

    p("=" * 72)
    p("PROVA DO DIVISOR + FILTRO — instrumento do juiz (zero GPU)")
    p("divisor real: pysbd, conforme conf.yaml segment_method")
    p("=" * 72)
    falhas: list[str] = []

    p("\nCAMADA A — o divisor nao parte um bloco delimitado ao meio")
    okA = True
    for texto in CASOS:
        for i, fr in enumerate(dividir(texto)):
            quebrado = desbalanceado(fr)
            if quebrado:
                okA = False
                falhas.append(f"A: pedaco {i} de {texto!r} saiu com {quebrado} orfao: {fr!r}")
                p(f"  [FALHA] {quebrado} orfao no pedaco {i}: {fr!r}")
    if okA:
        p("  [OK]    nenhum pedaco saiu com delimitador orfao")

    p("\nCAMADA B — conservacao da fala: nem palavra a menos, nem a mais")
    okB = True
    for texto in CASOS:
        esperado = palavras_faladas_esperadas(texto)
        obtido: list[str] = []
        for fr in dividir(texto):
            obtido += re.findall(r"\w+", tts_filter(fr, **FLAGS).lower())
        faltou = [w for w in esperado if w not in obtido]
        sobrou = [w for w in obtido if w not in esperado]
        if faltou or sobrou:
            okB = False
            if faltou:
                falhas.append(f"B: fala CORTADA em {texto!r}: sumiu {faltou}")
            if sobrou:
                falhas.append(f"B: rubrica FALADA em {texto!r}: entrou {sobrou}")
            p(f"  [FALHA] {texto!r}")
            p(f"          esperado falar: {esperado}")
            p(f"          falou de fato : {obtido}")
        else:
            p(f"  [OK]    {texto!r}")

    p("\nCAMADA C — texto normal continua sendo dividido (sem regressao)")
    okC = True
    for texto in NORMAIS:
        pedacos = dividir(texto)
        esperado = re.findall(r"\w+", texto.lower())
        obtido: list[str] = []
        for fr in pedacos:
            obtido += re.findall(r"\w+", tts_filter(fr, **FLAGS).lower())
        if len(pedacos) < 2:
            okC = False
            falhas.append(f"C: {texto!r} nao foi dividido em frases ({len(pedacos)} pedaco)")
            p(f"  [FALHA] nao dividiu: {texto!r}")
        elif obtido != esperado:
            okC = False
            falhas.append(f"C: texto normal alterado em {texto!r}")
            p(f"  [FALHA] alterou texto normal: {obtido}")
        else:
            p(f"  [OK]    {len(pedacos)} frases, texto intacto: {texto!r}")

    p("\n" + "-" * 72)
    for x in falhas:
        p(f"  {x}")
    p("-" * 72)
    v = okA and okB and okC
    p(f"CAMADAS: A={'ok' if okA else 'X'} B={'ok' if okB else 'X'} C={'ok' if okC else 'X'}")
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 72)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
