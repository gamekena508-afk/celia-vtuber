import pytest

from celia.core.asr_normalization import (
    evaluate_asr_acoustic_gate,
    is_protected_celia_call,
    normalize_asr_transcript,
)


def norm(text, context="", **audio):
    defaults = {
        "duration_ms": 1200,
        "rms": 0.03,
        "peak": 0.3,
        "voiced_ms": 500,
    }
    defaults.update(audio)
    return normalize_asr_transcript(text, context_summary=context, **defaults)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Séria, olha isso.", "Célia, olha isso."),
        ("Oi, séria, fala comigo.", "Oi, Célia, fala comigo."),
        ("Fale alguma coisa, sério.", "Fale alguma coisa, Célia."),
        ("Calma, série.", "Calma, Célia."),
        ("Oi Cedra, tudo bem?", "Oi Célia, tudo bem?"),
        ("SÉRIAAA", "Célia"),
        ("Como assim, séria?", "Como assim, Célia?"),
        ("Oi, oi, oi, séria, séria.", "Oi, oi, oi, Célia, Célia."),
        ("Ei, Céria!", "Ei, Célia!"),
        ("Oi, Celía", "Oi, Célia"),
        ("Selha, faz, show.", "Célia, faz, show."),
        ("Selio, joga.", "Célia, joga."),
        ("Celya, olha isso.", "Célia, olha isso."),
        ("Selya, olha isso.", "Célia, olha isso."),
        ("Stella, joga.", "Célia, joga."),
        ("Fala comigo, Felion.", "Fala comigo, Célia."),
        ("Oi, Félion!", "Oi, Célia!"),
    ],
)
def test_celia_vocative_is_contextual(raw, expected):
    result = norm(raw)
    assert result.text == expected

@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Essa situação é séria.", "Essa situação é séria."),
        ("Eu estava falando séria.", "Eu estava falando séria."),
        ("Pegava a palavra séria.", "Pegava a palavra séria."),
        ("Ela parece séria hoje.", "Ela parece séria hoje."),
        ("Isso é sério.", "Isso é sério."),
        ("Como assim, sério?", "Como assim, Célia?"),
        ("Essa série é boa.", "Essa série é boa."),
    ],
)
def test_ambiguous_names_require_context(raw, expected):
    result = norm(raw)
    assert result.text == expected
    assert ("celia_dictionary" in result.rules) == (raw != expected)


def test_transcricao_confusions_require_asr_context():
    context = "Criador: o Whisper errou a transcrição na interface do ASR"
    assert norm(
        "Como tu tá vendo aí, a descrição vai te ajudar.", context
    ).text == "Como tu tá vendo aí, a transcrição vai te ajudar."
    assert norm(
        "a transição tem que aparecer na interface", context
    ).text == "a transcrição tem que aparecer na interface"
    assert norm("A descrição do vídeo ficou boa.", "").text == "A descrição do vídeo ficou boa."
    assert norm("A descrição da interface ficou boa.", "").text == "A descrição da interface ficou boa."

def test_observed_project_vocabulary_rules_are_bounded():
    assert norm("Mas ainda tem muito o que marar.").text == "Mas ainda tem muito o que melhorar."
    assert norm("Sem boogies, né?", "debug dos bugs no código").text == "Sem bugs, né?"
    assert norm(
        "No ISP Na série, tá indo pro blog dela agora", "ASR debug log"
    ).text == "No ISP Na série, tá indo pro log dela agora"
    assert norm(
        "A postagem foi pro blog dela agora", ""
    ).text == "A postagem foi pro blog dela agora"


def test_observed_cohost_activation_phrase_is_normalized_only_in_technical_context():
    result = norm(
        "Briga de ativação da Modo Co-Host.",
        "estamos falando do gatilho de ativação do modo co-host",
    )
    assert result.text == "gatilho de ativação do modo co-host."
    assert "cohost_activation_phrase" in result.rules

@pytest.mark.parametrize(
    ("text", "duration_ms", "rms", "peak"),
    [
        ("E aí", 405, 0.0007, 0.0070),
        ("Obrigado.", 448, 0.0012, 0.0123),
        ("Legenda por Sônia Ruberti", 405, 0.0012, 0.0086),
    ],
)
def test_recurrent_whisper_vices_are_rejected_only_with_weak_audio(
    text, duration_ms, rms, peak
):
    result = norm(
        text,
        duration_ms=duration_ms,
        rms=rms,
        peak=peak,
        voiced_ms=190,
    )
    assert result.rejected is True
    assert result.decision == "REJECT"
    assert result.text == ""


