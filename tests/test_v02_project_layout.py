from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_v02_root_layout_keeps_application_manifests_and_frontend_assets():
    required_files = (
        "run_server.py",
        "run.ps1",
        "run.cmd",
        "launcher_config.py",
        "pyproject.toml",
        "requirements.txt",
        "uv.lock",
        "README.md",
        "frontend/index.html",
        "assets/fonts/Chewy-Regular.ttf",
    )
    required_directories = ("src/celia", "frontend", "bots", "mcp", "tests", "docs/migration")

    assert all((ROOT / path).is_file() for path in required_files)
    assert all((ROOT / path).is_dir() for path in required_directories)


def test_v02_minecraft_node_manifest_and_regression_tests_are_preserved():
    required = (
        "integrations/minecraft/mineflayer/package.json",
        "integrations/minecraft/mineflayer/package-lock.json",
        "integrations/minecraft/mineflayer/src/physical.test.js",
        "integrations/minecraft/mineflayer/src/hud_sensors.test.js",
        "tests/test_portable_paths.py",
        "tests/test_launcher_startup_policy.py",
    )

    assert all((ROOT / path).is_file() for path in required)


def test_v02_launcher_uses_only_its_own_environment_and_ignore_rules_keep_samples():
    launcher = (ROOT / "run.ps1").read_text(encoding="utf-8")
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".venv\\Scripts\\python.exe" in launcher
    assert "does not fall back" in launcher
    assert "/conf.yaml.bak*" in ignore
    assert "**/datasets/generated/" in ignore
    assert "**/logs/" in ignore
    assert "!**/fixtures/**/*.wav" in ignore
    assert "!**/templates/**/*.wav" in ignore
