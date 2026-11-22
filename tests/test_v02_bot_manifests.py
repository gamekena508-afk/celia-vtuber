from pathlib import Path

import json
import yaml

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "discord",
    "minecraft",
    "react",
    "tiktok",
    "twitch",
    "youtube",
    "retro",
}


def test_bot_registry_matches_disabled_per_bot_manifests():
    registry = yaml.safe_load((ROOT / "config" / "bots.yaml").read_text(encoding="utf-8"))
    assert registry["default_enabled"] is False
    assert set(registry["bots"]) == EXPECTED

    for bot_id, item in registry["bots"].items():
        assert item["enabled"] is False
        assert item["root"] == f"bots/{bot_id}"
        assert item["health"] == "heartbeat"
        assert item["restart"] == "on-failure"
        assert isinstance(item["startup_timeout_sec"], int)
        assert item["launcher"] is None
        assert item["launchable"] is False
        manifest_path = ROOT / item["manifest"]
        assert manifest_path.is_relative_to(ROOT)
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        assert manifest["bot_id"] == bot_id
        assert manifest["enabled"] is False
        assert manifest["readiness"] == item["readiness"]
        assert manifest["phase"] == item["phase"]


def test_p1_manifests_are_inventory_only_and_do_not_launch():
    registry = yaml.safe_load((ROOT / "config" / "bots.yaml").read_text(encoding="utf-8"))
    for bot_id, item in registry["bots"].items():
        manifest = yaml.safe_load((ROOT / item["manifest"]).read_text(encoding="utf-8"))
        assert manifest["launch_policy"].startswith("disabled;")
        if bot_id in {"discord", "react", "twitch", "youtube"}:
            assert manifest["v02_environment"].startswith(f"bots/{bot_id}/.venv (created")
        else:
            assert manifest["v02_environment"].endswith("(not created)") or "(not created;" in manifest["v02_environment"]
        assert "command" not in manifest
        assert "token" not in manifest
        assert "secret" not in manifest
        for source in manifest["source_entrypoints"]:
            assert not Path(source).is_absolute()
            assert source.replace("\\", "/").startswith(("integrations/", "extensions/", "src/", "bots/"))
        for dependency in manifest["dependency_sources"]:
            assert not Path(dependency).is_absolute()


def test_mcp_wrapper_schema_is_preserved_but_servers_stay_disabled():
    config = json.loads((ROOT / "mcp_servers.json").read_text(encoding="utf-8"))
    assert set(config) == {"mcp_servers"}
    assert config["mcp_servers"]
    for server in config["mcp_servers"].values():
        assert set(("command", "args", "disabled")) <= set(server)
        assert isinstance(server["command"], str)
        assert isinstance(server["args"], list)
        assert server["disabled"] is True
        if server["command"] == "python" and server["args"]:
            assert not Path(server["args"][0]).is_absolute()


def test_model_chain_keeps_approved_fallback_order_and_stays_off():
    chain = yaml.safe_load((ROOT / "config" / "model_chains.yaml").read_text(encoding="utf-8"))["celia"]
    assert chain["enabled"] is False
    assert chain["chain"][0] == "cx/gpt-5.6-luna"
    assert chain["chain"][-1]["model"] == "celia-gemma4-e2b"


def test_bootstrap_config_is_loopback_and_optional_integrations_are_off():
    config = yaml.safe_load((ROOT / "conf.yaml").read_text(encoding="utf-8"))
    system = config["system_config"]
    assert system["host"] == "127.0.0.1"
    assert system["port"] == 12393
    assert system["auto_open_browser"] is False
    agent = config["character_config"]["agent_config"]["agent_settings"]["basic_memory_agent"]
    assert agent["use_mcpp"] is False
    assert agent["tool_agent_enabled"] is False
    assert agent["bridge_enabled"] is False
    assert config["live_config"]["bilibili_live"]["room_ids"] == []
    assert config["live_config"]["bilibili_live"]["sessdata"] == ""