@pytest.mark.parametrize("text", ["E aí", "Obrigado.", "Tchau.", "Oi."])
def test_real_short_phrases_with_good_audio_are_not_blocked(text):
    result = norm(text, duration_ms=900, rms=0.05, peak=0.5, voiced_ms=430)
    assert result.rejected is False
    assert result.text == text

def test_garbage_is_not_reconstructed_into_an_invented_sentence():
    raw = "No ISP Na série, tá indo pro blog dela agora"
    result = norm(raw, "ASR debug log")
    assert result.text.startswith("No ISP Na série")
    assert "log dela" in result.text
    assert "Whisper" not in result.text
    assert result.decision == "CORRECT"


def test_keep_preserves_raw_exactly_when_no_rule_applies():
    raw = "Abre o jogo da velha"
    result = norm(raw)
    assert result.raw_text == raw
    assert result.text == raw
    assert result.decision == "KEEP"
    assert result.rules == ()


def test_acoustic_gate_rejects_preroll_click_but_allows_strong_short_call():
    weak = evaluate_asr_acoustic_gate(
        duration_ms=403,
        rms=0.0007,
        peak=0.02,
        voiced_ms=43,
        min_voiced_ms=180,
    )
    assert weak.allow_transcription is False
    assert weak.decision == "REJECTED_MIN_VOICED"

    strong = evaluate_asr_acoustic_gate(
        duration_ms=420,
        rms=0.04,
        peak=0.45,
        voiced_ms=150,
        min_voiced_ms=180,
    )
    assert strong.allow_transcription is True
    assert strong.reason == "short_voiced_but_acoustically_strong"


@pytest.mark.parametrize(
    "text",
    ["Célia", "Célia!", "Célia?", "Oi, Célia", "Como assim, Célia?"],
)
def test_short_celia_calls_are_protected_from_self_echo(text):
    assert is_protected_celia_call(text) is True


def test_long_or_non_celia_text_is_not_protected_from_self_echo():
    assert is_protected_celia_call("Isso é sério.") is False
    assert is_protected_celia_call("Célia falou uma frase longa demais para ser só uma chamada direta agora") is False


@pytest.mark.parametrize(("raw", "expected"), [
    ("E aí, Sérgio, tudo bom?", "E aí, Célia, tudo bom?"),
    ("Oi, Sergio, tudo bem?", "Oi, Célia, tudo bem?"),
    ("O Sérgio não era pra ser Sérgio, era pra ser Sélia.",
     "O Sérgio não era pra ser Sérgio, era pra ser Sélia."),
    ("Era pra ser séria, mas só que a gente saiu errado.",
     "Era pra ser Célia, mas só que a gente saiu errado."),
])
def test_confirmed_september_21_errors(raw, expected):
    result = norm(raw, context="Erro na transcrição ASR da Célia")
    assert result.raw_text == raw
    assert result.text == expected
    assert result.decision == ("CORRECT" if raw != expected else "KEEP")


@pytest.mark.parametrize(("raw", "expected"), [
    ("Oi, essa série é boa.", "Oi, essa série é boa."),
    ("Oi, estou falando sério.", "Oi, estou falando sério."),
    ("Oi, Sérgio chegou hoje.", "Oi, Sérgio chegou hoje."),
    ("O Sérgio é meu amigo.", "O Sérgio é meu amigo."),
    ("As correções como Sérgio", "As correções como Sérgio"),
    ("Oi, Célia, essa série é boa.", "Oi, Célia, essa série é boa."),
    ("Ela disse a palavra séria.", "Ela disse a palavra séria."),
])
def test_dictionary_preserves_ordinary_context(raw, expected):
    assert norm(raw, context="Transcrição ASR da Célia").text == expected


def test_reported_asr_error_requires_asr_context():
    raw = "Era pra ser séria, mas virou comédia."
    assert norm(raw).text == raw


