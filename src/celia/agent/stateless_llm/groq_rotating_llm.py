"""OpenAI-compatible Groq client with ordered API-key failover."""

import asyncio
import hashlib
import json
import time
from typing import Any, AsyncIterator, Dict, List

from loguru import logger
from openai import AsyncOpenAI, AuthenticationError, RateLimitError, Timeout

from .openai_compatible_llm import AsyncLLM
from ...core.observability import observability


class GroqRotatingLLM(AsyncLLM):
    """Try Groq keys in order and temporarily cool down exhausted keys."""

    def __init__(
        self,
        model: str,
        api_keys: str,
        base_url: str = "https://api.groq.com/openai/v1",
        temperature: float = 0.2,
        max_tokens: int = 512,
        rate_limit_cooldown_sec: int = 60,
        auth_cooldown_sec: int = 900,
        fallback_model: str = "",
        request_budget_sec: float = 8.0,
        hedge_delay_sec: float = 4.0,
        breaker_failures: int = 2,
        breaker_cooldown_sec: float = 30.0,
    ):
        keys = [key.strip() for key in (api_keys or "").split(",") if key.strip()]
        if not keys:
            raise ValueError("Tool agent enabled but no Groq API keys were configured.")

        super().__init__(
            model=model,
            base_url=base_url,
            llm_api_key=keys[0],
            temperature=temperature,
            max_tokens=max_tokens,
            fallback_model=fallback_model,
        )
        self._request_budget_sec = max(0.1, float(request_budget_sec))
        self._hedge_delay_sec = max(
            0.0, min(float(hedge_delay_sec), self._request_budget_sec)
        )
        self._breaker_failures = max(1, int(breaker_failures))
        self._breaker_cooldown_sec = max(1.0, float(breaker_cooldown_sec))
        self._primary_failure_streak = 0
        self._primary_breaker_until = 0.0
        self._keys = keys
        self._clients = [
            AsyncOpenAI(
                base_url=base_url,
                api_key=key,
                timeout=Timeout(
                    timeout=self._request_budget_sec,
                    connect=min(3.0, self._request_budget_sec),
                ),
                max_retries=0,
            )
            for key in keys
        ]
        self._cooldown_until = [0.0 for _ in keys]
        self._rate_limit_cooldown_sec = max(1, int(rate_limit_cooldown_sec))
        self._auth_cooldown_sec = max(1, int(auth_cooldown_sec))
        self._cooldown_reason = ["" for _ in keys]
        # A model on a different gateway namespace has an independent quota.
        # Keep the same-provider guard: another model on the exhausted route
        # cannot cure a provider-wide 429.
        self._quota_fallback = (
            AsyncLLM(
                model=fallback_model,
                base_url=base_url,
                llm_api_key=keys[0],
                temperature=temperature,
                max_tokens=max_tokens,
                request_timeout_sec=self._request_budget_sec,
            )
            if fallback_model and model.split("/", 1)[0] != fallback_model.split("/", 1)[0]
            else None
        )
        self.reraise_retryable_errors = True
        self.allow_gateway_fallback = False
        logger.info(
            f"Initialized Groq tool agent with {len(keys)} API keys and model {model}"
            + (f", fallback_model={fallback_model}" if fallback_model else "")
        )

    def _available_indexes(self) -> List[int]:
        now = time.monotonic()
        return [
            index
            for index, cooldown_until in enumerate(self._cooldown_until)
            if cooldown_until <= now
        ]

    def _payload_observability(
        self,
        messages: List[Dict[str, Any]],
        system: str | None,
        tools: Any,
    ) -> Dict[str, Any]:
        safe_messages = []
        total_chars = 0
        for message in messages or []:
            role = str(message.get("role") or "unknown")
            content = message.get("content")
            content_text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)
            total_chars += len(content_text)
            safe_messages.append({"role": role, "chars": len(content_text)})
        tool_names: List[str] = []
        tool_schema_chars = 0
        for tool in tools or []:
            try:
                function = tool.get("function") or {}
                tool_names.append(str(function.get("name") or tool.get("name") or "unknown"))
                tool_schema_chars += len(json.dumps(tool, ensure_ascii=False, sort_keys=True, default=str))
            except Exception:
                tool_names.append("unknown")
        shape = {
            "model": self.model,
            "messages": safe_messages,
            "system_chars": len(system or ""),
            "tool_names": tool_names,
            "tool_schema_chars": tool_schema_chars,
        }
        signature = hashlib.sha256(
            json.dumps(shape, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]
        return {
            "model": self.model,
            "message_count": len(messages or []),
            "message_chars": total_chars,
            "system_chars": len(system or ""),
            "tool_count": len(tool_names),
            "tool_names": ",".join(tool_names[:12]),
            "tool_schema_chars": tool_schema_chars,
            "payload_shape_sha256_16": signature,
        }

    @staticmethod
    async def _collect_events(stream: AsyncIterator[Any]) -> List[Any]:
        events: List[Any] = []
        async for event in stream:
            events.append(event)
        return events

    @staticmethod
    def _events_are_usable(events: List[Any]) -> bool:
        for event in events:
            if isinstance(event, list) and event:
                return True
            if isinstance(event, str):
                text = event.strip()
                if not text:
                    continue
                if text.startswith("Error calling the chat endpoint:"):
                    continue
                if text == "__API_NOT_SUPPORT_TOOLS__":
                    continue
                return True
        return False

    def _primary_breaker_open(self) -> bool:
        return time.monotonic() < self._primary_breaker_until

    def _mark_primary_success(self) -> None:
        self._primary_failure_streak = 0
        self._primary_breaker_until = 0.0

    def _mark_primary_failure(self, reason: str) -> None:
        self._primary_failure_streak += 1
        if self._primary_failure_streak >= self._breaker_failures:
            self._primary_breaker_until = time.monotonic() + self._breaker_cooldown_sec
            logger.warning(
                "Tool-router primary circuit opened for "
                f"{self._breaker_cooldown_sec:.0f}s after "
                f"{self._primary_failure_streak} failures (last={reason})."
            )

    def _mark_key_error(self, index: int, exc: BaseException) -> None:
        if isinstance(exc, RateLimitError):
            self._cooldown_until[index] = time.monotonic() + self._rate_limit_cooldown_sec
            self._cooldown_reason[index] = "quota"
        elif isinstance(exc, AuthenticationError):
            self._cooldown_until[index] = time.monotonic() + self._auth_cooldown_sec
            self._cooldown_reason[index] = "auth"

    async def _hedged_provider_completion(
        self,
        index: int,
        messages: List[Dict[str, Any]],
        system: str | None,
        tools: Any,
    ) -> tuple[List[Any], str, int]:
        """Race only provider/model selection. Tool execution happens downstream once."""
        loop = asyncio.get_running_loop()
        started = loop.time()
        deadline = started + self._request_budget_sec
        primary_failure_recorded = False

        async def run_primary() -> List[Any]:
            self.client = self._clients[index]
            return await self._collect_events(
                super(GroqRotatingLLM, self).chat_completion(messages, system, tools)
            )

        async def run_fallback() -> List[Any]:
            if self._quota_fallback is None:
                return []
            return await self._collect_events(
                self._quota_fallback.chat_completion(messages, system, tools)
            )

        if self._primary_breaker_open():
            logger.warning("Tool-router primary circuit is open; bypassing primary provider.")
            try:
                events = await asyncio.wait_for(
                    run_fallback(), timeout=self._request_budget_sec
                )
            except Exception as exc:
                logger.warning(f"Tool-router fallback failed while breaker open: {type(exc).__name__}")
                events = []
            elapsed_ms = int((loop.time() - started) * 1000)
            return (
                events if self._events_are_usable(events) else ["__NO_TOOL__"],
                "fallback_breaker",
                elapsed_ms,
            )

        primary_task = asyncio.create_task(run_primary())
        fallback_task = None

        # Give the preferred provider a short head start. Do not cancel it when
        # the hedge delay expires; the independent provider starts alongside it.
        head_start = min(self._hedge_delay_sec, max(0.0, deadline - loop.time()))
        if head_start > 0:
            done, _ = await asyncio.wait({primary_task}, timeout=head_start)
        else:
            done = set()

        if primary_task in done:
            try:
                events = primary_task.result()
            except Exception as exc:
                self._mark_key_error(index, exc)
                self._mark_primary_failure(type(exc).__name__)
                primary_failure_recorded = True
                events = []
            if self._events_are_usable(events):
                self._mark_primary_success()
                return events, "primary", int((loop.time() - started) * 1000)
            if not primary_failure_recorded:
                self._mark_primary_failure("invalid_or_error_payload")
                primary_failure_recorded = True
        else:
            logger.info(
                f"Tool-router hedge fired after {self._hedge_delay_sec:.2f}s; "
                "racing independent fallback provider."
            )

        fallback_task = asyncio.create_task(run_fallback())
        tasks = {task for task in (primary_task, fallback_task) if not task.done()}
        # A primary that already failed stays out of the race; fallback still gets
        # the remaining global budget.
        if not primary_task.done():
            tasks.add(primary_task)
        if not fallback_task.done():
            tasks.add(fallback_task)

        # Handle an immediately completed fallback before entering asyncio.wait.
        immediate = [task for task in (primary_task, fallback_task) if task.done()]
        for task in immediate:
            source = "primary" if task is primary_task else "fallback"
            try:
                events = task.result()
            except Exception as exc:
                if source == "primary":
                    self._mark_key_error(index, exc)
                    if not primary_failure_recorded:
                        self._mark_primary_failure(type(exc).__name__)
                        primary_failure_recorded = True
                events = []
            if self._events_are_usable(events):
                if source == "primary":
                    self._mark_primary_success()
                elif not primary_task.done() and not primary_failure_recorded:
                    self._mark_primary_failure("hedge_lost")
                    primary_failure_recorded = True
                for pending in tasks:
                    pending.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                return events, source, int((loop.time() - started) * 1000)

        while tasks and loop.time() < deadline:
            remaining = max(0.0, deadline - loop.time())
            done_now, pending = await asyncio.wait(
                tasks,
                timeout=remaining,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done_now:
                break
            tasks = set(pending)
            for task in done_now:
                source = "primary" if task is primary_task else "fallback"
                try:
                    events = task.result()
                except Exception as exc:
                    if source == "primary":
                        self._mark_key_error(index, exc)
                        if not primary_failure_recorded:
                            self._mark_primary_failure(type(exc).__name__)
                            primary_failure_recorded = True
                    events = []
                if not self._events_are_usable(events):
                    if source == "primary" and not primary_failure_recorded:
                        self._mark_primary_failure("invalid_or_error_payload")
                        primary_failure_recorded = True
                    continue

                if source == "primary":
                    self._mark_primary_success()
                elif not primary_task.done() and not primary_failure_recorded:
                    self._mark_primary_failure("hedge_lost")
                    primary_failure_recorded = True
                for pending_task in tasks:
                    pending_task.cancel()
                if tasks:
                    await asyncio.gather(*tasks, return_exceptions=True)
                return events, source, int((loop.time() - started) * 1000)

        for task in (primary_task, fallback_task):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (primary_task, fallback_task) if task is not None),
            return_exceptions=True,
        )
        if not primary_failure_recorded:
            self._mark_primary_failure("budget_exhausted")
        return ["__NO_TOOL__"], "budget_exhausted", int((loop.time() - started) * 1000)

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools=None,
    ) -> AsyncIterator[Any]:
        available_indexes = self._available_indexes()
        if not available_indexes:
            if self._quota_fallback is not None and all(reason == "quota" for reason in self._cooldown_reason):
                async for event in self._quota_fallback.chat_completion(messages, system, tools):
                    yield event
                return
            # Same-provider models and authentication failures do not warrant a quota fallback.
            wait_seconds = max(1, int(min(self._cooldown_until) - time.monotonic()))
            logger.warning(
                f"All Groq tool-agent keys are cooling down. Retry in about {wait_seconds}s."
            )
            yield "As ferramentas estao temporariamente ocupadas. Tente de novo em instantes."
            return

        payload_meta = self._payload_observability(messages, system, tools)

        # Independent-provider fallback can be safely hedged here because this
        # layer only chooses a tool. No tool is executed until the winner is
        # yielded to BasicMemoryAgent downstream.
        if self._quota_fallback is not None:
            index = available_indexes[0]
            request_meta = {
                **payload_meta,
                "key_slot": index + 1,
                "key_slots_total": len(self._keys),
            }
            observability.emit(
                "tool_llm_request",
                "model.tool_router",
                "running",
                request_meta,
            )
            logger.info(
                "Tool agent hedged attempt payload="
                f"messages:{payload_meta['message_count']}/"
                f"{payload_meta['message_chars']}chars "
                f"system:{payload_meta['system_chars']}chars "
                f"tools:{payload_meta['tool_count']}/"
                f"{payload_meta['tool_schema_chars']}chars "
                f"shape:{payload_meta['payload_shape_sha256_16']} "
                f"budget:{self._request_budget_sec:.2f}s "
                f"hedge:{self._hedge_delay_sec:.2f}s"
            )
            events, winner, elapsed_ms = await self._hedged_provider_completion(
                index, messages, system, tools
            )
            first_event = events[0] if events else ""
            observability.emit(
                "tool_llm_first_event",
                "model.tool_router",
                "ok" if winner != "budget_exhausted" else "fallback",
                {
                    **request_meta,
                    "first_event_ms": elapsed_ms,
                    "event_type": type(first_event).__name__,
                    "source": winner,
                },
                duration_ms=elapsed_ms,
            )
            for event in events:
                yield event
            observability.emit(
                "tool_llm_end",
                "model.tool_router",
                "ok" if winner != "budget_exhausted" else "fallback",
                {
                    **request_meta,
                    "first_event_ms": elapsed_ms,
                    "source": winner,
                },
                duration_ms=elapsed_ms,
            )
            return

        for index in available_indexes:
            self.client = self._clients[index]
            emitted = False
            attempt_started = time.perf_counter()
            first_event_ms = None
            request_meta = {
                **payload_meta,
                "key_slot": index + 1,
                "key_slots_total": len(self._keys),
            }
            observability.emit(
                "tool_llm_request",
                "model.tool_router",
                "running",
                request_meta,
            )
            try:
                logger.info(
                    "Tool agent attempt with key slot "
                    f"{index + 1}/{len(self._keys)} payload="
                    f"messages:{payload_meta['message_count']}/"
                    f"{payload_meta['message_chars']}chars "
                    f"system:{payload_meta['system_chars']}chars "
                    f"tools:{payload_meta['tool_count']}/"
                    f"{payload_meta['tool_schema_chars']}chars "
                    f"shape:{payload_meta['payload_shape_sha256_16']}"
                )
                async for event in super().chat_completion(messages, system, tools):
                    if first_event_ms is None:
                        first_event_ms = int((time.perf_counter() - attempt_started) * 1000)
                        observability.emit(
                            "tool_llm_first_event",
                            "model.tool_router",
                            "ok",
                            {
                                **request_meta,
                                "first_event_ms": first_event_ms,
                                "event_type": type(event).__name__,
                            },
                            duration_ms=first_event_ms,
                        )
                    emitted = True
                    yield event
                observability.emit(
                    "tool_llm_end",
                    "model.tool_router",
                    "ok",
                    {**request_meta, "first_event_ms": first_event_ms},
                    duration_ms=int((time.perf_counter() - attempt_started) * 1000),
                )
                return
            except RateLimitError:
                observability.emit(
                    "tool_llm_end",
                    "model.tool_router",
                    "error",
                    {**request_meta, "error_type": "RateLimitError", "first_event_ms": first_event_ms},
                    duration_ms=int((time.perf_counter() - attempt_started) * 1000),
                )
                # Never replace a stream after output: it could repeat a tool call.
                if emitted:
                    raise
                self._cooldown_until[index] = (
                    time.monotonic() + self._rate_limit_cooldown_sec
                )
                self._cooldown_reason[index] = "quota"
                logger.warning(
                    f"Groq tool-agent key slot {index + 1} reached its limit; trying the next key."
                )
            except AuthenticationError:
                observability.emit(
                    "tool_llm_end",
                    "model.tool_router",
                    "error",
                    {**request_meta, "error_type": "AuthenticationError", "first_event_ms": first_event_ms},
                    duration_ms=int((time.perf_counter() - attempt_started) * 1000),
                )
                if emitted:
                    raise
                self._cooldown_until[index] = (
                    time.monotonic() + self._auth_cooldown_sec
                )
                self._cooldown_reason[index] = "auth"
                logger.warning(
                    f"Groq tool-agent key slot {index + 1} failed authentication; trying the next key."
                )
            except Exception as exc:
                observability.emit(
                    "tool_llm_end",
                    "model.tool_router",
                    "error",
                    {
                        **request_meta,
                        "error_type": type(exc).__name__,
                        "first_event_ms": first_event_ms,
                    },
                    duration_ms=int((time.perf_counter() - attempt_started) * 1000),
                )
                raise

        if self._quota_fallback is not None and all(reason == "quota" for reason in self._cooldown_reason):
            logger.warning(f"Tool agent quota exhausted; trying independent route {self.fallback_model}")
            async for event in self._quota_fallback.chat_completion(messages, system, tools):
                yield event
            return
        if self.fallback_model:
            logger.warning(f"Skipping tool fallback {self.fallback_model}: same provider or non-quota failure")

        logger.warning("No Groq tool-agent key completed the request.")
        yield "As ferramentas estao temporariamente indisponiveis. Tente novamente em instantes."
