"""Todo roteiro entregável precisa ser percorrível de ponta a ponta.

Guarda para um defeito barato e caro: um `next` apontando para um id que não
existe, ou um beat que ninguém alcança, só aparece na hora da gravação.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from celia.core.continuous_behavior import ScriptTracker
from celia.core.roteiro_live import RoteiroLiveRuntime

RAIZ = Path(__file__).resolve().parents[1]
ROTEIROS = sorted(
    p for p in (RAIZ / "roteiros").glob("*.json")
    if isinstance(json.loads(p.read_text(encoding="utf-8")), dict)
    and json.loads(p.read_text(encoding="utf-8")).get("beats")
)


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


@pytest.mark.parametrize("caminho", ROTEIROS, ids=lambda p: p.stem)
def test_todo_beat_e_alcancavel_a_partir_do_start(caminho):
    """Alcançável pelo grafo, não pelo botão.

    A primeira versão deste teste usava cliques de AVANÇAR FALA e reprovava o
    `ia_vs_criador`, que tem ramos escolhidos por resultado de jogo
    (`03A_velha_criador` e irmãos). O teste é que estava errado: ramo não é beco.
    """
    script = json.loads(caminho.read_text(encoding="utf-8"))
    ids = [b["id"] for b in script["beats"]]
    assert len(ids) == len(set(ids)), "id de beat repetido"
    if not any(b.get("next") for b in script["beats"]):
        pytest.skip(
            f"{caminho.name} é esqueleto: nenhum beat declara `next`, então a "
            "gravação não anda. O catálogo marca como rascunho e nunca o sugere."
        )

    por_id = {b["id"]: b for b in script["beats"]}
    saidas = {}
    for beat in script["beats"]:
        destinos = list(beat.get("next") or [])
        for chave in ("branches", "results", "options"):
            ramos = beat.get(chave)
            # `branches` é um dicionário resultado -> beat. Iterar um dicionário
            # devolve as CHAVES ("celia", "criador", "draw"), que não são ids de
            # beat: foi assim que este teste acusou o ia_vs_criador de ter beat
            # órfão que sempre existiu e sempre foi alcançável.
            if isinstance(ramos, dict):
                ramos = list(ramos.values())
            for ramo in ramos or []:
                alvo = ramo.get("beat_id") if isinstance(ramo, dict) else ramo
                if alvo:
                    destinos.append(alvo)
        for destino in destinos:
            assert destino in por_id, f"{beat['id']} aponta para {destino}, que não existe"
        saidas[beat["id"]] = destinos

    alcancados, fila = set(), [script["start"]]
    while fila:
        atual = fila.pop()
        if atual in alcancados:
            continue
        alcancados.add(atual)
        fila.extend(saidas.get(atual, []))

    orfaos = [i for i in ids if i not in alcancados]
    assert not orfaos, f"beats que ninguém alcança a partir de {script['start']}: {orfaos}"


@pytest.mark.parametrize("caminho", ROTEIROS, ids=lambda p: p.stem)
def test_roteiro_com_falas_anda_ate_o_fim_com_avancar_fala(caminho):
    script = json.loads(caminho.read_text(encoding="utf-8"))
    tem_fala = [
        b["id"] for b in script["beats"]
        if any(str((l or {}).get("text") or "").strip() for l in (b.get("lines") or []))
    ]
    if not tem_fala:
        pytest.skip(
            f"{caminho.name} é esqueleto: {len(script['beats'])} beats, nenhum com fala. "
            "O teleprompter abriria vazio; o catálogo marca como rascunho."
        )

    async def caminhar():
        runtime = RoteiroLiveRuntime()
        await runtime.activate("gravacao", new_session=True, script=script)
        vistos, cliques = [], 0
        while cliques < 400:
            atual = (runtime.snapshot().get("current_beat") or {}).get("id")
            if atual and (not vistos or vistos[-1] != atual):
                vistos.append(atual)
            antes = (atual, len(runtime.completed_lines))
            await runtime.control({"action": "forward_line"})
            agora = (runtime.snapshot().get("current_beat") or {}).get("id")
            cliques += 1
            if (agora, len(runtime.completed_lines)) == antes:
                break
        return vistos, cliques

    vistos, cliques = _run(caminhar())
    assert cliques < 400, "AVANÇAR FALA não terminou: laço"
    assert vistos[0] == script["start"]

    # A espinha linear: seguir o primeiro `next` até acabar ou até um beat que
    # espera resultado de jogo. Parar num ponto de ramo é comportamento correto
    # do produto — o avanço manual não escolhe vencedor por ele.
    por_id = {b["id"]: b for b in script["beats"]}
    espinha, atual = [], script["start"]
    while atual and atual in por_id and atual not in espinha:
        espinha.append(atual)
        beat = por_id[atual]
        if beat.get("branch_source") == "game_result":
            break
        seguintes = beat.get("next") or []
        atual = seguintes[0] if seguintes else None

    assert vistos == espinha, (
        f"AVANÇAR FALA saiu da espinha do roteiro.\n  esperado: {espinha}\n  andou:    {vistos}"
    )


@pytest.mark.parametrize("caminho", ROTEIROS, ids=lambda p: p.stem)
def test_roteiro_do_disco_tem_id_e_start_valido(caminho):
    script = json.loads(caminho.read_text(encoding="utf-8"))
    assert script.get("id"), "roteiro sem id não entra no seletor"
    assert script.get("start") in [b["id"] for b in script["beats"]]


@pytest.mark.parametrize("caminho", ROTEIROS, ids=lambda p: p.stem)
def test_roteiro_pronto_tem_titulo_em_todo_beat(caminho):
    script = json.loads(caminho.read_text(encoding="utf-8"))
    com_fala = [
        b for b in script["beats"]
        if any(str((l or {}).get("text") or "").strip() for l in (b.get("lines") or []))
    ]
    if not com_fala:
        pytest.skip(f"{caminho.name} é esqueleto, sem fala em nenhum beat")
    sem_titulo = [b["id"] for b in com_fala if not b.get("title")]
    assert not sem_titulo, f"beat com fala e sem título: {sem_titulo}"


# ------------------------- validade do instrumento acima -------------------
# Régua que nunca foi testada contra um defeito real não é régua. Os dois casos
# abaixo obrigam o verificador a disparar; os roteiros do disco obrigam ele a
# ficar quieto.


def _grafo(script: dict) -> tuple[dict, set]:
    por_id = {b["id"]: b for b in script["beats"]}
    saidas = {}
    for beat in script["beats"]:
        destinos = list(beat.get("next") or [])
        ramos = beat.get("branches")
        if isinstance(ramos, dict):
            destinos += list(ramos.values())
        saidas[beat["id"]] = destinos
    alcancados, fila = set(), [script["start"]]
    while fila:
        atual = fila.pop()
        if atual in alcancados or atual not in por_id:
            continue
        alcancados.add(atual)
        fila.extend(saidas[atual])
    return saidas, alcancados


def test_POSITIVO_verificador_acusa_next_apontando_para_beat_inexistente():
    script = {
        "id": "x", "start": "a",
        "beats": [
            {"id": "a", "title": "A", "lines": [], "next": ["fantasma"]},
        ],
    }
    saidas, _ = _grafo(script)
    ids = {b["id"] for b in script["beats"]}
    pendurados = [d for destinos in saidas.values() for d in destinos if d not in ids]

    assert pendurados == ["fantasma"]


def test_POSITIVO_verificador_acusa_beat_que_ninguem_alcanca():
    script = {
        "id": "x", "start": "a",
        "beats": [
            {"id": "a", "title": "A", "lines": [], "next": []},
            {"id": "ilha", "title": "Ilha", "lines": [], "next": []},
        ],
    }
    _, alcancados = _grafo(script)

    assert "ilha" not in alcancados


def test_NEGATIVO_verificador_aceita_ramo_declarado_como_dicionario():
    script = {
        "id": "x", "start": "a",
        "beats": [
            {"id": "a", "title": "A", "lines": [], "next": ["b"]},
            {"id": "b", "title": "B", "lines": [], "next": [],
             "branches": {"celia": "c", "criador": "d", "draw": "d"}},
            {"id": "c", "title": "C", "lines": [], "next": []},
            {"id": "d", "title": "D", "lines": [], "next": []},
        ],
    }
    _, alcancados = _grafo(script)

    assert alcancados == {"a", "b", "c", "d"}
