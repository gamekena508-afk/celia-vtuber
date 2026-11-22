from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
IDENTITIES_FILE = PROJECT_ROOT / "config" / "image-identities.json"


def _plain(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in normalized if not unicodedata.combining(ch)).lower()


def without_image_vocative(text: str) -> str:
    """Keep Celia as a requested subject, but remove direct address."""
    text = re.sub(r"^\s*c[eé]lia\s*[,!:.-]?\s+(?=\w)", "", text, flags=re.I)
    text = re.sub(r",\s*c[eé]lia\s*[.!?]*$", "", text, flags=re.I)
    if (re.search(r"\b(?:essa|esta)\s+imagem\b|clipboard", text, re.I)
            and not re.search(r"\b(?:a|da|com|de|e)\s+c[eé]lia\s*[.!?]*$", text, re.I)):
        text = re.sub(r"\s+c[eé]lia\s*[.!?]*$", "", text, flags=re.I)
    return text.strip()


class IdentityRegistry:
    def __init__(self, config_path: Path = IDENTITIES_FILE):
        self.config_path = config_path
        self.data = json.loads(config_path.read_text(encoding="utf-8"))
        self.identities: dict[str, dict] = self.data["identities"]

    def path_for(self, identity_id: str) -> Path:
        item = self.identities[identity_id]
        return (PROJECT_ROOT / item["main"]).resolve()

    def resolve_ids(self, text: str, explicit: list[str] | None = None) -> list[str]:
        if explicit:
            return self._dedupe([x for x in explicit if x in self.identities])

        p = _plain(without_image_vocative(text))
        found: list[str] = []
        human_markers = ("criador humano", "eu humano", "imagem humana", "foto humana", "versao humana")
        slime_markers = ("criador slime", "eu slime", "forma slime", "versao slime")
        pair_markers = ("eu e tu", "eu e voce", "nos dois", "arte nossa", "imagem nossa", "foto nossa")

        owner_markers = (
            "minha arte", "uma arte minha", "imagem minha", "foto minha",
            "me coloca", "me coloque", "coloca eu", "coloque eu", "comigo",
        )
        owner_human = ("humano" in p or "humana" in p) and any(x in p for x in owner_markers)

        if any(x in p for x in human_markers) or owner_human:
            found.append("criador_human")
        elif any(x in p for x in slime_markers):
            found.append("criador_slime")
        elif any(x in p for x in pair_markers):
            found.extend(["criador_slime", "celia"])
        if not any(x.startswith("criador_") for x in found) and any(x in p for x in owner_markers):
            found.append("criador_slime")

        celia_markers = ("celia", "célia", "sua arte", "sua imagem", "voce", "você", " tu ")
        if any(_plain(x) in f" {p} " for x in celia_markers):
            found.append("celia")

        return self._dedupe(found)

    def resolve_paths(self, text: str, explicit_ids: list[str] | None = None) -> tuple[list[str], list[Path]]:
        ids = self.resolve_ids(text, explicit_ids)
        return ids, [self.path_for(identity_id) for identity_id in ids]

    @staticmethod
    def _dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        for item in items:
            if item not in out:
                out.append(item)
        return out


def provider_from_text(text: str) -> str | None:
    p = _plain(text)
    patterns = [
        ("grok", r"\b(grok(?:\s+build|\s+bild)?)\b"),
        ("flow", r"\b(?:google\s+)?flow\b"),
        ("meta", r"\bmeta(?:\s+ai)?\b"),
        ("gpt", r"\b(?:chat\s*gpt|chatgpt|gpt)\b"),
    ]
    for provider, pattern in patterns:
        if re.search(pattern, p):
            return provider
    return None
