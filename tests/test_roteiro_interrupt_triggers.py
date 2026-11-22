import pytest

from src.celia.core.interruption_intent import is_explicit_roteiro_stop


@pytest.mark.parametrize("text", [
    "Calma, Célia.",
    "Calma, série.",
    "Parou, série.",
    "Célia, para.",
    "Célia, por favor, para.",
    "Espera aí, Célia.",
    "Segura, Célia.",
    "Corta, Célia.",
    "Silêncio, Célia.",
    "Para de falar, Célia.",
    "Célia, cala a boca.",
    "Volta, Célia.",
])
def test_explicit_roteiro_stop_accepts_strong_commands(text):
    assert is_explicit_roteiro_stop(text) is True


@pytest.mark.parametrize("text", [
    "Cara, cara, cara.",
    "Exciting!",
    "E aí a UFM, vamos ver.",
    "Ô tristeza, queria só gravar com a minha filha.",
    "Voltando ao assunto.",
    "Calma.",
    "Para.",
    "Silêncio.",
    "Célia, o vídeo para quando eu clico.",
    "A cena para quando muda o beat.",
    "Célia, espera que eu vou explicar como isso funciona primeiro.",
    "Célia, para mim isso funciona.",
    "Célia, calma que isso era parte do roteiro.",
    "Célia disse para de falar no vídeo.",
    "A série parou ontem.",
])
def test_explicit_roteiro_stop_rejects_ordinary_speech(text):
    assert is_explicit_roteiro_stop(text) is False
