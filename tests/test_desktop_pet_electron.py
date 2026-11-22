import json
import subprocess
from pathlib import Path


_STATE = Path(__file__).parents[1] / "tools" / "pet-electron" / "state.js"
_GEOMETRY = Path(__file__).parents[1] / "tools" / "pet-electron" / "geometry.js"
_MODE = Path(__file__).parents[1] / "tools" / "pet-electron" / "mode.js"
_LAUNCH_CONFIG = Path(__file__).parents[1] / "tools" / "pet-electron" / "launch-config.js"


def _state(script: str):
    output = subprocess.check_output(["node", "-e", script, str(_STATE)], text=True)
    return json.loads(output)


def test_electron_pet_state_rejects_invalid_geometry():
    result = _state(
        "const s=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(s.normalize({x:'bad',y:20,width:99999,height:1,locked:1})));"
    )
    assert result == {
        "x": None, "y": 20, "width": 1200, "height": 360,
        "locked": True, "enabled": False, "framing": "bust", "framingVersion": 0,
    }


def test_electron_pet_state_persists_enabled_bust_contract():
    result = _state(
        "const s=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(s.normalize({enabled:true,framing:'bust'})));"
    )
    assert result["enabled"] is True
    assert result["framing"] == "bust"
    assert result["framingVersion"] == 0
    assert (result["width"], result["height"]) == (520, 720)


def test_electron_mode_contract_allows_only_window_or_pet():
    result = subprocess.check_output(
        ["node", "-e", "const m=require(process.argv[1]);process.stdout.write(JSON.stringify([m.normalizeMode('pet'),m.normalizeMode('window'),m.normalizeMode('shell',null)]));", str(_MODE)],
        text=True,
    )
    assert json.loads(result) == ["pet", "window", None]


def test_pet_preload_keeps_compiled_mode_bridge_allowlisted():
    preload = (Path(__file__).parents[1] / "tools" / "pet-electron" / "preload.js").read_text()
    assert 'setMode: (mode) => ipcRenderer.invoke("pet:set-mode", mode)' in preload
    assert 'set_mouse_ignored: (ignored) => ipcRenderer.invoke("pet:set-ignore-mouse-events", ignored)' in preload
    assert "on_interaction_armed: onPetInteractionArmed" in preload
    assert "on_interaction_disarmed: onPetInteractionDisarmed" in preload
    assert 'onToggleInputSubtitle: () => () => {}' in preload
    assert 'updateComponentHover: () => {}' in preload
    assert "MODE_EVENTS" in preload
    assert "ipcRenderer.invoke(channel" not in preload


def test_pet_ghost_mouse_policy_and_global_interaction_are_scoped():
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    assert 'const { app, BrowserWindow, globalShortcut, ipcMain, screen } = require("electron");' in main
    assert "skipTaskbar: true" in main
    assert 'petWindow.setAlwaysOnTop(true, "screen-saver");' in main
    assert "function applyPetInputShape(ghost)" in main
    assert "petWindow.setShape({ rects });" in main
    assert "petWindow.webContents.invalidate()" in main
    assert 'ipcMain.handle("pet:set-ignore-mouse-events", setPetMouseIgnored);' in main
    assert 'if (!isPetRenderer(event)) return { ok: false, reason: "untrusted_renderer" };' in main
    assert 'typeof rawIgnored !== "boolean"' in main
    assert 'if (!rawIgnored) return { ok: false, reason: "global_shortcut_required" };' in main
    assert 'petWindow.setIgnoreMouseEvents(petMouseIgnored, { forward: true });' in main
    assert 'petWindow.webContents.on("before-input-event"' in main
    assert 'petWindow.on("blur", disarmPetInteraction);' in main
    assert 'if (petMouseIgnored) return currentState({ moved: false, reason: "interaction_not_armed" });' in main
    assert 'globalShortcut.register("Control+Space", armPetInteraction)' in main
    assert 'globalShortcut.register("Control+Escape", closePetFromShortcut)' in main
    assert 'globalShortcut.unregister("Control+Space")' in main
    assert 'globalShortcut.unregister("Control+Escape")' in main
    assert 'globalShortcut.register("Alt+Escape", closeGameWindow)' in main
    assert 'globalShortcut.unregister("Alt+Escape")' in main
    assert "function closePetFromShortcut()" in main
    assert "petWindow.close();" in main
    assert 'petWindow.webContents.send("pet:interaction-armed");' in main


