#!/usr/bin/env python3
"""
Prova do MODO FANTASMA do Pet — instrumento do juiz. ZERO GPU.

DEFEITO RELATADO pelo Pai em 2026-08-28:
    "ela nao fica no modo fantasma, onde posso clicar atraves dela, quando
     soltei o ctrl + espaco"

LEITURA DO CODIGO (tools/pet-electron/main.js, medido em 2026-08-28):
    globalShortcut.register("Control+Space", armPetInteraction)   linha 440
    -> armPetInteraction: setIgnoreMouseEvents(false) + focus()   linha 295
    A volta ao fantasma so acontece por DOIS caminhos:
      petWindow.on("blur", disarmPetInteraction)                  linha 171
      before-input-event, keyUp de Control ou Space               linha 173

INFERIDO (nao medido — exige Electron com teclado real): o atalho global
consome o evento de tecla, entao o `keyUp` nao chega ao `before-input-event`;
e como `armPetInteraction` chama `focus()`, a janela tambem nao perde o foco,
entao o `blur` nao dispara. Sem nenhum dos dois caminhos, a janela fica
interativa PARA SEMPRE e o clique nunca mais atravessa. E o que o Pai relatou.

O CONSERTO NAO PODE DEPENDER DE SABER QUAL DOS DOIS FALHOU. A porta tem de
voltar ao fantasma por propriedade propria — uma rede que so existe porque o
evento pode nao chegar e defeito; o algoritmo tem de terminar sozinho.

Por isso este teste cobra um MODULO PURO, `tools/pet-electron/ghost-gate.js`,
no mesmo padrao dos que ja existem ali (state.js, geometry.js, mode.js,
subtitle-state.js) — os testes atuais nao tocam main.js porque ele e
inseparavel do Electron.

CONTRATO CONGELADO
------------------
    const { createGate } = require("ghost-gate.js");
    const porta = createGate({ holdMs: 700, now: 0 });

    porta.isGhost()      -> boolean   (true = clique atravessa)
    porta.arm(now)       -> renova a interacao; janela fica clicavel
    porta.touch(now)     -> renova por atividade do mouse
    porta.disarm(now)    -> volta ao fantasma na hora
    porta.tick(now)      -> devolve true se ACABOU de voltar ao fantasma

REGRAS
  1. Comeca no fantasma. O padrao e o desktop utilizavel.
  2. `arm` deixa clicavel.
  3. SEM renovacao, `tick` devolve ao fantasma depois de holdMs. Esta e a
     regra que conserta o defeito: nao depende de keyUp nem de blur.
  4. `arm` repetido (auto-repeat do teclado enquanto o Pai segura) renova.
  5. `touch` renova — ela nao pode sumir no meio de um arraste.
  6. `disarm` volta na hora, mesmo dentro do prazo.
  7. Relogio que anda para tras nao pode prender a porta aberta.
  8. `tick` avisa a transicao UMA vez, nao a cada chamada.

Uso:  .venv/bin/python tests/prova_modo_fantasma.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
GATE = RAIZ / "tools" / "pet-electron" / "ghost-gate.js"


def p(t: str = "") -> None:
    print(t, flush=True)


def roda(corpo: str):
    script = (
        "const {createGate} = require(process.argv[1]);"
        "const saida = [];"
        "const diz = (k, v) => saida.push([k, v]);"
        + corpo
        + "process.stdout.write(JSON.stringify(saida));"
    )
    try:
        out = subprocess.check_output(
            ["node", "-e", script, str(GATE)], text=True, stderr=subprocess.PIPE, timeout=30
        )
    except subprocess.CalledProcessError as e:
        return None, (e.stderr or "").strip().splitlines()[:2]
    return dict(json.loads(out)), None


def main() -> int:
    p("=" * 70)
    p("PROVA DO MODO FANTASMA — instrumento do juiz (zero GPU)")
    p(f"modulo: {GATE}")
    p("=" * 70)

    if not GATE.exists():
        p(f"\nREPROVOU: {GATE} nao existe.")
        p("  O contrato pede o modulo puro; a porta nao pode viver so dentro")
        p("  do main.js, que e inseparavel do Electron e nao e testavel.")
        return 1

    falhas: list[str] = []

    r, err = roda(
        "const g = createGate({holdMs: 700, now: 0});"
        "diz('inicio_fantasma', g.isGhost());"
        "g.arm(0); diz('apos_arm', g.isGhost());"
        "g.tick(500); diz('dentro_do_prazo', g.isGhost());"
        "const t = g.tick(800); diz('voltou_no_tick', t); diz('apos_prazo', g.isGhost());"
        "diz('tick_repetido_nao_reavisa', g.tick(900));"
    )
    if err:
        p(f"\nREPROVOU: o modulo nao carrega: {err}")
        return 1

    p("\nCAMADA 1 — a porta volta sozinha, sem keyUp e sem blur")
    casos1 = [
        ("inicio_fantasma", True, "comeca no fantasma (desktop utilizavel)"),
        ("apos_arm", False, "arm deixa clicavel"),
        ("dentro_do_prazo", False, "dentro do prazo continua clicavel"),
        ("voltou_no_tick", True, "tick avisa a volta ao fantasma"),
        ("apos_prazo", True, "ESTA e a regra que conserta o defeito"),
        ("tick_repetido_nao_reavisa", False, "avisa a transicao uma vez so"),
    ]
    for chave, esperado, desc in casos1:
        obtido = r.get(chave)
        if obtido != esperado:
            falhas.append(f"camada1/{chave}: esperado {esperado}, obtido {obtido}")
            p(f"  [FALHA] {desc}: esperado {esperado}, obtido {obtido}")
        else:
            p(f"  [OK]    {desc}")

    p("\nCAMADA 2 — renovacao mantem clicavel (nao sumir no meio do arraste)")
    r2, _ = roda(
        "const g = createGate({holdMs: 700, now: 0});"
        "g.arm(0); g.arm(400); g.tick(800); diz('arm_repetido_renova', g.isGhost());"
        "const h = createGate({holdMs: 700, now: 0});"
        "h.arm(0); h.touch(500); h.tick(900); diz('touch_renova', h.isGhost());"
        "const i = createGate({holdMs: 700, now: 0});"
        "i.arm(0); i.disarm(100); diz('disarm_imediato', i.isGhost());"
    )
    for chave, esperado, desc in [
        ("arm_repetido_renova", False, "auto-repeat do teclado renova"),
        ("touch_renova", False, "atividade do mouse renova"),
        ("disarm_imediato", True, "disarm volta na hora"),
    ]:
        obtido = (r2 or {}).get(chave)
        if obtido != esperado:
            falhas.append(f"camada2/{chave}: esperado {esperado}, obtido {obtido}")
            p(f"  [FALHA] {desc}: esperado {esperado}, obtido {obtido}")
        else:
            p(f"  [OK]    {desc}")

    p("\nCAMADA 3 — nenhum relogio esquisito prende a porta aberta")
    r3, _ = roda(
        "const g = createGate({holdMs: 700, now: 1000});"
        "g.arm(1000); g.tick(500);"          # relogio andou para tras
        "diz('relogio_para_tras', g.tick(5000));"
        "const h = createGate({holdMs: 700, now: 0});"
        "h.arm(0); diz('salto_enorme', h.tick(1e12));"
    )
    for chave, desc in [
        ("relogio_para_tras", "relogio para tras nao prende a porta"),
        ("salto_enorme", "salto enorme de tempo fecha a porta"),
    ]:
        if (r3 or {}).get(chave) is not True:
            falhas.append(f"camada3/{chave}: a porta ficou presa aberta")
            p(f"  [FALHA] {desc}")
        else:
            p(f"  [OK]    {desc}")

    p("\n" + "-" * 70)
    for f in falhas:
        p(f"  {f}")
    p("-" * 70)
    v = not falhas
    p(f"VEREDITO: {'PASSOU' if v else 'REPROVOU'}")
    p("=" * 70)
    return 0 if v else 1


if __name__ == "__main__":
    raise SystemExit(main())
