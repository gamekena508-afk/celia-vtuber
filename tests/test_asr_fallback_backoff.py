"""Um blip do ASR primário não pode condenar o dono a 2 minutos de ASR lento.

Medido em 2026-10-01 nesta máquina, mesmo WAV de 2 s nos dois endpoints:

    primário :20127 groq/whisper-large-v3-turbo ....... 1,035 s
    fallback :8010  openai/whisper-large-v3-turbo ..... 3,852 s   (3,7× mais lento)

O hold era fixo em 120 s. Os logs de 04:51–04:58 registram 11
`primario falhou (ConnectError); caindo para ASR local por 120s`. Cada queda
isolada custava dois minutos de transcrição 3,7× mais lenta — a origem medida
das transcrições de 3–4 s que o dono relatou.

A histerese continua existindo (não pode alternar a cada frase quando o
primário está realmente morto), mas agora é progressiva.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from celia.asr.openai_compatible_asr import (  # noqa: E402
    FALLBACK_HOLD_BASE_S,
    FALLBACK_HOLD_MAX_S,
    _fallback_hold_seconds,
)


def test_primeira_falha_segura_pouco_e_nao_dois_minutos():
    """O caso comum é um blip: o primário volta logo e é 3,7x mais rápido."""
    assert _fallback_hold_seconds(1) == FALLBACK_HOLD_BASE_S
    assert _fallback_hold_seconds(1) <= 10.0, (
        "uma queda isolada do primário voltou a prender o dono no ASR lento"
    )


def test_falhas_seguidas_escalam_ate_o_teto_antigo():
    """CONTROLE: a histerese não foi destruída, só deixou de ser imediata."""
    assert _fallback_hold_seconds(2) == 16.0
    assert _fallback_hold_seconds(3) == 32.0
    assert _fallback_hold_seconds(4) == 64.0
    assert _fallback_hold_seconds(5) == FALLBACK_HOLD_MAX_S
    assert _fallback_hold_seconds(50) == FALLBACK_HOLD_MAX_S, "o teto tem de segurar"


def test_hold_nunca_e_zero_nem_negativo():
    """Sem hold nenhum, o sistema alternaria gateway/local a cada frase."""
    for n in range(0, 8):
        assert _fallback_hold_seconds(n) > 0


def test_sucesso_no_primario_zera_o_backoff():
    """Depois que o primário volta, a próxima queda recomeça curta."""
    from celia.asr.openai_compatible_asr import VoiceRecognition

    asr = VoiceRecognition.__new__(VoiceRecognition)
    asr._falhas_primario = 4

    # Simula o trecho de sucesso de transcribe_np: estado pronto zera a contagem.
    asr._falhas_primario = 0

    assert _fallback_hold_seconds(asr._falhas_primario + 1) == FALLBACK_HOLD_BASE_S


def test_o_modulo_nao_guarda_mais_um_hold_fixo_de_120():
    """Regressão de leitura: se alguém reintroduzir FALLBACK_HOLD_S, isto acusa."""
    import celia.asr.openai_compatible_asr as mod

    assert not hasattr(mod, "FALLBACK_HOLD_S"), (
        "FALLBACK_HOLD_S voltou; o hold fixo de 120 s era a causa da lentidão"
    )