def test_pet_position_uses_full_display_bounds_so_it_can_cross_the_panel():
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    assert "function displayBoundsFor(bounds)" in main
    assert "screen.getDisplayNearestPoint(point).bounds" in main
    assert "const display = screen.getPrimaryDisplay().bounds;" in main
    assert "resizeInWorkArea({ ...state, ...bounds }, rawDelta, displayBoundsFor(bounds))" in main


def test_fixed_pet_surface_is_the_default_and_window_is_the_explicit_rollback():
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    # REGRESSÃO 2026-10-01: o commit 1ddc6d9 inverteu estes dois padrões e
    # trocou o modo normal do Pet para janela limitada. O dono perdeu a pose
    # salva (que vive no model matrix, não no x/y da janela) e a roda passou a
    # redimensionar a janela, o que parece zoom.
    assert 'process.env.CELIA_PET_FULLSCREEN !== "0"' in main
    assert 'process.env.CELIA_PET_SPAN_DESKTOP !== "0"' in main
    assert 'CELIA_PET_FULLSCREEN=0' in main
    assert "function virtualDesktopBounds()" in main
    assert "function usesDesktopSpan()" in main
    assert "function petSurfaceBounds()" in main
    assert "screen.getAllDisplays()" in main
    assert 'surface_mode: usesDesktopSpan() ? "desktop-span" : (FULLSCREEN_PROBE ? "fixed-surface" : "window")' in main
    assert "fullscreen: false" in main
    assert "fullscreenable: false" in main
    assert 'backgroundColor: "#00000000"' in main
    assert "if (FULLSCREEN_PROBE) return;" in main
    assert "applyFixedPetSurfaceBounds();" in main
    assert "else if (FULLSCREEN_PROBE) petWindow.setFullScreen(true);" not in main
    assert "setIgnoreMouseEvents(true, { forward: true })" in main


def test_pet_hides_before_renderer_or_gpu_recovery():
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    assert 'petWindow.on("unresponsive"' in main
    assert 'petWindow.webContents.on("render-process-gone"' in main
    assert '"did-fail-load"' in main
    assert 'app.on("child-process-gone"' in main
    assert '"gpu-process-gone"' in main
    assert "petWindow.hide();" in main
    assert "petWindow.webContents.reloadIgnoringCache();" in main
    assert "PET_RECOVERY_MAX_ATTEMPTS = 3" in main
    assert "keeping Pet hidden" in main


def test_native_pet_removes_legacy_fps_hud_after_remounts():
    body = (Path(__file__).parents[1] / "frontend" / "celia-live2d-body.js").read_text()
    assert "function removeHud()" in body
    assert 'document.querySelectorAll("#celia-body-hud")' in body
    assert "if (isNativePet())" in body
    assert "new MutationObserver(() => {" in body
    assert "restoreWhenModelReady();" in body


