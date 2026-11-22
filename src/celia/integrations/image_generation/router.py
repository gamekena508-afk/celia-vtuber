from __future__ import annotations

import json
import re
import time
from dataclasses import replace
from pathlib import Path

from .identities import IdentityRegistry, provider_from_text, without_image_vocative
from .models import ImageRequest, ImageResult, ProviderAttempt
from .providers import ChatGPTImageProvider, FlowImageProvider, GrokCliImageProvider, MetaImageProvider
from .utils import capture_clipboard_image, looks_like_clipboard_edit, open_image

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROVIDERS_FILE = PROJECT_ROOT / "config" / "image-providers.json"


class ImageRouter:
    def __init__(self):
        self.identities = IdentityRegistry()
        self.provider_config = json.loads(PROVIDERS_FILE.read_text(encoding="utf-8"))
        self.providers = {
            "gpt": ChatGPTImageProvider(),
            "meta": MetaImageProvider(),
            "flow": FlowImageProvider(),
            "grok": GrokCliImageProvider(),
        }

    def infer_provider(self, request: ImageRequest) -> tuple[str, bool]:
        if request.provider != "auto":
            return request.provider, True
        named = provider_from_text(request.prompt)
        return (named, True) if named else ("auto", False)

    def _reference_plan(self, request: ImageRequest) -> tuple[list[Path], list[str], list[str]]:
        refs: list[Path] = []
        labels: list[str] = []
        progress: list[str] = []

        want_clipboard = request.use_clipboard or looks_like_clipboard_edit(request.prompt)
        if want_clipboard:
            clip, err = capture_clipboard_image()
            if clip:
                refs.append(clip)
                labels.append("original image to edit")
                progress.append("reference_ready:clipboard")
            else:
                progress.append(f"clipboard_error:{err}")

        for raw in request.reference_paths:
            path = Path(raw).expanduser().resolve()
            if path.exists() and path.is_file() and path not in refs:
                refs.append(path)
                labels.append(f"referência explícita {path.name}")

        ids, identity_paths = self.identities.resolve_paths(request.prompt, request.identities or None)
        for identity_id, path in zip(ids, identity_paths):
            if path not in refs:
                refs.append(path)
                labels.append(self.identities.identities[identity_id]["label"])
        return refs[:4], labels[:4], progress

    @staticmethod
    def _build_prompt(prompt: str, labels: list[str]) -> str:
        if not labels:
            return prompt.strip()
        mapping = "\n".join(f"Reference {i + 1}: {label}." for i, label in enumerate(labels))
        return (
            prompt.strip()
            + "\n\nReference mapping (preserve these identities/roles exactly):\n"
            + mapping
            + "\nUse the references as visual identity anchors, not merely style inspiration."
        )

    def _auto_order(self, reference_count: int) -> list[str]:
        key = "simple_generate" if reference_count == 0 else ("with_references" if reference_count == 1 else "multi_reference")
        return list(self.provider_config["auto_order"][key])

    async def health_all(self) -> dict[str, dict]:
        result = {}
        for name, provider in self.providers.items():
            result[name] = (await provider.health()).to_dict()
        return result

    async def execute(self, request: ImageRequest) -> ImageResult:
        started = time.monotonic()
        references, labels, progress = self._reference_plan(request)
        prompt = without_image_vocative(request.prompt)
        # Provider routing is an instruction to the adapter, not image content.
        prompt = re.sub(
            r"\b(?:(?:no|pelo)\s+|usando\s+(?:o\s+)?)(?:chat\s*gpt|gpt|meta(?:\s+ai)?|(?:google\s+)?flow|grok(?:\s+build)?)\b",
            "", prompt, flags=re.I,
        )
        if references and (request.use_clipboard or looks_like_clipboard_edit(request.prompt)):
            prompt += "\nEdit reference 1. Preserve its existing objects, colors and composition except for the requested changes."
        prompt_final = self._build_prompt(prompt, labels)
        chosen, explicit_provider = self.infer_provider(request)
        if any(item.startswith("clipboard_error:") for item in progress):
            return ImageResult(
                False, chosen if chosen != "auto" else None, prompt_final,
                status="failed", error_code="clipboard_missing",
                error="Não encontrei uma imagem no clipboard. Copie a imagem e peça novamente.",
                progress=progress,
            )
        order = [chosen] if chosen != "auto" else self._auto_order(len(references))
        attempts: list[ProviderAttempt] = []

        for name in order:
            provider = self.providers.get(name)
            if not provider:
                attempts.append(ProviderAttempt(name, False, "provider_unknown", "Provider desconhecido"))
                continue
            if len(references) > provider.max_references:
                attempts.append(ProviderAttempt(name, False, "capability_insufficient",
                                                f"{name} suporta no máximo {provider.max_references} referências"))
                if explicit_provider:
                    break
                continue

            health = await provider.health()
            if not health.ok:
                attempts.append(ProviderAttempt(name, False, health.code, health.message))
                progress.append(f"provider_failed:{name}:{health.code}")
                if explicit_provider or not request.allow_auto_fallback:
                    break
                progress.append(f"fallback:{name}")
                continue

            progress.append(f"submitted:{name}")
            result = await provider.generate(request, references, prompt_final)
            attempts.append(ProviderAttempt(name, result.ok, result.error_code or "ok", result.error or "", result.duration_ms))
            if result.ok:
                result.attempts = attempts
                result.progress = progress + [f"completed:{name}"]
                result.duration_ms = int((time.monotonic() - started) * 1000)
                if request.open_after and result.primary_output:
                    opened, message = open_image(result.primary_output)
                    result.progress.append("opened" if opened else f"open_failed:{message}")
                return result

            progress.append(f"provider_failed:{name}:{result.error_code or 'provider_error'}")
            if explicit_provider or not request.allow_auto_fallback:
                break
            progress.append(f"fallback:{name}")

        last = attempts[-1] if attempts else None
        return ImageResult(
            False, chosen if chosen != "auto" else None, prompt_final,
            [str(p) for p in references],
            duration_ms=int((time.monotonic() - started) * 1000),
            status="failed",
            error_code=last.code if last else "no_provider",
            error=last.message if last else "Nenhum provider disponível",
            attempts=attempts,
            progress=progress,
        )
