"""ASR: variantes de "Minecraft", interjeição sozinha e continuação de frase (tentativa de gravação de 2026-10-21)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.core.asr_normalization import normalize_asr_transcript  # noqa: E402


def _norm(texto):
    return normalize_asr_transcript(texto, context_summary="")


@pytest.mark.parametrize("entrada,esperado", [
    # transcrições REAIS da tentativa
    ("Como é que tu se sente, Célia? estando dentro do Moniqueft.", "Como é que tu se sente, Célia? estando dentro do Minecraft."),
    ("A série jogará Monecraft.", "A série jogará Minecraft."),
    ("Gente, eu vou jogar Minecrafta hoje", "Gente, eu vou jogar Minecraft hoje"),
    ("Tá no Minicraft agora", "Tá no Minecraft agora"),
])
def test_minecraft_asr_variants_are_resolved_to_the_game_name(entrada, esperado):
    r = _norm(entrada)
    assert r.text == esperado
    assert "minecraft_alias" in r.rules
    assert r.raw_text == entrada, "o texto bruto do ASR tem de ficar para auditoria"


@pytest.mark.parametrize("entrada", [
    "Minecraft", "Vou minerar um pouco", "O mineiro chegou", "Esse craft ficou bom", "Olha o monitor", "Bora pro mundo novo",
    "Meu microfone tá baixo", "Que mexicano gostoso", "Uma máquina de lavar", "Fala do Manec aí",   # 'Manec' é ambíguo: não se mexe
])
def test_common_words_and_the_correct_name_are_left_alone(entrada):
    r = _norm(entrada)
    assert r.text == entrada
    assert "minecraft_alias" not in r.rules
