"""Checks for the portable Célia prompt/memory engineering guide.

Documentation only: never boots the model, Discord or MCP servers.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "GUIA-ENGENHARIA-PROMPTS-CELIA.md"
READMES = (
    ROOT / "README.md",
    ROOT / "docs/diagnosticos/benchmark-personalidade-celia-v2/README.md",
    ROOT / "docs/diagnosticos/benchmark-gemma-2b-celia/README.md",
    ROOT / "AGENTS.md",
)


def _markdown_links(source: str) -> list[str]:
    return re.findall(r"\[[^\]\n]+\]\(([^)\n]+)\)", source)


def test_central_guide_has_only_existing_relative_links() -> None:
    text = GUIDE.read_text(encoding="utf-8")
    links = _markdown_links(text)
    assert len(links) >= 25, "The guide should be navigable, not a dead summary."
    for link in links:
        target = link.split("#", 1)[0]
        assert not re.match(r"^[a-z]+://", target, flags=re.I), link
        assert not re.match(r"^[A-Za-z]:[\\/]", target), link
        assert not target.startswith(("/", "\\")), link
        assert (GUIDE.parent / target).exists(), f"Broken relative link: {link}"


def test_readmes_and_agent_instructions_reference_same_guide() -> None:
    for path in READMES:
        text = path.read_text(encoding="utf-8")
        links = _markdown_links(text)
        expected = GUIDE.relative_to(path.parent) if GUIDE.is_relative_to(path.parent) else None
        assert any((path.parent / link).resolve() == GUIDE for link in links), path
        assert expected is None or expected.is_relative_to(Path("."))


def test_guide_is_historical_but_points_to_live_v01_and_l0() -> None:
    guide = GUIDE.read_text(encoding="utf-8")
    assert "V01_PRIORIDADE" in guide
    assert "L0_LONGA" in guide
    assert "6.581" in guide
    assert "sem juiz" in guide.lower()
    assert "2/3" in guide
    assert not re.search(r"[A-Za-z]:\\", guide)
    assert not re.search(r"(?m)^[/\\]home[/\\]", guide)
    config = yaml.safe_load((ROOT / "conf.yaml").read_text(encoding="utf-8"))
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == "persona_prompt" and isinstance(value, str):
                    found.append(value)
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(config)
    assert len(found) == 1 and len(found[0]) == 1283
    snapshot = guide.split("**Instantâneo literal da V01", 1)[1]
    snapshot = snapshot.split("```text\n", 1)[1].split("\n```", 1)[0]
    assert snapshot == found[0], "Documented persona snapshot diverged from conf.yaml"
    assert (ROOT / "src/celia/core/speaker_identity.py").exists()
