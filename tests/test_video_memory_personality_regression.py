"""Regressoes observadas no corpus de videos de 19 fontes (read-only)."""
from __future__ import annotations

import pytest

from celia.core import video_memory as vm


@pytest.fixture
def memoria_real(tmp_path):
    if not vm.ARQ_MEMORIA.exists():
        pytest.skip("Corpus de video local nao disponivel")
    return vm.Memoria(arq=vm.ARQ_MEMORIA, estado=tmp_path / "estado.json")


@pytest.mark.parametrize("pergunta", [
    "O que você sabe sobre AmigoDev?", "Quem é a Amiga?", "Quem é o AmigoJogos?",
    "Meu café esfriou", "Qual é a diferença entre CPU e GPU?",
    "Minha memória tá uma bagunça, como eu organizo?",
    "Tu tá lerda hoje, hein?", "Nossa Célia, tu tá toda metida hoje.",
    "Organizei os canais de estudo, jogo e música no servidor.",
    "Oi, gostei de você. Prazer em conhecer!",
    "Ih, olha ela se achando a rainha do servidor!",
    "Não lembro o que falei ontem. Tem uma forma prática de organizar meu histórico?",
])
def test_nao_injeta_video_sem_evidencia_relevante(memoria_real, pergunta):
    assert memoria_real.lembrar(pergunta, owner=True, atualizar=False)["bloco"] == ""


def test_xadrez_nao_confunde_jogo_da_velha(memoria_real):
    for pergunta in ("Você gosta de xadrez?", "Lembra quando eu te ensinei a jogar xadrez?"):
        r = memoria_real.lembrar(pergunta, owner=True, atualizar=False)
        assert "xadrez" in r["bloco"].lower()
        assert "jogo da velha" not in r["bloco"].lower()


def test_gosto_de_comer_recupera_preferencia_nao_episodio(memoria_real):
    r = memoria_real.lembrar("O que você gosta de comer?", owner=True, atualizar=False)
    assert r["resultados"]
    assert all(x["item"]["kind"] == "gosto" for x in r["resultados"])
    assert "cookies" in r["bloco"].lower()


def test_memoria_positiva_e_lore_preservadas(memoria_real):
    for frase, esperado in (
        ("Lembra do Natal?", "natal"),
        ("Tu ainda gosta de Bitcoin?", "bitcoin"),
    ):
        bloco = memoria_real.lembrar(frase, owner=True, atualizar=False)["bloco"]
        assert esperado in bloco.lower()
    assert "lore" in memoria_real.lembrar("Gosta de Bitcoin?", atualizar=False)["bloco"].lower()


def test_falsa_lembranca_nao_puxa_video_aleatorio(memoria_real):
    r = memoria_real.lembrar("Lembra daquele vulcão na Lua que a gente filmou?", owner=True, atualizar=False)
    assert "nenhum" not in r["bloco"].lower() or "não há registro" in r["bloco"].lower()
    assert "admita que não lembra" in r["bloco"].lower()
    assert "«" not in r["bloco"]


def test_pergunta_sobre_pessoa_nao_segue_palavra_sabe(memoria_real):
    r = memoria_real.buscar("O que você sabe sobre AmigoDev?", owner=True)
    assert r == []