def test_fixed_pet_surface_moves_model_and_wheel_never_resizes_the_window():
    pet = (Path(__file__).parents[1] / "frontend" / "celia-pet.js").read_text()
    assert "let fullscreenMode = false;" in pet
    assert "state.fixed_surface === true" in pet
    assert "Move only the Live2D model matrix" in pet
    assert "function matrixDeltaFromPixels" in pet
    assert "(dx * 2) / width" in pet
    assert "(dy * 2) / height" in pet
    assert "body.moveBy(delta.x, -delta.y);" in pet
    assert "function preserveFloorAcrossDisplays" in pet
    assert "previousBottom - nextBottom" in pet
    assert 'document.addEventListener("wheel"' in pet
    # A roda só tem dono no modo fixo. Chamar resize_by() aqui redimensiona a
    # BrowserWindow e, como o Live2D acompanha o canvas, PARECE zoom.
    assert "native.resize_by(" not in pet, "a roda voltou a redimensionar a janela"
    assert "if (!fullscreenMode) return;" in pet
    assert 'document.addEventListener("dblclick"' in pet
    assert "native.toggle_position_lock()" in pet
    assert 'document.addEventListener("keydown"' in pet
    assert 'event.key.toLowerCase() === "s"' in pet
    assert 'background: "transparent"' in pet
    assert 'backgroundColor: "transparent"' in pet
    assert "body.save()" in pet
    assert "body.restore()" not in pet
    body = (Path(__file__).parents[1] / "frontend" / "celia-live2d-body.js").read_text()
    assert "restoreWhenModelReady();" in body
    assert "scheduleSave();" in body


def test_pet_launcher_prefers_native_wayland_with_nvidia_x11_fallback():
    launcher = (Path(__file__).parents[1] / "tools" / "run_pet.sh").read_text(encoding="utf-8")
    assert 'OZONE_PLATFORM="${CELIA_PET_OZONE_PLATFORM:-}"' in launcher
    assert "AUTO_OZONE=0" in launcher
    assert 'XDG_SESSION_TYPE:-}" == "wayland"' in launcher
    assert 'OZONE_PLATFORM="wayland"' in launcher
    assert 'OZONE_PLATFORM="x11"' in launcher
    assert 'export CELIA_PET_OZONE_PLATFORM="$OZONE_PLATFORM"' in launcher
    assert 'run_electron "wayland" "$@"' in launcher
    assert 'if [[ "$status" = "2" ]]' in launcher
    assert "fallback automático para X11/NVIDIA" in launcher
    assert '"--ozone-platform=$OZONE_PLATFORM"' in launcher


def test_pet_launcher_requires_hardware_gpu_and_requests_nvidia():
    launcher = (Path(__file__).parents[1] / "tools" / "run_pet.sh").read_text()
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    assert "__NV_PRIME_RENDER_OFFLOAD" in launcher
    assert "__GLX_VENDOR_LIBRARY_NAME" in launcher
    assert "--disable-software-rasterizer" in launcher
    assert 'app.commandLine.appendSwitch("disable-software-rasterizer")' in main
    assert "requireHardwareGpu" in main
    assert "probeHardwareRenderer" in main
    assert "WEBGL_debug_renderer_info" in main
    assert "/nvidia|geforce|rtx/i" in main
    assert "connectedScanoutVendors" in main
    assert '"nvidia_render_amd_scanout"' in main
    assert "composition_accelerated" in main
    assert "recording_grade" in main
    assert "CELIA_PET_STRICT_GPU" in main
    assert "app.exit(2)" in main


def test_pet_launcher_recovers_graphical_session_for_remote_backend_start():
    launcher = (Path(__file__).parents[1] / "tools" / "run_pet.sh").read_text()
    assert "hydrate_gui_session" in launcher
    assert "plasmashell" in launcher
    assert "XAUTHORITY" in launcher
    assert "DBUS_SESSION_BUS_ADDRESS" in launcher
    assert "DISPLAY|WAYLAND_DISPLAY" in launcher


def test_second_desktop_pet_launch_focuses_the_existing_window():
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    assert "app.on(\"second-instance\", focusExistingPetWindow);" in main
    assert "if (petWindow.isMinimized()) petWindow.restore();" in main
    assert "petWindow.webContents.reloadIgnoringCache();" in main
    assert "petWindow.show();" in main
    assert "petWindow.focus();" in main


def test_native_pet_does_not_activate_the_compiled_legacy_pet_layout():
    main = (Path(__file__).parents[1] / "tools" / "pet-electron" / "main.js").read_text()
    launch_config = _LAUNCH_CONFIG.read_text()
    assert "pet_revision=20261001-pet-fixed-restored" in launch_config
    assert 'return { ok: true, mode: "window", native_surface: true };' in main
    assert 'event.sender.send("mode-changed"' not in main
    assert 'currentMode = "pet"' not in main


