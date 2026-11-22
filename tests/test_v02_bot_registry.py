from pathlib import Path

import pytest
import yaml

from celia.bridge.registry import BotRegistry, RegistryError
from v02_bot_test_support import manager_for


def test_current_manifests_are_inventory_only_and_no_launcher_is_resolved():
    root = Path(__file__).resolve().parents[1]
    registry = BotRegistry.load(root)

    assert registry.autostart_ids() == ()
    assert len(registry.list_entries()) == 7
    for entry in registry.list_entries():
        assert not entry.enabled
        assert not entry.launchable
        assert entry.launcher is None
        assert registry.launch_spec(entry.bot_id) is None


def test_launchable_manifest_requires_a_code_owned_allowlist(tmp_path):
    manager = manager_for(tmp_path, "pass")
    root = manager.registry.project_root

    with pytest.raises(RegistryError, match="code allowlist"):
        BotRegistry.load(root)


def test_registry_rejects_paths_that_escape_project_root(tmp_path):
    manager = manager_for(tmp_path, "pass")
    root = manager.registry.project_root
    config_path = root / "config" / "bots.yaml"
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    data["bots"]["probe"]["root"] = "../outside"
    config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

    with pytest.raises(RegistryError, match="safe relative path"):
        BotRegistry.load(root, launch_allowlist={"probe-python": manager.registry.launch_spec("probe")})
