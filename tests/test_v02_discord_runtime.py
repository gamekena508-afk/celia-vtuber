import json
import asyncio
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "bots" / "discord" / "run.py"


def test_offline_preflight_reports_structure_without_connecting_or_reading_credentials():
    result = subprocess.run(
        [sys.executable, "-B", str(RUNNER), "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "CELIA_DISCORD_ENABLED": "false"},
        check=False,
    )
    assert result.returncode == 0
    report = json.loads(result.stdout)
    assert report["legacy_entrypoint_exists"] is True
    assert report["requirements_exists"] is True
    assert report["enabled"] is False
    assert report["token_values_read"] is False
    assert report["services_started"] is False
    assert isinstance(report["missing_imports"], list)


def test_runtime_refuses_to_start_without_explicit_enable_flag():
    environment = dict(os.environ)
    environment.pop("CELIA_DISCORD_ENABLED", None)
    result = subprocess.run(
        [sys.executable, "-B", str(RUNNER)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
        check=False,
    )
    assert result.returncode == 3
    assert "disabled" in result.stderr.lower()
    assert "token" not in result.stdout.lower()


def test_discord_runtime_has_local_dependency_manifest_but_no_launch_enablement():
    import yaml

    registry = yaml.safe_load((ROOT / "config" / "bots.yaml").read_text(encoding="utf-8"))
    discord = registry["bots"]["discord"]
    manifest = yaml.safe_load((ROOT / "bots" / "discord" / "manifest.yaml").read_text(encoding="utf-8"))
    assert discord["enabled"] is False
    assert discord["launcher"] is None
    assert discord["launchable"] is False
    assert manifest["v02_entrypoint"] == "bots/discord/run.py"
    assert Path(ROOT / "bots" / "discord" / "requirements.txt").is_file()
    assert Path(ROOT / "bots" / "discord" / "uv.lock").is_file()
    assert manifest["readiness"] == discord["readiness"]


def test_legacy_outbound_adapter_resolves_to_v02_runner_and_respects_disabled_state(monkeypatch):
    from celia.integrations.discord import outbound

    assert outbound.BOT_SCRIPT == ROOT / "bots" / "discord" / "run.py"
    assert outbound.BOT_PYTHON.is_relative_to(ROOT / "bots" / "discord")
    monkeypatch.delenv("CELIA_DISCORD_ENABLED", raising=False)
    monkeypatch.setattr(outbound, "_settings", lambda: ("fixture-only-token", 0))
    monkeypatch.setattr(
        outbound.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not spawn")),
    )
    result = asyncio.run(outbound.start_discord_bot())
    assert result["code"] == "bot_disabled"
