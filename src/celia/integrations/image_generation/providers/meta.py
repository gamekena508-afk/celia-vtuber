from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from ..models import ImageRequest, ImageResult, ProviderHealth
from ..utils import DEFAULT_OUTPUT_DIR, file_to_data_url, timestamp_name
from .base import ImageProvider


class MetaImageProvider(ImageProvider):
    name = "meta"
    max_references = 4
    health_url = "http://127.0.0.1:3002/health"
    chat_url = "http://127.0.0.1:3002/v1/chat/completions"

    async def health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(self.health_url)
                response.raise_for_status()
                data = response.json()
            cookies = data.get("cookies") or {}
            if not cookies.get("hasSession"):
                return ProviderHealth(self.name, False, "session_missing", "Meta Proxy sem sessão autenticada", data)
            return ProviderHealth(self.name, True, details=data)
        except Exception as exc:
            return ProviderHealth(self.name, False, "proxy_offline", f"Meta Proxy indisponível: {exc}")

    async def _download(self, url: str, output_dir: Path) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = ".webp" if ".webp" in url.lower() else ".jpg"
        path = output_dir / timestamp_name("meta", suffix)
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            response = await client.get(url)
            response.raise_for_status()
            path.write_bytes(response.content)
        return path

    async def generate(self, request: ImageRequest, references: list[Path], prompt_final: str) -> ImageResult:
        started = time.monotonic()
        output_dir = Path(request.output_dir) if request.output_dir else DEFAULT_OUTPUT_DIR / "meta"
        content: list[dict] = [{"type": "text", "text": prompt_final}]
        for path in references[: self.max_references]:
            content.append({"type": "image_url", "image_url": {"url": file_to_data_url(path)}})
        payload = {
            "model": "meta-ai/Imagine",
            "stream": False,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            timeout = httpx.Timeout(390.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.chat_url, json=payload)
                response.raise_for_status()
                data = response.json()
            raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = json.loads(raw) if isinstance(raw, str) and raw.lstrip().startswith("{") else {}
            media = parsed.get("media") or []
            outputs: list[str] = []
            seen: set[str] = set()
            for item in media:
                src = item.get("src") if isinstance(item, dict) else None
                if src and src.startswith("http") and src not in seen:
                    seen.add(src)
                    outputs.append(str(await self._download(src, output_dir)))
            duration = int((time.monotonic() - started) * 1000)
            if not outputs:
                return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                                   duration_ms=duration, error_code="generation_refused_or_empty",
                                   error=(str(raw) or "Meta não retornou imagem")[:800])
            return ImageResult(True, self.name, prompt_final, [str(p) for p in references], outputs,
                               duration_ms=duration, status="completed")
        except httpx.TimeoutException as exc:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="timeout", error=str(exc))
        except Exception as exc:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="provider_error", error=str(exc))
