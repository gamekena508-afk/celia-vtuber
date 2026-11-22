"""Recording turns stop forcing a 15-50 word reply (Fatia 3, 2026-10-02).

Measured: "Ah, calma, deixa eu ativar a visão da Célia." received
"TAMANHO: 15-50 palavras" + "Inicios possiveis: Então,..." and the 70B produced
"Então, você acha que a visão da Célia vai melhorar isso aqui?".
Decision 4: in recording, an action/statement asks for a short reaction (3-12
words); a question keeps the normal template. Chat and system turns unchanged.
"""
from celia.core.recording_reply_shape import shape_emotion_prompt, is_question

TEMPLATE = (
    "--- ESTADO EMOCIONAL ATUAL (Célia) ---\nEmocao: NEUTRAL\n\n"
    "TAMANHO: 15-50 palavras\nPontuacao preferida: .\n"
    "Inicios possiveis: Então,, Bom,, Olha,, Certo,\nGirias BR: 35%\n"
    "--- FIM DO ESTADO EMOCIONAL ---"
)


def test_recording_statement_gets_short_reaction():
    out = shape_emotion_prompt(TEMPLATE, "Ah, calma, deixa eu ativar a visão da Célia.", "gravacao", "owner")
    assert "TAMANHO: 3-12 palavras" in out
    assert "15-50" not in out
    assert "Inicios possiveis" not in out
    assert "FIM DO ESTADO EMOCIONAL" in out  # block stays parseable by the compactor


def test_recording_question_keeps_template():
    assert shape_emotion_prompt(TEMPLATE, "Tá vendo o site?", "gravacao", "owner") == TEMPLATE


def test_chat_and_system_turns_unchanged():
    text = "deixa eu ativar a visão"
    assert shape_emotion_prompt(TEMPLATE, text, "chat", "owner") == TEMPLATE
    assert shape_emotion_prompt(TEMPLATE, text, "gravacao", "system") == TEMPLATE


def test_is_question_controls():
    for q in ["Tá vendo o site?", "o que você achou", "qual que é melhor", "cadê o cabelo", "como ficou"]:
        assert is_question(q), q
    for s in ["Ah, calma, deixa eu ativar a visão da Célia.", "Eu comecei por esse site aqui, ó.", "Fica comigo que eu te mostro."]:
        assert not is_question(s), s
