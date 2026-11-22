"""Catálogo de roteiros do teleprompter.

Cada regra decidida pelo dono tem controle positivo (obriga a disparar) e
controle negativo (obriga a ficar quieto). Métrica que só roda não é métrica boa.
"""

from __future__ import annotations

import json
import time

import pytest

from celia.core import roteiro_catalogo as cat


def _roteiro(identificador: str, beats: list[str], titulo: str = "", versao: int = 1) -> dict:
    return {
        "id": identificador,
        "title": titulo or identificador,
        "version": versao,
        # Beat com fala: sem isso o catálogo marca o roteiro como rascunho e,
        # corretamente, para de sugeri-lo.
        "beats": [
            {"id": b, "title": b, "lines": [{"speaker": "Criador", "text": f"fala {b}"}]}
            for b in beats
        ],
    }


@pytest.fixture
def arvore(tmp_path, monkeypatch):
    """Árvore isolada: nenhum teste encosta no var/ nem no roteiros/ do dono."""
    roteiros = tmp_path / "roteiros"
    sessoes = tmp_path / "var" / "roteiro_sessions"
    roteiros.mkdir(parents=True)
    sessoes.mkdir(parents=True)
    monkeypatch.setattr(cat, "ROTEIROS_DIR", roteiros)
    monkeypatch.setattr(cat, "SESSOES_DIR", sessoes)
    monkeypatch.setattr(cat, "MARCAS_PATH", tmp_path / "var" / "roteiros_gravados.json")

    def escrever(nome: str, dados: dict) -> None:
        (roteiros / nome).write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")

    def sessao(nome: str, eventos: list[dict]) -> None:
        linhas = [json.dumps(e, ensure_ascii=False) for e in eventos]
        (sessoes / nome).write_text("\n".join(linhas), encoding="utf-8")

    return {"raiz": tmp_path, "roteiro": escrever, "sessao": sessao}


# --------------------------------------------------------------------- leitura


def test_arquivo_sem_beats_nao_entra_no_catalogo(arvore):
    """O rotulo.json real do dono não é roteiro e não pode virar item de lista."""
    arvore["roteiro"]("bom.json", _roteiro("bom", ["a", "b"]))
    arvore["roteiro"]("rotulo.json", {"titulo": "isto e um rotulo", "beats": []})
    arvore["roteiro"]("sem_id.json", {"beats": [{"id": "x"}]})

    ids = [item["id"] for item in cat.roteiros_em_disco()]

    assert ids == ["bom"]


def test_catalogo_lista_os_roteiros_com_versao_e_beats(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1", "2", "3"], versao=2))
    arvore["roteiro"]("b.json", _roteiro("beta", ["1"]))

    itens = cat.catalogo()["roteiros"]

    assert [i["id"] for i in itens] == ["alfa", "beta"]
    assert itens[0]["versao"] == 2 and itens[0]["beats"] == 3
    assert itens[1]["beats"] == 1


def test_catalogo_marca_qual_esta_ativo(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["roteiro"]("b.json", _roteiro("beta", ["1"]))

    itens = cat.catalogo(ativo_id="beta")["roteiros"]

    assert [i["ativo"] for i in itens] == [False, True]


# ------------------------------------------------------------------- evidência


def test_beats_vistos_ignora_ids_de_versao_antiga(arvore):
    """POSITIVO do achado 24/16: log velho traz beat que não existe mais."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["novo_1", "novo_2"]))
    arvore["sessao"]("s1.jsonl", [
        {"event": "session.started", "timestamp": 10, "data": {"script_id": "alfa"}},
        {"event": "beat.changed", "timestamp": 11, "data": {"beat_id": "novo_1"}},
        {"event": "beat.changed", "timestamp": 12, "data": {"beat_id": "beat_de_2020"}},
        {"event": "beat.changed", "timestamp": 13, "data": {"beat_id": "outro_extinto"}},
    ])

    item = cat.catalogo()["roteiros"][0]

    assert item["beats"] == 2
    assert item["evidencia"]["beats_vistos"] == 1, "beat extinto não pode contar"
    assert item["evidencia"]["beats_vistos"] <= item["beats"], "24/16 é impossível"


def test_script_id_orfao_nao_vira_item_de_catalogo(arvore):
    """POSITIVO: os órfãos medidos, 'session' e 'discord-tutorial' sem sufixo."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["sessao"]("s1.jsonl", [
        {"event": "session.started", "timestamp": 10, "data": {"script_id": "session"}},
        {"event": "beat.changed", "timestamp": 11, "data": {"beat_id": "1"}},
    ])

    saida = cat.catalogo()

    assert [i["id"] for i in saida["roteiros"]] == ["alfa"]
    assert saida["orfaos"] == ["session"]


