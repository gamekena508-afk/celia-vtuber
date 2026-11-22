from celia.core.obs_subtitles import ObsSubtitleService


def test_neuro_overlay_resets_whole_stack_on_fifth_line():
    html = ObsSubtitleService()._html("neuro")

    assert "const MAX_STACK_LINES = 4;" in html
    assert "if (stack.length >= MAX_STACK_LINES) stack = [];" in html
    assert "stack.shift()" not in html
    assert "stack = stack.filter(" not in html


def test_neuro_overlay_keeps_block_until_idle_timeout():
    html = ObsSubtitleService()._html("neuro")

    assert "if (!idleSince) idleSince = nowServer();" in html
    assert "stack = [];\n      clearCanvas();" in html
    assert "A quinta limpa o bloco e recomeca de uma." in html


def test_subtitle_boundary_strips_control_tags_without_normalizing_display_numbers():
    from celia.core.obs_subtitles import _sanitize_subtitle_text

    assert _sanitize_subtitle_text("[happy] Tenho 3 jogos. <teasing> #angry") == "Tenho 3 jogos."


def test_subtitle_boundary_drops_internal_emotion_directive():
    from celia.core.obs_subtitles import _sanitize_subtitle_text

    assert _sanitize_subtitle_text("Responde com 1 emoção. Tags: [happy] Oi Pai.") == "Oi Pai."


def test_obs_overlay_reports_render_handshake_to_embedded_pet():
    html = ObsSubtitleService()._html("neuro")
    assert 'channel: "celia-subtitle"' in html
    assert 'notifyParent("ready"' in html
    assert 'notifyParent("ws"' in html
    assert 'notifyParent("state"' in html
    assert 'notifyParent("rendered"' in html
    assert 'speech_id: activeSpeechId' in html
