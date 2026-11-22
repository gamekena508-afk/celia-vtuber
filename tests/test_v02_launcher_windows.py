import importlib.util
import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("launcher_config", ROOT / "launcher_config.py")
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


def test_launcher_files_resolve_from_project_root_and_select_only_local_venv():
    source = (ROOT / "run.ps1").read_text(encoding="utf-8")
    cmd = (ROOT / "run.cmd").read_text(encoding="utf-8")

    assert "$PSScriptRoot" in source
    assert "Set-Location -LiteralPath $ProjectRoot" in source
    assert ".venv\\Scripts\\python.exe" in source
    assert "does not fall back" in source
    assert "%~dp0run.ps1" in cmd
    assert (ROOT / "launcher_config.py").is_file()


def test_bind_values_use_yaml_then_explicit_environment_overrides():
    base = {"system_config": {"host": "127.0.0.1", "port": 12393}}
    assert launcher.effective_bind(base, {}) == ("127.0.0.1", 12393)
    assert launcher.effective_bind(
        base, {"CELIA_BIND_HOST": "0.0.0.0", "CELIA_PORT": "22493"}
    ) == ("0.0.0.0", 22493)
    assert launcher.effective_bind({}, {}) == ("localhost", 12393)
    with pytest.raises(ValueError, match="CELIA_PORT"):
        launcher.effective_bind(base, {"CELIA_PORT": "not-a-port"})
    with pytest.raises(ValueError, match="CELIA_PORT"):
        launcher.effective_bind(base, {"CELIA_PORT": "65536"})
    with pytest.raises(ValueError, match="CELIA_BIND_HOST"):
        launcher.effective_bind(base, {"CELIA_BIND_HOST": "  "})
    with pytest.raises(ValueError, match="CELIA_PORT.*empty"):
        launcher.effective_bind(base, {"CELIA_PORT": ""})


def test_preflight_uses_fixture_paths_and_does_not_require_v02_environment(tmp_path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").touch()
    (tmp_path / "run_server.py").write_text("# fixture only", encoding="utf-8")
    (tmp_path / "conf.yaml").write_text(
        "system_config:\n  host: 127.0.0.1\n  port: 12393\ncharacter_config: {}\n",
        encoding="utf-8",
    )

    report = launcher.preflight(tmp_path, {"CELIA_PORT": "22493"})

    assert report["project_root"] == str(tmp_path.resolve())
    assert report["python"].endswith(".venv\\Scripts\\python.exe")
    assert report["port"] == 22493
    assert report["bind_host"] == "127.0.0.1"
    assert set(report) == {
        "project_root", "python", "entrypoint", "config_path", "bind_host", "port"
    }


def test_preflight_report_never_serializes_other_yaml_fields(tmp_path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").touch()
    (tmp_path / "run_server.py").write_text("# fixture only", encoding="utf-8")
    (tmp_path / "conf.yaml").write_text(
        "system_config:\n  host: localhost\n  port: 12393\n"
        "character_config:\n  api_key: never-print-this\n",
        encoding="utf-8",
    )

    report = launcher.preflight(tmp_path, {})

    assert "never-print-this" not in repr(report)
    assert "api_key" not in report


def test_preflight_rejects_malformed_or_non_mapping_yaml_without_echoing_contents(tmp_path):
    (tmp_path / ".venv" / "Scripts").mkdir(parents=True)
    (tmp_path / ".venv" / "Scripts" / "python.exe").touch()
    (tmp_path / "run_server.py").write_text("# fixture only", encoding="utf-8")
    config = tmp_path / "conf.yaml"

    config.write_text("- list-root\n", encoding="utf-8")
    with pytest.raises(ValueError, match="root must be a mapping"):
        launcher.preflight(tmp_path, {})

    config.write_text("system_config: []\n", encoding="utf-8")
    with pytest.raises(ValueError, match="system_config must be a mapping"):
        launcher.preflight(tmp_path, {})

    config.write_text("private_marker: super-secret-value\nsystem_config: [\n", encoding="utf-8")
    with pytest.raises(ValueError, match="parser details redacted") as exc:
        launcher.preflight(tmp_path, {})
    assert "super-secret-value" not in str(exc.value)


def test_run_server_safe_options_are_parsed_before_application_imports():
    tree = ast.parse((ROOT / "run_server.py").read_text(encoding="utf-8"))
    top_level_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    imported_modules = [
        alias.name if isinstance(node, ast.Import) else (node.module or "")
        for node in top_level_imports
        for alias in (node.names if isinstance(node, ast.Import) else [None])
    ]
    assert not any(name.startswith("src.celia") for name in imported_modules)
    assert "--check" in (ROOT / "run_server.py").read_text(encoding="utf-8")
    assert "--config" in (ROOT / "run_server.py").read_text(encoding="utf-8")


def test_optional_services_are_gated_by_explicit_boot_flags():
    launcher_source = (ROOT / "run_server.py").read_text(encoding="utf-8")
    server_source = (ROOT / "src/celia/server.py").read_text(encoding="utf-8")
    for flag in (
        "CELIA_AUTOSTART_OBS_SUBTITLES",
        "CELIA_AUTOSTART_TRACKER",
        "CELIA_AUTOSTART_LIVE_CHAT",
        "CELIA_AUTOSTART_VOICE_LISTENER",
        "CELIA_AUTOSTART_REACT_WORKER",
    ):
        assert flag in launcher_source or flag in server_source