def test_evidencia_conta_sessao_e_tomada_boa_separadas(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["sessao"]("s1.jsonl", [
        {"event": "session.started", "timestamp": 10, "data": {"script_id": "alfa"}},
        {"event": "take.marked", "timestamp": 11, "data": {"mark": "good"}},
        {"event": "take.marked", "timestamp": 12, "data": {"mark": "discarded"}},
    ])
    arvore["sessao"]("s2.jsonl", [
        {"event": "session.started", "timestamp": 20, "data": {"script_id": "alfa"}},
    ])

    ev = cat.catalogo()["roteiros"][0]["evidencia"]

    assert ev["sessoes"] == 2
    assert ev["tomadas_boas"] == 1
    assert ev["descartadas"] == 1
    assert ev["ultima_sessao"] == 20


def test_evidencia_nao_marca_nada_como_gravado(arvore):
    """ORDEM da decisão 3(b): evidência informa, nunca decide.

    NEGATIVO: 1415 sessões e 4 tomadas boas continuam devolvendo gravado=False.
    """
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    eventos = [{"event": "session.started", "timestamp": 1, "data": {"script_id": "alfa"}}]
    eventos += [{"event": "take.marked", "timestamp": 2, "data": {"mark": "good"}}] * 4
    arvore["sessao"]("s1.jsonl", eventos)

    item = cat.catalogo()["roteiros"][0]

    assert item["evidencia"]["tomadas_boas"] == 4
    assert item["gravado"] is False, "só o botão do dono marca gravado"


# ----------------------------------------------------------------------- marca


def test_marcar_gravado_persiste_e_desmarcar_volta(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))

    cat.marcar_gravado("alfa", True)
    assert cat.catalogo()["roteiros"][0]["gravado"] is True
    assert cat.catalogo()["roteiros"][0]["gravado_em"] is not None

    cat.marcar_gravado("alfa", False)
    assert cat.catalogo()["roteiros"][0]["gravado"] is False


def test_marca_sobrevive_a_releitura_do_disco(arvore):
    """A marca mora no servidor — decisão 4(a). Reler o arquivo tem de bastar."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    cat.marcar_gravado("alfa", True)

    assert cat.MARCAS_PATH.exists()
    gravado_em_disco = json.loads(cat.MARCAS_PATH.read_text(encoding="utf-8"))

    assert "alfa" in gravado_em_disco["gravados"]


def test_marcar_roteiro_inexistente_e_recusado(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))

    with pytest.raises(ValueError, match="roteiro_desconhecido"):
        cat.marcar_gravado("nao-existe", True)


def test_marcas_corrompidas_nao_derrubam_o_catalogo(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    cat.MARCAS_PATH.parent.mkdir(parents=True, exist_ok=True)
    cat.MARCAS_PATH.write_text("{lixo nao json", encoding="utf-8")

    saida = cat.catalogo()

    assert saida["roteiros"][0]["gravado"] is False


# ------------------------------------------------------------------- sugestão


def test_sugestao_pula_os_ja_gravados(arvore):
    """ORDEM do dono (decisão 7). POSITIVO: o gravado não pode ser sugerido."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["roteiro"]("b.json", _roteiro("beta", ["1"]))
    cat.marcar_gravado("alfa", True)

    sugestao = cat.catalogo()["sugestao"]

    assert sugestao["id"] == "beta"
    assert sugestao["todos_gravados"] is False


