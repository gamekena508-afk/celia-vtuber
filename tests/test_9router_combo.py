from __future__ import annotations

import importlib.util
from pathlib import Path

SCRIPT_PATH = Path(__file__).parents[1] / "tools" / "create_9router_celia_combo.py"


def _load_helper():
    spec = importlib.util.spec_from_file_location("create_9router_celia_combo", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_combo_helper_uses_existing_initial_password(tmp_path, monkeypatch):
    helper = _load_helper()
    env_path = tmp_path / ".env"
    env_path.write_text("OTHER=value\nINITIAL_PASSWORD=dashboard-secret\n", encoding="utf-8")
    monkeypatch.setattr(helper, "ENV_PATH", env_path)

    assert helper._password_from_env_file() == "dashboard-secret"


def test_dedicated_password_override_has_priority(tmp_path, monkeypatch):
    helper = _load_helper()
    env_path = tmp_path / ".env"
    env_path.write_text(
        "INITIAL_PASSWORD=old-secret\nNINE_ROUTER_DASHBOARD_PASSWORD=separate-secret\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(helper, "ENV_PATH", env_path)

    assert helper._password_from_env_file() == "separate-secret"


def test_guardian_gpt_combo_excludes_groq_and_keeps_optional_last():
    helper = _load_helper()
    guardian = next(combo for combo in helper.COMBOS if combo["name"] == "celia-guardian-gpt")

    assert guardian["models"] == ["cx/gpt-5.6-sol", "ollama/gpt-oss:120b"]
    assert all("groq" not in model for model in guardian["models"])


def test_launcher_no_longer_enables_the_gpt_guardian():
    """24/10/2026: revisor GPT do guardião removido por ordem do Criador."""
    launcher = (SCRIPT_PATH.parents[1] / "bin" / "ligar.sh").read_text(encoding="utf-8")

    assert "CELIA_GUARDIAO_LLM" not in launcher
