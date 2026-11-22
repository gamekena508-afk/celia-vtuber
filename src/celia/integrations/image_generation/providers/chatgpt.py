from __future__ import annotations

import json
import time
from pathlib import Path

import httpx

from ..models import ImageRequest, ImageResult, ProviderHealth
from ..utils import DEFAULT_OUTPUT_DIR, file_to_data_url, save_data_url
from .base import ImageProvider


class ChatGPTImageProvider(ImageProvider):
    name = "gpt"
    max_references = 4
    health_url = "http://127.0.0.1:3006/health"
    chat_url = "http://127.0.0.1:3006/v1/chat/completions"

    async def health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(self.health_url)
                response.raise_for_status()
                data = response.json()
            cookies = data.get("cookies") or {}
            browser = data.get("browser") or {}
            if not cookies.get("hasSession"):
                return ProviderHealth(self.name, False, "session_missing", "ChatGPT Proxy sem sessão autenticada", data)
            if browser and not browser.get("contextReady", True):
                return ProviderHealth(self.name, False, "browser_not_ready", "ChatGPT Proxy sem contexto de navegador pronto", data)
            return ProviderHealth(self.name, True, details=data)
        except Exception as exc:
            return ProviderHealth(self.name, False, "proxy_offline", f"ChatGPT Proxy indisponível: {exc}")

    async def generate(self, request: ImageRequest, references: list[Path], prompt_final: str) -> ImageResult:
        started = time.monotonic()
        output_dir = Path(request.output_dir) if request.output_dir else DEFAULT_OUTPUT_DIR / "gpt"
        content: list[dict] = [{"type": "text", "text": prompt_final}]
        for path in references[: self.max_references]:
            content.append({"type": "image_url", "image_url": {"url": file_to_data_url(path)}})
        payload = {
            "model": "chatgpt/image",
            "stream": False,
            "messages": [{"role": "user", "content": content}],
        }
        try:
            timeout = httpx.Timeout(660.0, connect=10.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(self.chat_url, json=payload)
                response.raise_for_status()
                data = response.json()
            raw = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            parsed = json.loads(raw) if isinstance(raw, str) and raw.lstrip().startswith("{") else {}
            images = parsed.get("images") or []
            outputs: list[str] = []
            for image in images:
                data_url = image.get("dataUrl") or image.get("data_url")
                if data_url:
                    outputs.append(str(save_data_url(data_url, output_dir, "gpt")))
            duration = int((time.monotonic() - started) * 1000)
            if not outputs:
                message = parsed.get("text") if parsed else str(raw)
                return ImageResult(False, self.name, prompt_final, [str(p) for p in references], duration_ms=duration,
                                   error_code="generation_refused_or_empty", error=(message or "GPT não retornou imagem")[:800])
            return ImageResult(True, self.name, prompt_final, [str(p) for p in references], outputs,
                               duration_ms=duration, status="completed")
        except httpx.TimeoutException as exc:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="timeout", error=str(exc))
        except Exception as exc:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="provider_error", error=str(exc))
