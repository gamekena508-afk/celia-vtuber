"""Erro/aviso de infraestrutura nunca é fala da Célia (tentativa de gravação de 2026-10-21, 14:21-14:25)."""
import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.core.dialogue_hygiene import clean_dialogue, is_infrastructure_text  # noqa: E402

# Textos REAIS da tentativa e do histórico.
INFRA = [
    "Error calling the chat endpoint: todos os provedores da fila falharam.",
    "Error calling the chat endpoint: Rate limit exceeded.Please try again later.See the logs for details.",
    "Error calling the chat endpoint: Connection error.Failed to connect to the LLM API.Check the configurations",
    "As ferramentas estao temporariamente indisponiveis.Tente novamente em instantes.",
    "🔴 Todas as rotas de IA estão indisponíveis. Não consigo responder agora.",
    "🔴 O reconhecimento de voz está indisponível. Não consigo te ouvir agora.",
]
FALA_DA_CELIA = [
    "Oxe, hum o que?",
    "[teasing] Ahhh o que, cara?",
    "Sério isso? Vida? Tá falando de Minecraft, Pai.",
    "Claro, Pai, tá preparado. O que tá rolando?",
    # fala real que MENCIONA falha/erro sem ser a mensagem do sistema: continua sendo fala dela
    "O erro foi meu, cara. Entendi errado o que tu pediu.",
    "A rota de IA caiu e eu fiquei calada. Desculpa, Pai.",
    "Não consigo responder agora porque tô rindo da tua cara.",
    "",
]


@pytest.mark.parametrize("texto", INFRA)
def test_infrastructure_text_is_recognised(texto):
    assert is_infrastructure_text(texto) is True


@pytest.mark.parametrize("texto", FALA_DA_CELIA)
def test_real_celia_speech_is_never_classified_as_infrastructure(texto):
    assert is_infrastructure_text(texto) is False


def test_the_notice_sender_is_infrastructure_whatever_the_text_says():
    assert is_infrastructure_text("qualquer coisa", nome="Aviso") is True
    assert is_infrastructure_text("qualquer coisa", nome="Célia") is False


def test_clean_dialogue_drops_infra_but_keeps_what_criador_said():
    historico = [
        {"role": "user", "content": "Tá pronto? Tá pronta essa?"},
        {"role": "assistant", "content": INFRA[0]},
        {"role": "user", "content": "Opa."},
        {"role": "assistant", "content": "[teasing] Opa o quê?"},
        {"role": "ai", "content": INFRA[4], "name": "Aviso"},
    ]
    original = copy.deepcopy(historico)
    limpo = clean_dialogue(historico)
    assert [m["content"] for m in limpo] == ["Tá pronto? Tá pronta essa?", "Opa.", "[teasing] Opa o quê?"]
    assert historico == original, "o registro original não pode ser alterado: auditoria"


def test_clean_dialogue_handles_multimodal_text_blocks():
    historico = [{"role": "assistant", "content": [{"type": "text", "text": INFRA[0]}]}, {"role": "assistant", "content": "ok"}]
    assert [m["content"] for m in clean_dialogue(historico)] == ["ok"]
