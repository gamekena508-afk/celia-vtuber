#!/usr/bin/env python3
"""
Prova do TEXTO que vira voz — instrumento do juiz. ZERO GPU.

Dois defeitos relatados pelo Pai em 2026-08-27:
  "as vezes gera frase incompleta e cortada"
  "as vezes fala a tag, tipo happy"

Reproduzidos no mesmo dia, deterministicamente, em
`src/celia/utils/tts_preprocessor.py::tts_filter`, com TODAS as flags do
conf.yaml ligadas (remove_special_char, ignore_brackets, ignore_parentheses,
ignore_asterisks, ignore_angle_brackets = true):

    'Oi Pai [happy tudo bem com voce hoje?'  ->  'Oi Pai'      CORTOU
    'Oi Pai (rindo tudo bem com voce hoje?'  ->  'Oi Pai'      CORTOU
    'Oi Pai < tudo bem com voce hoje?'       ->  'Oi Pai'      CORTOU
    'happy Oi Pai, tudo bem?'                ->  inalterado    FALA A TAG
    'Vou jogar agora. confident'             ->  inalterado    FALA A TAG

Um delimitador que ABRE e nunca FECHA engole todo o resto da fala. Um unico
`<` solto decepa a frase inteira. E a tag sem colchete nao e removida por
ninguem: `remove_emotion_keywords()` do live2d_model so apaga a forma `[tag]`,
e nem chega a ser chamada neste caminho.

CONTRATO QUE ESTE TESTE COBRA
-----------------------------
1. Delimitador nao fechado NAO pode decepar a fala. O delimitador solto pode
   sumir; as PALAVRAS depois dele nao.
2. O que ja funciona nao regride: par fechado continua sendo filtrado.
3. Tag de emocao conhecida, sozinha, no COMECO ou no FIM, sai — com ou sem
   colchete.
4. Palavra legitima NAO some. "saudade" contem "sad" e continua inteira; e uma
   emocao no MEIO de texto real fica, porque remover ali e chute.
5. Texto que tinha palavra de verdade nunca vira vazio.

Uso:  .venv/bin/python tests/prova_tts_texto.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

FLAGS = dict(
    remove_special_char=True, ignore_brackets=True, ignore_parentheses=True,
    ignore_asterisks=True, ignore_angle_brackets=True,
)

# emotionMap do modelo celia_vts em model_dict.json
EMOCOES = [
    "angry", "confused", "funny", "happy", "holding_back_laughter", "in_love",
    "neutral", "passionate", "sad", "scared", "surprised", "teasing",
    "trol_idle", "trol_speaking",
]


def p(t: str = "") -> None:
    print(t, flush=True)


def main() -> int:
    from celia.utils.tts_preprocessor import tts_filter  # type: ignore

    def f(t: str) -> str:
        return tts_filter(t, **FLAGS)

    p("=" * 70)
    p("PROVA DO TEXTO QUE VIRA VOZ — instrumento do juiz (zero GPU)")
    p("=" * 70)
    falhas: list[str] = []

    # ---- CAMADA 1: delimitador aberto e nunca fechado nao pode decepar
    p("\nCAMADA 1 — delimitador nao fechado NAO pode cortar a fala")
    abertos = [
        ("colchete", "Oi Pai [happy tudo bem com voce hoje?"),
        ("parentese", "Oi Pai (rindo tudo bem com voce hoje?"),
        ("angulo", "Oi Pai < tudo bem com voce hoje?"),
        ("asterisco impar", "Oi Pai *ri tudo bem com voce hoje?"),
        ("fecha sem abrir", "Oi Pai] tudo bem com voce hoje?"),
        ("colchete no fim", "Tudo bem com voce hoje? ["),
    ]
    ok1 = True
    for nome, txt in abertos:
        saida = f(txt)
        # a prova e concreta: as palavras do FIM tem de sobreviver
        perdidas = [w for w in ("tudo", "bem", "hoje") if w in txt and w not in saida]
        if perdidas:
            ok1 = False
            falhas.append(f"camada1/{nome}: perdeu {perdidas} — {txt!r} -> {saida!r}")
            p(f"  [FALHA] {nome:16s} {txt!r}\n           -> {saida!r}")
        else:
            p(f"  [OK]    {nome:16s} -> {saida!r}")

    # ---- CAMADA 2: o que ja funciona nao pode regredir
    p("\nCAMADA 2 — par fechado continua sendo filtrado (nao regredir)")
    # A assercao compara o MARCADOR literal, nao a letra dentro dele. A primeira
    # versao deste teste comparava "b" (de "<b>") e casava com "bem" — falso
    # positivo meu, corrigido em 2026-08-27 antes de delegar qualquer coisa.
    fechados = [
        ("[happy] Oi Pai, tudo bem?", "happy"),
        ("Oi Pai (rindo) tudo bem?", "rindo"),
        ("Oi Pai <b>tudo</b> bem?", "<b>"),
        ("Oi Pai *ri baixinho* tudo bem?", "*ri"),
    ]
    ok2 = True
    for txt, sumir in fechados:
        saida = f(txt)
        if sumir in saida:
            ok2 = False
            falhas.append(f"camada2: {sumir!r} sobreviveu em {saida!r}")
            p(f"  [FALHA] {txt!r} -> {saida!r}")
        else:
            p(f"  [OK]    {txt!r} -> {saida!r}")

    # ---- CAMADA 3: tag sem colchete no comeco ou no fim tem de sair
    p("\nCAMADA 3 — tag de emocao sem colchete, no comeco ou no fim, sai")
    nus = [
        "happy Oi Pai, tudo bem?",
        "Vou jogar agora. confident",
        "trol_speaking Vamos la, Pai.",
        "Ganhei essa partida. teasing",
        "neutral Boa noite.",
        "Boa noite, Pai. happy",
    ]
    ok3 = True
    for txt in nus:
        saida = f(txt)
        vazou = [e for e in EMOCOES + ["confident"] if e in saida.lower().split()]
        if vazou:
            ok3 = False
            falhas.append(f"camada3: tag {vazou} falada em {saida!r}")
            p(f"  [FALHA] {txt!r} -> {saida!r}   (tag {vazou} seria FALADA)")
        else:
            p(f"  [OK]    {txt!r} -> {saida!r}")

    # ---- CAMADA 4: palavra legitima nao pode sumir
    p("\nCAMADA 4 — palavra legitima NAO some (o conserto nao pode exagerar)")
    legitimas = [
        ("saudade", "Que saudade de jogar contigo, Pai."),
        ("sadio", "Isso e um habito sadio."),
        ("frase limpa", "Oi Pai, tudo bem com voce hoje?"),
        ("emocao no meio", "Ele falou happy no meio da frase de proposito."),
        ("acentos", "Voce nao imagina a alegria que eu senti, coroa."),
    ]
    ok4 = True
    for nome, txt in legitimas:
        saida = f(txt)
        alvo = {"saudade": "saudade", "sadio": "sadio"}.get(nome)
        problema = None
        if alvo and alvo not in saida:
            problema = f"perdeu {alvo!r}"
        if nome in ("frase limpa", "acentos") and saida.strip() != txt.strip():
            problema = "frase sem marcacao foi alterada"
        if nome == "emocao no meio" and "meio da frase" not in saida:
            problema = "cortou texto real ao redor da emocao do meio"
        if problema:
            ok4 = False
            falhas.append(f"camada4/{nome}: {problema} — {txt!r} -> {saida!r}")
            p(f"  [FALHA] {nome:16s} {problema}: {saida!r}")
        else:
            p(f"  [OK]    {nome:16s} -> {saida!r}")

    # ---- CAMADA 5: invariante global — nunca esvaziar fala real
    p("\nCAMADA 5 — texto com palavra de verdade nunca vira vazio")
    ok5 = True
    for _, txt in abertos + [(n, t) for n, t in legitimas]:
        saida = f(txt)
        if not saida.strip():
            ok5 = False
            falhas.append(f"camada5: {txt!r} virou vazio")
            p(f"  [FALHA] {txt!r} -> vazio")
    if ok5:
        p("  [OK]    nenhum caso virou vazio")

    p("\n" + "-" * 70)
    for x in falhas:
        p(f"  {x}")
    p("-" * 70)
    v = ok1 and ok2 and ok3 and ok4 and ok5
    p(f"CAMADAS: 1={'ok' if ok1 else 'X'} 2={'ok' if ok2 else 'X'} "
      f"3={'ok' if ok3 else 'X'} 4={'ok' if ok4 else 'X'} 5={'ok' if ok5 else 'X'}")
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 70)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
