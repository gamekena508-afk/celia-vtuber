"""Explicit OpenAI-compatible local VLM client for Célia.

No model is chosen or started here. The operator provides the already-running
local endpoint/model through environment variables. GPU serialization belongs
to gpu_scheduler; this client only performs one bounded HTTP request.
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
from .vision_policy import vision_policy, VisionUnavailable


_ENDPOINT_SUFFIX = "/chat/completions"


def chat_completions_url(configured: str) -> str:
    """URL final de /chat/completions a partir do que o operador configurou.

    `CELIA_LOCAL_VISION_URL` tem dois consumidores com convenções diferentes: `mcp/vision_mcp_realtime.py` usa o valor
    como URL COMPLETA (`.../v1/chat/completions`, o que está em secrets/.env) e este cliente o tratava como base e
    acrescentava `/chat/completions` de novo => `.../v1/chat/completions/chat/completions` => HTTP 404 em todo pedido
    (medido 2026-10-18: 404 na URL duplicada, 200 na correta). Aceita base (`.../v1`), URL completa e barra final.
    """
    url = (configured or "").strip().rstrip("/")
    if not url:
        return ""
    if url.endswith(_ENDPOINT_SUFFIX):
        return url
    return url + _ENDPOINT_SUFFIX


@dataclass(frozen=True)
class LocalVisionResult:
    raw: str
    model: str
    latency_ms: int
    url: str = ""
    images: int = 1
    prompt_tokens: int | None = None


class LocalVisionClient:
    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        return vision_policy().url

    @property
    def endpoint(self) -> str:
        """URL absoluta usada de fato (sem base_url do httpx, para nunca emendar caminhos)."""
        return chat_completions_url(self.base_url)

    @property
    def model(self) -> str:
        return vision_policy().model

    @property
    def timeout(self) -> float:
        return vision_policy().timeout

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self.timeout)
        return self._http

    async def _post(self, url: str, body: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client().post(url, json=body, timeout=self.timeout)
        except httpx.TimeoutException as exc:
            raise VisionUnavailable("timeout") from exc
        except httpx.RequestError as exc:
            raise VisionUnavailable("connection_error") from exc

    @staticmethod
    def _extract_content(payload: dict[str, Any]) -> str:
        choices = payload.get("choices") or []
        if not choices:
            return ""
        message = (choices[0] or {}).get("message") or {}
        return str(message.get("content") or "").strip()

    async def request(
        self,
        *,
        prompt: str,
        image: str | list[str],
        max_tokens: int,
        json_schema: dict[str, Any] | None = None,
    ) -> LocalVisionResult:
        """Um pedido ao VLM local. `image` pode ser uma imagem ou uma LISTA (sequência, na ordem dada)."""
        started = time.monotonic()
        vision_policy().require_local()
        images = [image] if isinstance(image, str) else [str(i) for i in image]
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        content += [{"type": "image_url", "image_url": {"url": item}} for item in images]
        url = self.endpoint
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.1,
            "max_tokens": max(1, int(max_tokens)),
            "stream": False,
        }
        if json_schema:
            # llama-server gera JSON valido por gramatica. Medido 2026-10-18: o VLM de 1.6B ignorava o formato pedido no
            # prompt em 3 de 4 quadros ("resposta sem JSON util"); com o esquema, o JSON sempre fecha.
            body["response_format"] = {"type": "json_schema", "json_schema": {"name": "visao", "strict": True, "schema": json_schema}}
        response = await self._post(url, body)
        if response.status_code >= 500:
            # Medido 2026-10-18: depois que o cliente CANCELA um pedido com imagem em voo (timeout), o llama-server responde UMA vez
            # 500 "failed to process mtmd chunk" e volta ao normal no pedido seguinte (12 repetições seguidas ok). Uma nova tentativa
            # curta resolve; mais que uma esconderia um servidor realmente quebrado.
            await asyncio.sleep(float(os.getenv("CELIA_LOCAL_VISION_RETRY_DELAY", "0.4")))
            response = await self._post(url, body)
        if response.status_code != 200:
            # O corpo do erro diz a causa real (rota, contexto estourado, imagem invalida); nunca vira "descricao".
            raise LocalVisionHTTPError(url, response.status_code, response.text[:300])
        try:
            payload = response.json()
            raw = self._extract_content(payload)
        except (ValueError, TypeError, AttributeError, KeyError, IndexError) as exc:
            raise VisionUnavailable("invalid_response") from exc
        if payload.get("model", self.model) != self.model:
            raise VisionUnavailable("unexpected_model")
        if not raw:
            raise VisionUnavailable("empty_response")
        usage = payload.get("usage") if isinstance(payload, dict) else None
        return LocalVisionResult(
            raw=raw,
            model=str(payload.get("model") or self.model),
            latency_ms=int((time.monotonic() - started) * 1000),
            url=url,
            images=len(images),
            prompt_tokens=(usage or {}).get("prompt_tokens") if isinstance(usage, dict) else None,
        )


class LocalVisionHTTPError(VisionUnavailable):
    def __init__(self, url: str, status: int, body: str) -> None:
        super().__init__("http_error", status=status)
        self.args = (f"VLM local HTTP {status} em {url}: {body}",)
        self.url, self.status, self.body = url, status, body


local_vision_client = LocalVisionClient()
