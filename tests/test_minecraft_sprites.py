"""Local Minecraft item sprites: real captures, pinned inputs, exact 1.21.10 registry coverage, honest licensing notes."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "frontend/minecraft-items/1.21.10"
TOOL = ROOT / "tools/minecraft_sprites"
sys.path.insert(0, str(ROOT / "integrations/minecraft"))


def manifest():
    return json.loads((OUT / "manifest.json").read_text(encoding="utf-8"))


def test_every_manifest_entry_is_a_png_whose_sha256_matches():
    m = manifest()
    assert m["minecraft_version"] == "1.21.10" and m["count"] == len(m["items"])
    for name, meta in m["items"].items():
        data = (OUT / f"{name}.png").read_bytes()
        assert data[:8] == b"\x89PNG\r\n\x1a\n", name
        assert hashlib.sha256(data).hexdigest() == meta["sha256"], name


def test_the_folder_holds_exactly_the_manifest_and_the_real_1_21_10_item_registry():
    m = manifest()
    registry = set(json.loads((TOOL / "registry-1.21.10.json").read_text())["items"])
    files = {p.stem for p in OUT.glob("*.png")}
    assert files == set(m["items"]) == registry, (sorted(files ^ registry)[:10])
    assert m["registry_items_without_sprite"] == []
    assert {"scute", "grass", "sign"}.isdisjoint(files), "renamed/removed items must not ship under their old names"
    assert {"crossbow", "cyan_wool", "turtle_scute", "short_grass"} <= files, "the catalog's `removed` flag is wrong for these; the registry decides"


def test_hud_examples_and_every_mission_stage_icon_have_a_sprite():
    from celia_minecraft.hud_payload import STAGE_ICONS

    items = set(manifest()["items"])
    needed = {"diamond_sword", "iron_pickaxe", "iron_axe", "iron_shovel", "torch", "cobblestone", "cooked_beef", "golden_apple", "water_bucket", "cooked_chicken"}
    assert needed <= items
    assert set(STAGE_ICONS.values()) <= items


def test_source_and_license_decisions_are_recorded_where_the_next_person_will_look():
    m = manifest()
    assert m["source_commit"] == "8888a461f088c6b907f13c14f25a12d90a481ae3" and "v2" in m["decision"] and "1.21.10" in m["decision"]
    assert [layer["version"] for layer in m["layers"]][0] == "1.13.2" and [layer["version"] for layer in m["layers"]][-1] == "1.21.10"
    pins = json.loads((TOOL / "zip-sha256.json").read_text())
    assert {layer["version"]: layer["sha256"] for layer in m["layers"]} == pins
    notice = (ROOT / "frontend/minecraft-items/NOTICE.md").read_text(encoding="utf-8")
    assert "Mojang" in notice and "MIT" in notice and "does not transfer" in notice and "not AI generated" in notice


def test_the_build_tool_reproduces_the_committed_set_offline():
    cache = ROOT / "var/cache/minecraft-sprites"
    if not all((cache / f"{v}.zip").exists() for v in json.loads((TOOL / "zip-sha256.json").read_text())):
        pytest.skip("pinned ZIP cache not present on this machine (var/ is git-ignored)")
    before = hashlib.sha256((OUT / "manifest.json").read_bytes()).hexdigest()
    done = subprocess.run([sys.executable, str(TOOL / "build_sprites.py"), "--offline"], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert hashlib.sha256((OUT / "manifest.json").read_bytes()).hexdigest() == before, "same inputs must give the same set"


def test_a_tampered_input_zip_is_refused(tmp_path, monkeypatch):
    """Negative control for the pin: the tool must abort when a cached ZIP does not match its pinned SHA-256."""
    cache = ROOT / "var/cache/minecraft-sprites"
    if not (cache / "1.13.2.zip").exists():
        pytest.skip("pinned ZIP cache not present on this machine")
    sys.path.insert(0, str(TOOL))
    import importlib

    build = importlib.import_module("build_sprites")
    fake_cache = tmp_path / "cache"
    fake_cache.mkdir()
    for z in cache.glob("*.zip"):
        (fake_cache / z.name).write_bytes(z.read_bytes())
    (fake_cache / "1.13.2.zip").write_bytes(b"PK-tampered")
    monkeypatch.setattr(build, "CACHE", fake_cache)
    monkeypatch.setattr(build, "OUT_ROOT", tmp_path / "out")
    monkeypatch.setattr(sys, "argv", ["build_sprites.py", "--offline"])
    assert build.main() == 3