# Variações de reconhecimento do nome da IA observadas nos logs de 2026-10-03.
# Todas são erros do Whisper e todas devem virar Célia.
OBSERVED_CELIA_VARIANTS = [
    "Helen", "Elen", "Helén", "Heleny",
    "Sérion", "Serion", "Sérgio", "Sergio", "Cério", "Serio",
    "Célia", "Celia", "Célio", "Celio", "Sélia", "Selia",
]


@pytest.mark.parametrize("raw", OBSERVED_CELIA_VARIANTS)
def test_observed_whisper_variants_become_celia(raw):
    assert norm(raw).text == "Célia"
    assert norm(raw.lower()).text == "Célia"
    assert norm("Oi, " + raw + ", tudo bem?").text == "Oi, Célia, tudo bem?"


@pytest.mark.parametrize("text", ["Helena chegou.", "Elena chegou.", "Serionista fala.", "Ceriol fala."])
def test_celia_dictionary_only_matches_whole_words(text):
    assert norm(text).text == text


# --- 2026-10-18: Cerno / Proxi (chamada) e Postinho (= Oxi). Casos reais do log celia-20261018T083518Z.log. ---

@pytest.mark.parametrize(
    ("raw", "expected", "rule"),
    [
        ("Postinho.", "Oxi.", "oxi_dictionary"),
        ("Postinho, você está aí?", "Oxi, você está aí?", "oxi_dictionary"),
        ("Oi, postinho", "Oi, Oxi", "oxi_dictionary"),
        ("Cerno", "Célia", "celia_call_alias"),
        ("Cerno.", "Célia.", "celia_call_alias"),
        ("Tá me ouvindo, Cerno, oi?", "Tá me ouvindo, Célia, oi?", "celia_call_alias"),
        ("Proxi, você vai falar isso?", "Célia, você vai falar isso?", "celia_call_alias"),
        ("Ei, Proxi!", "Ei, Célia!", "celia_call_alias"),
        ("PROXI, fala", "Célia, fala", "celia_call_alias"),
    ],
)
def test_aliases_de_2026_10_18_sao_corrigidos(raw, expected, rule):
    result = norm(raw)
    assert result.text == expected
    assert result.raw_text == raw  # a transcricao original fica para diagnostico
    assert rule in result.rules
    assert result.decision == "CORRECT"


@pytest.mark.parametrize(
    "texto",
    [
        "Oxi.",                         # ja esta certo
        "Oxi, você está aí?",
        "Eu cerno a farinha.",          # verbo real, sem vocativo
        "Eu cerno, o trigo.",           # virgula depois, mas nao e chamada
        "Vi o proxi do bug.",           # nao isolado
        "Fui ao postinho de saúde.",    # palavra real: "de" depois
        "O postinho abriu cedo.",       # artigo antes
        "Moro perto do postinho.",
        "Postinhos de saúde fecham.",   # plural nao e a palavra inteira
        "Aproximo e cerco o alvo.",     # sequencias parecidas dentro de outras palavras
    ],
)
def test_palavras_legitimas_e_contexto_ambiguo_nao_mudam(texto):
    result = norm(texto)
    assert result.text == texto
    assert result.decision == "KEEP"


def test_correcao_nao_apaga_pontuacao_nem_resto_da_frase():
    assert norm("Cerno, você vai falar isso, né?").text == "Célia, você vai falar isso, né?"
    assert norm("E aí, postinho, tudo bem?").text == "E aí, Oxi, tudo bem?"


def test_controle_negativo_sabotagem_remove_a_regra(monkeypatch):
    # prova que o teste enxerga a regra: sem o alias na lista, 'Proxi' volta a passar sem correção
    import celia.core.asr_normalization as mod

    monkeypatch.setattr(mod, "_CALL_ALIAS_RE", mod.re.compile(r"(?!x)x"))
    assert norm("Proxi, você vai falar isso?").text == "Proxi, você vai falar isso?"


def test_postinho_isolado_corrigido_antes_do_filtro_de_duracao():
    # O log mostrou 'Postinho.' (voiced_ms=127) rejeitado por voiced_below_turn_floor DEPOIS da normalizacao:
    # a normalizacao nao rejeita (o rejeitador e core/asr_turn_gate.py) e entrega 'Oxi.' ao gate.
    result = norm("Postinho.", duration_ms=1233, rms=0.024, peak=0.28, voiced_ms=127)
    assert result.rejected is False and result.text == "Oxi."
