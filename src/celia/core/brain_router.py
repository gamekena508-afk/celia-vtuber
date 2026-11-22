"""Low-latency, project-isolated provider router for Célia's cognitive tasks."""
from __future__ import annotations

import asyncio
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

from loguru import logger

from .latency_trace import latency_trace


@dataclass(frozen=True)
class BrainProvider:
    name: str
    base_url: str
    model: str
    reasoning_effort: str | None
    timeout: float
    vision: bool = False
    strict_json: bool = False
    latency_prior_ms: float = 2000.0
    service_tier: str | None = None
    vision_model: str | None = None
    temperature: float | None = 0.1
    min_output_tokens: int = 0

    def model_for(self, image: str | None) -> str:
        return self.vision_model if image is not None and self.vision_model else self.model


@dataclass(frozen=True)
class BrainResult:
    value: dict[str, Any]
    provider: str
    model: str
    latency_ms: int


class BrainRouter:
    """Routes tiny semantic/vision jobs without sharing any outside-project context."""

    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _truthy(name: str, default: str = "1") -> bool:
        return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}

    def providers(self) -> list[BrainProvider]:
        router = os.getenv("CELIA_COGNITIVE_ROUTER_URL", "http://127.0.0.1:20127/v1").strip().rstrip("/")
        luna_model = os.getenv("CELIA_BRAIN_LUNA_MODEL", "openrouter/openai/gpt-5-nano").strip()
        nano5 = luna_model.rsplit("/", 1)[-1] == "gpt-5-nano"
        nano41 = luna_model.rsplit("/", 1)[-1] == "gpt-4.1-nano"
        luna6 = luna_model.rsplit("/", 1)[-1] == "gpt-6-luna"
        return [
            BrainProvider(
                "codex_luna",
                router,
                luna_model,
                "minimal" if nano5 else (None if nano41 else "none"),
                max(0.5, float(os.getenv("CELIA_BRAIN_LUNA_TIMEOUT", "4.5"))),
                vision=True,
                latency_prior_ms=1300.0,
                service_tier=os.getenv("CELIA_BRAIN_LUNA_SERVICE_TIER", "" if nano5 or nano41 else "fast").strip() or None,
                vision_model=os.getenv("CELIA_BRAIN_LUNA_VISION_MODEL", "codex/gpt-5.6-luna").strip() or None,
                temperature=None if nano5 else 0.1,
                min_output_tokens=256 if nano5 or luna6 else 0,
            ),
            BrainProvider(
                "grok45",
                router,
                os.getenv("CELIA_BRAIN_GROK_MODEL", "grok-cli/grok-4.5-low").strip(),
                "low",
                max(0.5, float(os.getenv("CELIA_BRAIN_GROK_TIMEOUT", "4.0"))),
                vision=True,
                latency_prior_ms=1150.0,
            ),
            BrainProvider(
                "gemini_9router",
                router,
                os.getenv("CELIA_COGNITIVE_ROUTER_MODEL", "gemini/gemini-3.1-flash-lite-preview").strip(),
                "minimal",
                max(0.5, float(os.getenv("CELIA_BRAIN_GEMINI_TIMEOUT", "4.5"))),
                vision=True,
                latency_prior_ms=1700.0,
            ),
        ]

    def _state_for(self, name: str) -> dict[str, Any]:
        return self._state.setdefault(name, {
            "failures": 0,
            "disabled_until": 0.0,
            "ema_latency_ms": None,
            "last_latency_ms": None,
            "last_error": "",
            "successes": 0,
        })

    def _enabled(self, provider: BrainProvider) -> bool:
        env = {
            "codex_luna": "CELIA_BRAIN_LUNA_ENABLED",
            "grok45": "CELIA_BRAIN_GROK_ENABLED",
            "gemini_9router": "CELIA_BRAIN_GEMINI_ENABLED",
        }[provider.name]
        return self._truthy(env, "1") and bool(provider.base_url and provider.model)

    def _ordered(self, *, vision: bool) -> list[BrainProvider]:
        specs = [p for p in self.providers() if self._enabled(p) and (p.vision or not vision)]
        requested = [x.strip() for x in os.getenv(
            "CELIA_BRAIN_PROVIDER_ORDER",
            "codex_luna,grok45,gemini_9router",
        ).split(",") if x.strip()]
        rank = {name: idx for idx, name in enumerate(requested)}
        now = time.monotonic()
        specs = [p for p in specs if self._state_for(p.name)["disabled_until"] <= now]
        adaptive = self._truthy("CELIA_BRAIN_ADAPTIVE", "0")
        if not adaptive:
            return sorted(specs, key=lambda p: rank.get(p.name, 999))

        def score(p: BrainProvider) -> tuple[float, int]:
            state = self._state_for(p.name)
            latency = state["ema_latency_ms"]
            return (float(latency if latency is not None else p.latency_prior_ms), rank.get(p.name, 999))

        return sorted(specs, key=score)

    def _mark_failure(self, provider: BrainProvider, exc: Exception | str, *, invalid_payload: bool = False) -> None:
        state = self._state_for(provider.name)
        state["failures"] = min(int(state["failures"]) + 1, 6)
        code = getattr(exc, "code", None)
        if code in {401, 403, 404}:
            cooldown = 300.0
        else:
            cooldown = min(180.0, 15.0 * 2 ** (state["failures"] - 1))
        state["disabled_until"] = time.monotonic() + cooldown
        state["last_error"] = type(exc).__name__ if isinstance(exc, Exception) else str(exc)[:80]
        logger.warning(
            f"[BrainRouter] {provider.name} indisponível ({state['last_error']}); cooldown={cooldown:.0f}s"
        )

    def _mark_success(self, provider: BrainProvider, latency_ms: int) -> None:
        state = self._state_for(provider.name)
        previous = state["ema_latency_ms"]
        state["ema_latency_ms"] = float(latency_ms) if previous is None else previous * 0.7 + latency_ms * 0.3
        state["last_latency_ms"] = latency_ms
        state["failures"] = 0
        state["disabled_until"] = 0.0
        state["last_error"] = ""
        state["successes"] = int(state["successes"]) + 1

    @staticmethod
    def _extract_raw(payload: dict[str, Any]) -> str:
        return str((((payload.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()

    async def _request(
        self,
        provider: BrainProvider,
        *,
        prompt: str,
        image: str | None,
        max_tokens: int,
    ) -> tuple[str, int]:
        content: Any = prompt
        if image is not None:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image}},
            ]
        body: dict[str, Any] = {
            "model": provider.model_for(image),
            "messages": [{"role": "user", "content": content}],
            "max_tokens": max(max_tokens, provider.min_output_tokens) if image is None else max_tokens,
            "stream": False,
        }
        if provider.temperature is not None:
            body["temperature"] = provider.temperature
        if provider.reasoning_effort:
            body["reasoning_effort"] = provider.reasoning_effort
        if provider.service_tier:
            body["service_tier"] = provider.service_tier
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        url = f"{provider.base_url}/chat/completions"

        def call() -> str:
            req = urllib.request.Request(
                url,
                data=encoded,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=provider.timeout) as resp:
                return self._extract_raw(json.loads(resp.read()))

        started = time.monotonic()
        raw = await asyncio.wait_for(asyncio.to_thread(call), timeout=provider.timeout + 0.35)
        return raw, int((time.monotonic() - started) * 1000)

    @staticmethod
    def _has_full_thought_contract(raw: str) -> bool:
        try:
            start, end = raw.find("{"), raw.rfind("}")
            obj = json.loads(raw[start:end + 1])
        except Exception:
            return False
        required = {
            "internal_thought", "live2d_expression", "should_speak",
            "confidence", "urgency", "social_value",
        }
        return required.issubset(obj)

    async def _try_provider(
        self,
        provider: BrainProvider,
        *,
        prompt: str,
        parser: Callable[[str], dict[str, Any] | None],
        image: str | None,
        max_tokens: int,
    ) -> BrainResult | None:
        try:
            raw, latency_ms = await self._request(
                provider, prompt=prompt, image=image, max_tokens=max_tokens
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # network/provider isolation boundary
            self._mark_failure(provider, exc)
            return None
        if provider.strict_json and not self._has_full_thought_contract(raw):
            self._mark_failure(provider, "incomplete_json_contract", invalid_payload=True)
            return None
        value = parser(raw)
        if not value:
            self._mark_failure(provider, "invalid_model_payload", invalid_payload=True)
            return None
        self._mark_success(provider, latency_ms)
        logger.info(
            f"[BrainRouter] {provider.name} {provider.model_for(image)} em {latency_ms}ms"
        )
        return BrainResult(value, provider.name, provider.model_for(image), latency_ms)

    async def _race_providers(
        self,
        providers: list[BrainProvider],
        *,
        prompt: str,
        parser: Callable[[str], dict[str, Any] | None],
        image: str | None,
        max_tokens: int,
    ) -> BrainResult | None:
        """Race all healthy brain providers; the first valid result wins.

        Luna, Grok and Gemini are peer cognitive/orchestrator providers.  Health
        cooldown removes known-bad providers before the race; adaptive latency is
        retained for diagnostics and for sequential mode. Cancellation is best-effort
        because urllib may already be running in a worker thread.
        """
        tasks = [
            asyncio.create_task(
                self._try_provider(
                    provider, prompt=prompt, parser=parser, image=image,
                    max_tokens=max_tokens,
                )
            )
            for provider in providers
        ]
        try:
            for completed in asyncio.as_completed(tasks):
                result = await completed
                if result is None:
                    continue
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                return result
            return None
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _route_inner(
        self,
        prompt: str,
        parser: Callable[[str], dict[str, Any] | None],
        *,
        image: str | None = None,
        max_tokens: int = 128,
        trace_role: str = "brain_router",
    ) -> BrainResult | None:
        ordered = self._ordered(vision=image is not None)
        trace_provider = ordered[0] if ordered else None
        trace_call_id = latency_trace.llm_begin(
            role=trace_role,
            model=trace_provider.model_for(image) if trace_provider else "brain_router:none",
            endpoint=trace_provider.base_url if trace_provider else "",
            measurement="brain_router_request",
        )
        try:
            # Default to one request at a time, with Luna first. Parallel
            # provider racing remains available as a latency-first override.
            # Célia V5 belongs only to the 70B conversation fallback.
            if self._truthy("CELIA_BRAIN_RACE_REMOTE", "0") and len(ordered) >= 2:
                return await self._race_providers(
                    ordered, prompt=prompt, parser=parser, image=image,
                    max_tokens=max_tokens,
                )

            for provider in ordered:
                result = await self._try_provider(
                    provider, prompt=prompt, parser=parser, image=image,
                    max_tokens=max_tokens,
                )
                if result is not None:
                    return result
            return None
        finally:
            latency_trace.llm_end(trace_call_id)

    async def route(
        self,
        prompt: str,
        parser: Callable[[str], dict[str, Any] | None],
        *,
        image: str | None = None,
        max_tokens: int = 128,
        trace_role: str = "brain_router",
    ) -> BrainResult | None:
        """Mesma rota de antes; só acrescenta um pulso de observabilidade por chamada."""
        started = time.monotonic()
        result = None
        try:
            result = await self._route_inner(
                prompt, parser, image=image, max_tokens=max_tokens,
                trace_role=trace_role,
            )
            return result
        finally:
            elapsed_ms = int((time.monotonic() - started) * 1000)
            # Sem provedor nenhum a rota volta em <1 ms; isso não é "chamada" e só
            # encheria a caixa de debug.
            if result is not None or elapsed_ms >= 5:
                try:
                    from .observability import observability

                    observability.emit_ambient(
                        "router.call",
                        "ok" if result is not None else "fail",
                        {
                            "role": trace_role,
                            "provider": result.provider if result is not None else "",
                            "model": result.model if result is not None else "",
                        },
                        duration_ms=elapsed_ms,
                    )
                except Exception:
                    pass

    def debug_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        return {
            "order": [p.name for p in self._ordered(vision=False)],
            "race_remote": self._truthy("CELIA_BRAIN_RACE_REMOTE", "0"),
            "race_policy": "first_valid_peer_wins" if self._truthy("CELIA_BRAIN_RACE_REMOTE", "0") else "sequential_first_valid",
            "providers": {
                p.name: {
                    "model": p.model,
                    "vision_model": p.vision_model or p.model,
                    "vision": p.vision,
                    "reasoning_effort": p.reasoning_effort or "none/local",
                    "service_tier_requested": p.service_tier,
                    "min_output_tokens": p.min_output_tokens,
                    "enabled": self._enabled(p),
                    "cooldown_ms": max(0, int((self._state_for(p.name)["disabled_until"] - now) * 1000)),
                    "failures": self._state_for(p.name)["failures"],
                    "ema_latency_ms": self._state_for(p.name)["ema_latency_ms"],
                    "last_latency_ms": self._state_for(p.name)["last_latency_ms"],
                    "successes": self._state_for(p.name)["successes"],
                    "last_error": self._state_for(p.name)["last_error"],
                }
                for p in self.providers()
            },
        }


brain_router = BrainRouter()
