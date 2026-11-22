#!/usr/bin/env python3
"""
Prova da EVIDENCIA na recusa da rota de acao dos jogos — instrumento do juiz.

POR QUE
-------
`docs/diagnosticos/CORRECAO-CELIA.md`, C12: *"a falha e em posicoes especificas
mais adiante (...) NAO investiguei essa causa."* Ninguem investigou porque a
recusa nao guarda nada: em 2026-08-27 foi medido que
`observability.emit("game_action_invalid", ...)` vai SEM metadata e nao ha log
do texto bruto do modelo. A rota recusa e esquece.

Este teste NAO exige que o 422 pare de acontecer. Recusar acao adivinhada e o
comportamento CERTO. Ele exige que, quando recusar, a recusa CARREGUE PROVA.

CONTRATO CONGELADO — o executor implementa exatamente isto, em nivel de MODULO
(nao dentro de um closure), para poder ser importado e testado:

    src/celia/bridge_routes.py::diagnostico_recusa(
        *, modelo: str | None, legais: list[str], bruto: str,
        prompt_chars: int, tentativas: int) -> dict

    chaves exatas: erro, modelo, n_legais, legais_amostra, bruto_amostra,
                   prompt_chars, tentativas
    limites: legais_amostra <= 8 itens ; bruto_amostra <= 300 chars
    erro == "acao_estrutural_ausente"  (o nome antigo nao muda: e contrato)
    modelo ausente vira "nenhum", nunca None solto no log
    NUNCA carrega o tabuleiro inteiro: prova cabe em log, despejo nao.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

CHAVES = {"erro", "modelo", "n_legais", "legais_amostra", "bruto_amostra",
          "prompt_chars", "tentativas"}


def p(t: str = "") -> None:
    print(t, flush=True)


def main() -> int:
    p("=" * 68)
    p("PROVA DA EVIDENCIA NA RECUSA — instrumento do juiz")
    p("=" * 68)

    try:
        from celia.bridge_routes import diagnostico_recusa  # type: ignore
    except ImportError as e:
        p(f"\nREPROVOU: nao existe diagnostico_recusa importavel: {e}")
        p("  O contrato pede a funcao em NIVEL DE MODULO, nao dentro do closure.")
        return 1

    falhas: list[str] = []

    # 1) caso normal
    d = diagnostico_recusa(
        modelo="cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast",
        legais=[f"{i},2-{i-2},4" for i in range(20)],
        bruto='Acho que o melhor lance e 5,2-3,4 porque abre a diagonal.',
        prompt_chars=778, tentativas=6,
    )
    if set(d) != CHAVES:
        falhas.append(f"chaves erradas: {sorted(set(d) ^ CHAVES)}")
    if d.get("erro") != "acao_estrutural_ausente":
        falhas.append(f"erro mudou de nome: {d.get('erro')!r} — isso e contrato")
    if d.get("n_legais") != 20:
        falhas.append(f"n_legais errado: {d.get('n_legais')}")
    if len(d.get("legais_amostra") or []) > 8:
        falhas.append(f"legais_amostra sem teto: {len(d['legais_amostra'])}")
    if "5,2-3,4" not in (d.get("bruto_amostra") or ""):
        falhas.append("bruto_amostra nao carrega o que o modelo disse — e o dado que faltava")
    p(f"\n1. caso normal -> {json.dumps(d, ensure_ascii=False)[:200]}")

    # 2) texto gigante tem de ser cortado: prova cabe em log, despejo nao
    d2 = diagnostico_recusa(modelo="x", legais=["a"], bruto="Z" * 5000,
                            prompt_chars=10, tentativas=1)
    if len(d2.get("bruto_amostra") or "") > 301:
        falhas.append(f"bruto_amostra sem teto: {len(d2['bruto_amostra'])} chars")
    p(f"2. bruto de 5000 chars -> amostra de {len(d2.get('bruto_amostra') or '')} chars")

    # 3) sem modelo (cadeia inteira caiu) nao pode virar None solto
    d3 = diagnostico_recusa(modelo=None, legais=[], bruto="", prompt_chars=0, tentativas=6)
    if d3.get("modelo") != "nenhum":
        falhas.append(f"modelo ausente virou {d3.get('modelo')!r}, esperado 'nenhum'")
    if d3.get("n_legais") != 0:
        falhas.append("n_legais nao aceita lista vazia")
    p(f"3. cadeia inteira caiu -> modelo={d3.get('modelo')!r}")

    # 4) serializa em JSON (vai para log e para o corpo do 422)
    try:
        json.dumps(d)
    except Exception as e:  # noqa: BLE001
        falhas.append(f"nao serializa em JSON: {e!r}")

    # 5) nao pode vazar o tabuleiro
    texto = json.dumps(d) + json.dumps(d2) + json.dumps(d3)
    if '"board"' in texto or "player" in texto:
        falhas.append("a evidencia carrega o tabuleiro — prova cabe em log, despejo nao")
    p("4. serializa em JSON e nao carrega tabuleiro")

    p("\n" + "-" * 68)
    for f in falhas:
        p(f"  [FALHA] {f}")
    p("-" * 68)
    p(f"VEREDITO: {'PASSOU' if not falhas else 'REPROVOU'}")
    return 0 if not falhas else 1


if __name__ == "__main__":
    raise SystemExit(main())
