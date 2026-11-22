from pathlib import Path


ROOT = Path(__file__).parents[1]
LAYER_DIR = ROOT / "tools" / "pet-layer-shell"


def test_layer_shell_spike_uses_native_overlay_and_webengine():
    cmake = (LAYER_DIR / "CMakeLists.txt").read_text()
    source = (LAYER_DIR / "quick_main.cpp").read_text()
    qml = (LAYER_DIR / "main.qml").read_text()

    assert "find_package(LayerShellQt REQUIRED)" in cmake
    assert "Qt6::WebEngineQuick" in cmake
    assert "LayerShellQt::Window::LayerOverlay" in source
    assert "LayerShellQt::Window::LayerTop" in source
    assert "exclusive-zone" in source
    assert "KeyboardInteractivityNone" in source
    assert "QRegion(0, 0, 1, 1)" in source
    assert "probeMode" in qml


def test_layer_shell_spike_does_not_replace_official_electron_launcher():
    launcher = (ROOT / "tools" / "run_pet.sh").read_text()
    assert "/usr/lib/electron39/electron" in launcher
    assert "pet-electron" in launcher
