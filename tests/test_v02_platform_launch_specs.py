"""Offline checks for narrowly scoped Twitch and YouTube launch specs."""

from pathlib import Path
from types import SimpleNamespace

import yaml

from celia.core.bot_process_manager import BotLaunchSpec, BotProcessManager
from celia.server import _platform_launch_allowlist


REQUIRED = {
    "twitch": {
        "CELIA_TWITCH_ENABLED": "true",
        "TWITCH_CLIENT_ID": "fixture-twitch-client-id",
        "TWITCH_USER_ACCESS_TOKEN": "fixture-twitch-platform-token",
        "TWITCH_BROADCASTER_ID": "fixture-twitch-broadcaster",
        "TWITCH_USER_ID": "fixture-twitch-user",
        "CELIA_BRIDGE_V2_TOKEN_TWITCH_LIVE_CHAT": "fixture-twitch-bridge-token",
        "CELIA_BOT_HEARTBEAT_TOKEN_TWITCH": "fixture-twitch-heartbeat-token-0123456789abcdef",
    },
    "youtube": {
        "CELIA_YOUTUBE_ENABLED": "true",
        "YOUTUBE_API_KEY": "fixture-youtube-api-key",
        "YOUTUBE_LIVE_CHAT_ID": "fixture-youtube-live-chat",
        "CELIA_BRIDGE_V2_TOKEN_YOUTUBE_LIVE_CHAT": "fixture-youtube-bridge-token",
        "CELIA_BOT_HEARTBEAT_TOKEN_YOUTUBE": "fixture-youtube-heartbeat-token-0123456789abcdef",
    },
}


def _fake_project(root: Path) -> Path:
    for bot_id in REQUIRED:
        bot_root = root / "bots" / bot_id
        executable = bot_root / ".venv" / "Scripts" / "python.exe"
        executable.parent.mkdir(parents=True, exist_ok=True)
        executable.write_text("fixture executable; never launched\n", encoding="utf-8")
        (bot_root / "run.py").write_text("# fixture runner; never launched\n", encoding="utf-8")
    return root


def _set_fixture_environment(monkeypatch, *, omit=()):
    names = {name for values in REQUIRED.values() for name in values}
    names.update({"CELIA_BOT_HEARTBEAT_TOKEN_REACT", "CELIA_ADMIN_BOT_TOKEN"})
    for name in names:
        monkeypatch.delenv(name, raising=False)
    for bot_id, values in REQUIRED.items():
        for name, value in values.items():
            if name not in omit:
                monkeypatch.setenv(name, value)
    monkeypatch.setenv("CELIA_BOT_HEARTBEAT_TOKEN_REACT", "fixture-react-heartbeat-token")
    monkeypatch.setenv("CELIA_ADMIN_BOT_TOKEN", "fixture-admin-token")


def _config():
    return SimpleNamespace(system_config=SimpleNamespace(port=12393))


