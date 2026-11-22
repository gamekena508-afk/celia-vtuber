from celia.core.asr_turn_gate import (
    AsrTurnAssembler,
    AsrTurnFragment,
    classify_fragment,
    has_language_shape_mismatch,
    is_explicit_short_command,
    secondary_min_similarity,
    secondary_verification_reason,
    should_verify_secondary,
    transcript_similarity,
)


def frag(text, *, uid="u1", voiced=500, at=10.0, rms=0.04, peak=0.4, duration=1400):
    return AsrTurnFragment(
        utterance_id=uid,
        raw_text=text,
        text=text,
        duration_ms=duration,
        voiced_ms=voiced,
        rms=rms,
        peak=peak,
        captured_at=at,
    )


def test_ghost_e_ai_below_180ms_never_becomes_a_turn():
    result = classify_fragment(frag("E aí", voiced=85, rms=0.016, peak=0.38))
    assert result.action == "REJECT"
    assert result.reason == "voiced_below_turn_floor"


def test_explicit_short_commands_are_narrow_and_need_real_audio():
    assert is_explicit_short_command("joga")
    assert is_explicit_short_command("Célia, joga")
    assert not is_explicit_short_command("E aí")
    assert classify_fragment(frag("joga", voiced=150, rms=0.03, peak=0.4)).action == "SUBMIT"
    assert classify_fragment(frag("joga", voiced=70, rms=0.03, peak=0.4)).action == "REJECT"


def test_medium_short_fragment_is_held_then_merged_with_next_turn():
    gate = AsrTurnAssembler(merge_window_ms=1400)
    first = gate.push(frag("e você também disse", uid="a", voiced=255, at=10.0))
    assert first.action == "HOLD"
    second = gate.push(frag("que eu podia jogar", uid="b", voiced=800, at=10.7))
    assert second.action == "SUBMIT"
    assert second.fragment.text == "e você também disse que eu podia jogar"
    assert second.fragment.utterance_id == "b"
    assert second.merged_from == ("a", "b")


def test_two_short_fragments_can_become_one_valid_turn():
    gate = AsrTurnAssembler()
    assert gate.push(frag("eu também", uid="a", voiced=250, at=1.0)).action == "HOLD"
    result = gate.push(frag("disse isso", uid="b", voiced=260, at=1.5))
    assert result.action == "SUBMIT"
    assert result.fragment.text == "eu também disse isso"
    assert result.fragment.voiced_ms == 510


def test_stale_held_fragment_is_dropped_not_submitted_later():
    gate = AsrTurnAssembler(merge_window_ms=500)
    held = frag("fragmento", uid="old", voiced=250, at=1.0)
    gate.push(held)
    result = gate.push(frag("frase completa", uid="new", voiced=800, at=2.0))
    assert result.action == "SUBMIT"
    assert result.fragment.text == "frase completa"
    assert result.dropped_fragment.utterance_id == "old"


def test_secondary_verification_only_targets_measured_risk():
    assert should_verify_secondary("Vai de agnosticando, vai e vindo.", voiced_ms=1100)
    assert not should_verify_secondary("E aí", voiced_ms=250)
    assert should_verify_secondary("Lutar vendo o jogo, na tela.", voiced_ms=1600)
    assert should_verify_secondary("Sarp kjúgdurta hjá unha.", voiced_ms=1600)
    assert not should_verify_secondary("Essa frase está clara e completa.", voiced_ms=1300)


def test_language_shape_mismatch_only_requests_verification_and_never_rewrites():
    assert has_language_shape_mismatch("Sarp kjúgdurta hjá unha.")
    assert not has_language_shape_mismatch("Célia, faz cara de zangada.")
    assert secondary_verification_reason(
        "Sarp kjúgdurta hjá unha.", voiced_ms=1600
    ) == "language_shape_mismatch"
    assert secondary_verification_reason(
        "Lutar vendo o jogo, na tela.", voiced_ms=1600
    ) == "measured_pattern"
    assert secondary_verification_reason(
        "Stella, vai ficar um sinan.", voiced_ms=1600
    ) == "measured_pattern"


def test_reproduced_recording_patterns_require_stricter_secondary_agreement():
    assert secondary_min_similarity("measured_pattern", 0.58) == 0.82
    assert secondary_min_similarity("language_shape_mismatch", 0.58) == 0.72
    assert secondary_min_similarity("short_voiced_fragment", 0.58) == 0.58


def test_transcript_similarity_flags_strong_divergence():
    assert transcript_similarity("Célia, joga", "Célia joga") > 0.8
    assert transcript_similarity("Dá pra discutir opiniões sim", "Opa, estamos juntos para o PNC") < 0.58
    assert transcript_similarity(
        "Lutar vendo o jogo, na tela.",
        "Lutava no jogo, lutava no jogo, na tela.",
    ) < secondary_min_similarity("measured_pattern", 0.58)