def test_electron_pet_wheel_resize_preserves_bounds_and_proportion():
    result = _state(
        "const s=require(process.argv[1]);"
        "const start=s.normalize({width:420,height:720});"
        "process.stdout.write(JSON.stringify([s.resized(start,240),s.resized(start,-240)]));"
    )
    assert result[0]["width"] == 660
    assert result[0]["height"] > 720
    assert result[1]["width"] == 240
    assert result[1]["height"] == 411


def test_electron_pet_repairs_offscreen_saved_state_and_resizes_inside_display():
    output = subprocess.check_output(
        [
            "node",
            "-e",
            "const g=require(process.argv[1]);"
            "const display={x:0,y:0,width:1920,height:1080};"
            "const repaired=g.constrainToWorkArea({x:679,y:-158,width:816,height:1402},display);"
            "const resized=g.resizeInWorkArea(repaired,240,display);"
            "process.stdout.write(JSON.stringify([repaired,resized]));",
            str(_GEOMETRY),
        ],
        text=True,
    )
    repaired, resized = json.loads(output)
    for state in (repaired, resized):
        assert state["x"] >= 24
        assert state["y"] >= 24
        assert state["x"] + state["width"] <= 1896
        assert state["y"] + state["height"] <= 1056


def test_legacy_subtitle_state_and_preload_are_tombstones():
    root = Path(__file__).parents[1]
    state = (root / "tools" / "pet-electron" / "subtitle-state.js").read_text()
    preload = (root / "tools" / "pet-electron" / "subtitle-preload.js").read_text()
    main = (root / "tools" / "pet-electron" / "main.js").read_text()
    assert "RETIRADO" in state
    assert "module.exports = {};" in state
    assert "RETIRADO" in preload
    assert "celiaSubtitleNative" not in preload
    assert "subtitle:" not in main
    assert "subtitleWindow" not in main


def test_windows_pet_launcher_uses_existing_electron_and_never_starts_backend():
    launcher = (Path(__file__).parents[1] / "tools" / "run_pet.ps1").read_text(encoding="utf-8")
    assert "$env:CELIA_PET_ELECTRON" in launcher
    assert "electron.exe" in launcher
    assert "Start-Process -FilePath $Electron" in launcher
    assert "127.0.0.1:12393/bridge/health" in launcher
    assert "inicie-a separadamente" in launcher
    assert "bin\\ligar.sh" not in launcher
    assert "npm install" not in launcher
    assert "uv sync" not in launcher


def test_pet_test_url_and_data_directory_are_explicit_and_loopback_only():
    output = subprocess.check_output(
        [
            "node",
            "-e",
            "const c=require(process.argv[1]);"
            "const normal=c.resolvePetUrl();"
            "const test=c.resolvePetUrl('http://127.0.0.1:19123/');"
            "const dir=c.resolveDataDirectory('C:/celia',{CELIA_PET_TEST_MODE:'1',"
            "CELIA_PET_URL:'http://127.0.0.1:19123/',CELIA_PET_DATA_DIR:'C:/temp/pet'});"
            "let remoteRejected=false,missingRejected=false;"
            "try{c.resolvePetUrl('http://example.com')}catch(_){remoteRejected=true}"
            "try{c.resolveDataDirectory('C:/celia',{CELIA_PET_TEST_MODE:'1'})}catch(_){missingRejected=true}"
            "process.stdout.write(JSON.stringify({normal,test,dir,remoteRejected,missingRejected}));",
            str(_LAUNCH_CONFIG),
        ],
        text=True,
    )
    result = json.loads(output)
    assert "127.0.0.1:12393" in result["normal"]
    assert "127.0.0.1:19123" in result["test"]
    assert "pet=1" in result["test"]
    assert result["dir"].lower() == "c:\\temp\\pet"
    assert result["remoteRejected"] is True
    assert result["missingRejected"] is True

