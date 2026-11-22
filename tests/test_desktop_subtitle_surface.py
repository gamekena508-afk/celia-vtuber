from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_legacy_desktop_subtitle_surface_is_retired():
    source = (ROOT / "frontend" / "celia-subtitle.js").read_text(encoding="utf-8")
    html = (ROOT / "frontend" / "celia-subtitle.html").read_text(encoding="utf-8")
    assert "RETIRADO EM 2026-09-30" in source
    assert "new WebSocket" not in source
    assert "<button" not in source
    assert "data-action" not in source
    assert "celia-subtitle-controls" not in source
    assert "celia-subtitle.js" not in html



def test_native_pet_hides_the_old_black_narration_strip():
    source = (ROOT / "frontend" / "celia-face.js").read_text(encoding="utf-8")
    assert 'new URLSearchParams(window.location.search).get("pet") !== "1"' in source
    assert "if (!narracaoNoPalcoPet) return;" in source


def test_frontend_playback_has_one_authoritative_clock():
    bundles = list((ROOT / "frontend" / "assets").glob("main-*.js"))
    assert len(bundles) == 1
    bundle = bundles[0].read_text(encoding="utf-8")
    bridge = (ROOT / "frontend" / "external-playback-bridge.js").read_text(encoding="utf-8")
    assert 'audio-playback-started' not in bundle
    assert '_wavFileHandler.start(' not in bundle
    assert 'playback ACK delegated to external-playback-bridge' in bundle
    assert 'legacy WavFileHandler lipsync disabled' in bundle
    assert '__prematurePlaybackAcksSuppressed' in bridge
    assert 'sendNative(pending.socket' in bridge


def test_native_pet_has_one_canonical_embedded_subtitle_surface():
    main = (ROOT / "tools" / "pet-electron" / "main.js").read_text(encoding="utf-8")
    preload = (ROOT / "tools" / "pet-electron" / "preload.js").read_text(encoding="utf-8")
    embedded = (ROOT / "frontend" / "celia-legenda-embutida.js").read_text(encoding="utf-8")
    index = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")

    # Regra estrutural: não existe segundo dono de legenda dentro do Pet.
    for forbidden in (
        "SUBTITLE_URL",
        "SEPARATE_SUBTITLE_SURFACE",
        "subtitleWindow",
        "createSubtitleWindow",
        "pet:toggle-subtitles",
        "subtitle:get-state",
        "CELIA_SEPARATE_SUBTITLE",
        "subtitle_surface=separate",
    ):
        assert forbidden not in main
    assert "toggle_subtitles" not in preload
    assert 'parametros.get("pet") !== "1"' in embedded
    assert "subtitle_surface" not in embedded
    # O valor literal da revisão NÃO pode ser cravado aqui: o
    # docs/CONTRATO_LEGENDA_PET.md § Cache manda trocar o sufixo `?v=` toda vez
    # que celia-legenda-embutida.js muda, porque o Electron é persistente e
    # serve JS velho. Cravar a string fazia o teste reprovar justamente quem
    # cumpria o contrato. O que importa é existir cache-busting.
    import re
    assert re.search(
        r'celia-legenda-embutida\.js\?v=[A-Za-z0-9._-]+', index
    ), "a legenda embutida perdeu o sufixo ?v= e vai ficar presa em cache"



def test_embedded_pet_subtitle_has_render_watchdog_geometry_recovery_and_single_fallback():
    source = (ROOT / "frontend" / "celia-legenda-embutida.js").read_text(encoding="utf-8")
    assert "const WATCHDOG_MS = 700" in source
    assert "const MAX_RELOADS = 2" in source
    assert "sanearGeometria" in source
    assert 'ID + "-fallback"' in source
    assert "lastRenderedSpeechId" in source
    assert 'quadro.style.visibility = telemetria.fallbackActive ? "hidden" : "visible"' in source
    assert 'msg.channel !== "celia-subtitle"' in source
    assert 'msg.event === "rendered"' in source
    # O fallback vive dentro do mesmo dono canônico; não existe BrowserWindow
    # paralela de legenda para ser reativada por recuperação.
    assert "CELIA_SEPARATE_SUBTITLE" not in source