def test_sugestao_nunca_devolve_gravado_mesmo_estando_ativo(arvore):
    """POSITIVO duro: o gravado está ATIVO e ainda assim não pode ser sugerido."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["roteiro"]("b.json", _roteiro("beta", ["1"]))
    cat.marcar_gravado("alfa", True)

    sugestao = cat.catalogo(ativo_id="alfa")["sugestao"]

    assert sugestao["id"] == "beta"


def test_sugestao_prefere_o_ativo_quando_ele_ainda_nao_foi_gravado(arvore):
    """NEGATIVO: sem nada marcado, a regra não pode arrastar o dono para outro."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["roteiro"]("b.json", _roteiro("beta", ["1"]))

    sugestao = cat.catalogo(ativo_id="beta")["sugestao"]

    assert sugestao["id"] == "beta"
    assert sugestao["motivo"] == "ativo_e_pendente"


def test_todos_gravados_fica_no_ultimo_selecionado(arvore):
    """Decisão 5(a), com as palavras do dono: 'fica no último'."""
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    arvore["roteiro"]("b.json", _roteiro("beta", ["1"]))
    cat.registrar_selecao("beta")
    cat.marcar_gravado("alfa", True)
    cat.marcar_gravado("beta", True)

    sugestao = cat.catalogo()["sugestao"]

    assert sugestao["todos_gravados"] is True
    assert sugestao["id"] == "beta"
    assert sugestao["motivo"] == "todos_gravados_fica_no_ultimo"


def test_todos_gravados_sem_ultimo_nao_inventa_um(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    cat.marcar_gravado("alfa", True)

    sugestao = cat.catalogo()["sugestao"]

    assert sugestao["todos_gravados"] is True
    assert sugestao["id"] is None
    assert sugestao["motivo"] == "todos_gravados_sem_ultimo"


def test_ultimo_selecionado_apagado_do_disco_nao_quebra_a_sugestao(arvore):
    arvore["roteiro"]("a.json", _roteiro("alfa", ["1"]))
    cat.registrar_selecao("roteiro-que-sumiu")
    cat.marcar_gravado("alfa", True)

    assert cat.catalogo()["sugestao"]["id"] is None


def test_catalogo_vazio_nao_estoura(arvore):
    saida = cat.catalogo()

    assert saida["roteiros"] == []
    assert saida["sugestao"]["motivo"] == "sem_roteiro"


def test_rascunho_sem_nenhuma_fala_e_marcado_e_nunca_sugerido(arvore):
    """Medido em dois roteiros reais: beats sem fala abrem o teleprompter vazio.

    POSITIVO: o esqueleto é marcado e sai da sugestão.
    NEGATIVO: o roteiro com fala continua sendo sugerido normalmente.
    """
    arvore["roteiro"]("esqueleto.json", {
        "id": "esqueleto", "title": "Esqueleto", "version": 1,
        "beats": [{"id": "a", "title": "A", "lines": []},
                  {"id": "b", "title": "B", "lines": [{"speaker": "Criador", "text": "   "}]}],
    })
    arvore["roteiro"]("pronto.json", _roteiro("pronto", ["1", "2"]))

    itens = {i["id"]: i for i in cat.catalogo()["roteiros"]}

    assert itens["esqueleto"]["rascunho"] is True
    assert itens["esqueleto"]["beats_com_fala"] == 0
    assert itens["pronto"]["rascunho"] is False
    assert itens["pronto"]["beats_com_fala"] == 2
    assert cat.catalogo()["sugestao"]["id"] == "pronto"


def test_rascunho_continua_na_lista_para_escolha_manual(arvore):
    """Ele sai da sugestão, não da lista: o dono ainda pode abrir na mão."""
    arvore["roteiro"]("esqueleto.json", {
        "id": "esqueleto", "title": "Esqueleto", "version": 1,
        "beats": [{"id": "a", "title": "A", "lines": []}],
    })

    assert [i["id"] for i in cat.catalogo()["roteiros"]] == ["esqueleto"]
