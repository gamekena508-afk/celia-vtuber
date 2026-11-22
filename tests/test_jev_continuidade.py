"""Continuidade curta no Jev (2026-10-08): "Tá, Célia." é confirmação, "Bora, Célia." é avanço.

Defeito medido: `ta` estava em `_CALL_FILLERS`, então "Tá, Célia." virava `chamado`
("Criador está chamando a Célia") e "Bora/Beleza/Ok, Célia." virava `comentario`
("Reagir ao que ele disse"), sem nenhuma noção de que a fala continua o que já acontecia.

Controle positivo: falas que TÊM de virar `continuidade`.
Controle negativo: falas que TÊM de continuar no caminho antigo (chamado, pergunta, ação...).
Sem rede e sem modelo: só a função `interpret` e a ficha.
"""
from __future__ import annotations

import pytest

from celia.core import jev as jev_core
from celia.core.jev import Jev, interpret

T0 = 1_000_000.0


@pytest.mark.parametrize("text", [
    "Tá, Célia.",
    "Bora, Célia.",
    "Tá bom, Célia.",
    "Beleza, Célia.",
    "Ok, Célia.",
    "Vamos, Célia.",
    "Aham, Célia.",
    "Bora!",
    "tá bom",
    "Partiu, Célia.",
])
def test_positive_control_confirmation_and_advance_are_continuity(text):
    reading = interpret(text)
    assert reading.intent == "continuidade", (text, reading.intent)
    assert reading.expected == "seguir_situacao"
    assert reading.conversational_only  # nunca carrega pedido de ferramenta


@pytest.mark.parametrize("text, intent", [
    ("Célia?", "chamado"),
    ("Célia.", "chamado"),
    ("Célia, tá me ouvindo? Fala comigo.", "chamado"),
    ("Célia, tá me ouvindo, Célia, oi?", "chamado"),
    ("Tá me ouvindo?", "pergunta"),
    ("Tá?", None),
    ("Bora abrir o navegador, Célia", "pedido_acao"),
    ("Abre o YouTube pra mim, Célia", "pedido_acao"),
    ("Foi demais, Célia pato.", "comentario"),
    ("Caraca, Célia tá fraca, hein?", "comentario_sobre_celia"),
    ("Célia, nós já estamos aqui no Blender, tá vendo?", "pergunta"),
    ("Não, Célia, eu quis dizer o modelo 3D", "correcao"),
    ("Opa Célia, e aí tá tudo bem?", "cumprimento"),
    ("Bom dia, Célia", "cumprimento"),
])
def test_negative_control_everything_else_keeps_its_old_reading(text, intent):
    reading = interpret(text)
    assert reading.intent != "continuidade", (text, reading.intent)
    if intent is not None:
        assert reading.intent == intent, (text, reading.intent)


def test_ficha_says_it_is_not_a_new_call_and_fits_the_item_limit():
    ficha = Jev().build("Tá, Célia.", speaker="Criador", owner=True, profile="gravacao", now=T0)
    expected = jev_core.EXPECTED_BEHAVIOR["seguir_situacao"]
    assert len(expected) <= jev_core.ITEM_LIMIT  # item maior que o limite some em silêncio
    assert expected in ficha.text
    assert "está chamando a Célia" not in ficha.text
    assert "entre na gravação agora" not in ficha.text  # inferência de chamado, não vale aqui
    assert "confirmou ou mandou seguir" in ficha.text
    assert "Tipo: continuidade curta" in ficha.text
    assert "«" not in expected and '"' not in expected


def test_continuity_keeps_the_topic_and_the_last_line_of_the_session():
    state = Jev()
    state.observe_user("Estou montando o modelo no Blender, olha o rig", speaker="Criador", owner=True, now=T0 - 60)
    state.observe_celia("[teasing] Rig torto, hein.", now=T0 - 30)
    state.observe_user("Bora, Célia.", speaker="Criador", owner=True, now=T0)
    ficha = state.build("Bora, Célia.", speaker="Criador", owner=True, profile="gravacao", now=T0)
    assert "construção do modelo/avatar 3D da Célia" in ficha.text
    assert "Rig torto, hein." in ficha.text
    assert jev_core.EXPECTED_BEHAVIOR["seguir_situacao"] in ficha.text
