from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_framebuffer_diagnostic_is_opt_in_and_still_available_manually():
    source = (ROOT / "frontend" / "celia-mascaras.js").read_text(encoding="utf-8")

    assert "setInterval(medirPintura" not in source
    assert "const provaPinturaAtiva" in source
    assert "medirPintura," in source
    assert "if (provaPinturaAtiva)" in source


def test_canvas_and_native_pet_stage_cleanup_coalesce_dom_work_to_animation_frames():
    quality = (ROOT / "frontend" / "celia-live2d-quality.js").read_text(encoding="utf-8")
    stage = (ROOT / "frontend" / "celia-palco.js").read_text(encoding="utf-8")
    body = (ROOT / "frontend" / "celia-live2d-body.js").read_text(encoding="utf-8")
    pet = (ROOT / "frontend" / "celia-pet.js").read_text(encoding="utf-8")

    assert "new ResizeObserver(scheduleRepair)" in quality
    assert "requestAnimationFrame(repairCanvasResolution)" in quality
    assert "function petBustScale()" not in quality
    assert "canvasAttributeObserver" not in quality
    assert "matrix.scale(BUST_MODEL_SCALE, BUST_MODEL_SCALE);" not in pet
    assert "frameWhenModelReady" not in pet
    assert 'celiaRosto.aplicar(0, "neutral")' not in pet
    assert "matrix.scale(next.scale, next.scale);" in body
    assert "restoreWhenModelReady" in body
    assert "new MutationObserver(scheduleCleanup)" in stage
    assert "requestAnimationFrame(() =>" in stage
    assert "setInterval(limparInterface" not in stage
    assert 'filho.style.setProperty("display", "none", "important")' in stage
    assert '"celia-confusion-accessory"' in stage
    confusion = (ROOT / "frontend" / "celia-confusao.js").read_text(encoding="utf-8")
    assert 'window.addEventListener("celia-emotion", handleEmotionEvent)' in confusion
    assert "CeliaConfusionWebSocket" not in confusion
    assert "matrix.transformX" in confusion
    assert "matrix.transformY" in confusion
    # 2026-08-24: a asserção antiga procurava `if (isNativePet()) return;`, uma
    # forma que `celia-live2d-body.js` já não tem nem no HEAD do git — o teste
    # estava vermelho antes desta sessão. A garantia real é mais forte: no Pet
    # nativo o HUD é removido e um observador impede que a casca compilada o
    # remonte.
    assert "if (isNativePet()) {" in body
    assert "removeHud();" in body
    assert "new MutationObserver(() => {" in body
    assert "restoreWhenModelReady();" in body
    assert "setInterval(refreshScope, 250)" not in body
    assert 'addEventListener("celia-browser-pet-mode", refreshScope)' in body


def test_native_pet_persists_bounded_live2d_motion_telemetry():
    main = (ROOT / "tools" / "pet-electron" / "main.js").read_text(encoding="utf-8")
    preload = (ROOT / "tools" / "pet-electron" / "preload.js").read_text(encoding="utf-8")

    assert 'live2d_motion_telemetry.jsonl' in main
    assert 'pet:write-motion-telemetry' in main
    assert 'line.length > 24000' in main
    assert 'size > 4 * 1024 * 1024' in main
    assert 'write_motion_telemetry' in preload
