from __future__ import annotations

import asyncio
import shutil
import time
from pathlib import Path

import httpx

from ..models import ImageRequest, ImageResult, ProviderHealth
from ..utils import DEFAULT_OUTPUT_DIR
from .base import ImageProvider


class FlowImageProvider(ImageProvider):
    name = "flow"
    max_references = 1
    base_url = "http://127.0.0.1:3011"

    async def health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=4) as client:
                response = await client.get(f"{self.base_url}/health")
                response.raise_for_status()
                data = response.json()
            if not data.get("extensionReady"):
                return ProviderHealth(self.name, False, "extension_offline", "Flow Proxy não está conectado à ponte local", data)
            if not data.get("flowTabReady", True):
                return ProviderHealth(self.name, False, "flow_tab_missing", "Nenhuma aba saudável do Flow encontrada", data)
            return ProviderHealth(self.name, True, details=data)
        except Exception as exc:
            return ProviderHealth(self.name, False, "proxy_offline", f"Ponte do Flow indisponível: {exc}")

    async def generate(self, request: ImageRequest, references: list[Path], prompt_final: str) -> ImageResult:
        started = time.monotonic()
        output_dir = Path(request.output_dir) if request.output_dir else DEFAULT_OUTPUT_DIR / "flow"
        payload = {
            "prompt": prompt_final,
            "reference_paths": [str(p) for p in references[: self.max_references]],
            "output_dir": str(output_dir),
            "aspect_ratio": request.aspect_ratio,
        }
        try:
            timeout = httpx.Timeout(12.0, connect=4.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(f"{self.base_url}/jobs", json=payload)
                response.raise_for_status()
                job = response.json()
            job_id = job["id"]
            deadline = time.monotonic() + 420
            while time.monotonic() < deadline:
                await asyncio.sleep(2.0)
                async with httpx.AsyncClient(timeout=8) as client:
                    response = await client.get(f"{self.base_url}/jobs/{job_id}")
                    response.raise_for_status()
                    state = response.json()
                if state.get("status") == "completed":
                    raw_outputs = [Path(x) for x in (state.get("output_paths") or [])]
                    output_dir.mkdir(parents=True, exist_ok=True)
                    outputs: list[str] = []
                    for src in raw_outputs:
                        if not src.exists():
                            continue
                        dst = output_dir / src.name
                        try:
                            if src.resolve() != dst.resolve():
                                shutil.copy2(src, dst)
                            outputs.append(str(dst))
                        except Exception:
                            outputs.append(str(src))
                    if not outputs:
                        return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                                           duration_ms=int((time.monotonic() - started) * 1000),
                                           error_code="download_missing", error="Flow concluiu, mas o arquivo baixado não foi encontrado")
                    return ImageResult(True, self.name, prompt_final, [str(p) for p in references], outputs,
                                       duration_ms=int((time.monotonic() - started) * 1000), status="completed")
                if state.get("status") == "failed":
                    return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                                       duration_ms=int((time.monotonic() - started) * 1000),
                                       error_code=state.get("error_code") or "provider_error",
                                       error=state.get("error") or "Flow job falhou")
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000), error_code="timeout",
                               error="Flow excedeu 7 minutos")
        except Exception as exc:
            return ImageResult(False, self.name, prompt_final, [str(p) for p in references],
                               duration_ms=int((time.monotonic() - started) * 1000),
                               error_code="bridge_error", error=str(exc))