def test_platform_specs_require_explicit_opt_in_files_and_complete_values(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    _set_fixture_environment(monkeypatch)
    monkeypatch.delenv("CELIA_YOUTUBE_ENABLED", raising=False)

    specs = _platform_launch_allowlist(_config(), root)

    assert set(specs) == {"twitch-live-chat-v02"}
    spec = specs["twitch-live-chat-v02"]
    assert spec.bot_id == "twitch"
    assert spec.cwd == (root / "bots/twitch").resolve()
    assert spec.executable == (root / "bots/twitch/.venv/Scripts/python.exe").resolve()
    assert spec.args == (str((root / "bots/twitch/run.py").resolve()), "--connect")
    assert spec.env_file is None


def test_specs_map_only_own_credentials_and_hide_all_values_from_repr(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    _set_fixture_environment(monkeypatch)
    specs = _platform_launch_allowlist(_config(), root)

    twitch = specs["twitch-live-chat-v02"]
    youtube = specs["youtube-live-chat-v02"]
    assert set(twitch.launch_env) == {
        "CELIA_TWITCH_ENABLED", "CELIA_BRIDGE_BASE_URL", "TWITCH_CLIENT_ID",
        "TWITCH_USER_ACCESS_TOKEN", "TWITCH_BROADCASTER_ID", "TWITCH_USER_ID",
        "CELIA_BRIDGE_V2_TOKEN_TWITCH_LIVE_CHAT",
    }
    assert set(youtube.launch_env) == {
        "CELIA_YOUTUBE_ENABLED", "CELIA_BRIDGE_BASE_URL", "YOUTUBE_API_KEY",
        "YOUTUBE_LIVE_CHAT_ID", "CELIA_BRIDGE_V2_TOKEN_YOUTUBE_LIVE_CHAT",
    }
    assert twitch.launch_env["CELIA_BRIDGE_BASE_URL"] == "http://127.0.0.1:12393"
    assert twitch.heartbeat_token == REQUIRED["twitch"]["CELIA_BOT_HEARTBEAT_TOKEN_TWITCH"]
    assert youtube.heartbeat_token == REQUIRED["youtube"]["CELIA_BOT_HEARTBEAT_TOKEN_YOUTUBE"]
    for spec in specs.values():
        rendered = repr(spec)
        assert "fixture-" not in rendered
        assert "launch_env" not in rendered
        assert "heartbeat_token" not in rendered


def test_each_platform_requires_its_own_complete_token_set(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    for missing in (
        "CELIA_BOT_HEARTBEAT_TOKEN_TWITCH",
        "TWITCH_USER_ACCESS_TOKEN",
        "CELIA_BRIDGE_V2_TOKEN_TWITCH_LIVE_CHAT",
        "CELIA_BOT_HEARTBEAT_TOKEN_YOUTUBE",
        "YOUTUBE_API_KEY",
        "CELIA_BRIDGE_V2_TOKEN_YOUTUBE_LIVE_CHAT",
    ):
        _set_fixture_environment(monkeypatch, omit=(missing,))
        specs = _platform_launch_allowlist(_config(), root)
        matching_bot = "twitch" if "TWITCH" in missing else "youtube"
        assert f"{matching_bot}-live-chat-v02" not in specs


def test_missing_local_executable_or_runner_prevents_spec_selection(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    _set_fixture_environment(monkeypatch)
    (root / "bots/twitch/run.py").unlink()
    (root / "bots/youtube/.venv/Scripts/python.exe").unlink()

    assert _platform_launch_allowlist(_config(), root) == {}


def test_manager_forwards_only_the_selected_platform_environment(tmp_path, monkeypatch):
    root = _fake_project(tmp_path)
    _set_fixture_environment(monkeypatch)
    specs = _platform_launch_allowlist(_config(), root)
    monkeypatch.setenv("YOUTUBE_API_KEY", "fixture-other-platform-key")
    monkeypatch.setenv("CELIA_BOT_HEARTBEAT_TOKEN_YOUTUBE", "fixture-other-heartbeat-token")
    monkeypatch.setenv("CELIA_ADMIN_BOT_TOKEN", "fixture-admin-token")

    class EmptyRegistry:
        def list_entries(self):
            return []

    manager = BotProcessManager(EmptyRegistry(), log_dir=tmp_path / "logs")
    env = manager._filtered_environment(specs["twitch-live-chat-v02"])

    assert set(env).intersection(specs["twitch-live-chat-v02"].launch_env) == set(
        specs["twitch-live-chat-v02"].launch_env
    )
    assert "YOUTUBE_API_KEY" not in env
    assert "CELIA_BOT_HEARTBEAT_TOKEN_YOUTUBE" not in env
    assert "CELIA_ADMIN_BOT_TOKEN" not in env
    assert "CELIA_BOT_HEARTBEAT_TOKEN" not in env  # Manager injects only the spec's own value at spawn.


def test_platform_registry_entries_stay_disabled_and_react_allowlist_is_separate():
    root = Path(__file__).resolve().parents[1]
    data = yaml.safe_load((root / "config/bots.yaml").read_text(encoding="utf-8"))
    for bot_id in ("twitch", "youtube"):
        entry = data["bots"][bot_id]
        assert entry["enabled"] is False
        assert entry["launcher"] is None
        assert entry["launchable"] is False
    assert data["bots"]["react"]["launcher"] is None
    assert data["bots"]["react"]["launchable"] is False


def test_launch_env_field_is_hidden_and_rejects_invalid_or_cross_bot_names(tmp_path):
    spec = BotLaunchSpec(
        bot_id="twitch", cwd=tmp_path.resolve(), executable=tmp_path / "python.exe",
        args=("runner.py",), launch_env={"TWITCH_USER_ACCESS_TOKEN": "fixture-secret"},
    )
    assert "fixture-secret" not in repr(spec)
    assert "TWITCH_USER_ACCESS_TOKEN" not in repr(spec)
    try:
        BotLaunchSpec(
            bot_id="twitch", cwd=tmp_path.resolve(), executable=tmp_path / "python.exe",
            args=("runner.py",), launch_env={"BAD NAME": "fixture"},
        )
    except ValueError as exc:
        assert "launch environment" in str(exc)
    else:
        raise AssertionError("invalid launch environment name was accepted")
    for forbidden_name in ("YOUTUBE_API_KEY", "CELIA_ADMIN_BOT_TOKEN"):
        try:
            BotLaunchSpec(
                bot_id="twitch", cwd=tmp_path.resolve(), executable=tmp_path / "python.exe",
                args=("runner.py",), launch_env={forbidden_name: "fixture-secret"},
            )
        except ValueError as exc:
            assert "fixed credential allowlist" in str(exc)
        else:
            raise AssertionError(f"{forbidden_name} must not be launchable for Twitch")
