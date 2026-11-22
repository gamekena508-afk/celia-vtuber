"""Description: This file contains the implementation of the `AsyncLLM` class.
This class is responsible for handling asynchronous interaction with OpenAI API compatible
endpoints for language generation.
"""

import asyncio
import json
import os
import time
import threading
from typing import AsyncIterator, List, Dict, Any
from openai import (
    AsyncStream,
    AsyncOpenAI,
    APIError,
    APIConnectionError,
    AuthenticationError,
    APITimeoutError,
    APIStatusError,
    RateLimitError,
    NotGiven,
    NOT_GIVEN,
    Timeout,
)
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall
from loguru import logger

from .stateless_llm_interface import StatelessLLMInterface
from ...mcpp.types import ToolCallObject
from ...core.llm_telemetry import llm_telemetry
from ...core.training_trace import training_trace
from ...core.latency_trace import latency_trace


class ContextBudgetExceeded(ValueError):
    def __init__(self, *, estimated_tokens: int, input_budget: int, context_tokens: int, output_tokens: int):
        super().__init__("required system/current-turn context exceeds the configured input budget")
        self.estimated_tokens = estimated_tokens
        self.input_budget = input_budget
        self.context_tokens = context_tokens
        self.output_tokens = output_tokens


class AsyncLLM(StatelessLLMInterface):
    _provider_breaker_lock = threading.Lock()
    _provider_breaker_until: dict[str, float] = {}

    def __init__(
        self,
        model: str,
        base_url: str,
        llm_api_key: str = "z",
        organization_id: str = "z",
        project_id: str = "z",
        temperature: float = 1.0,
        max_tokens: int = 256,
        fallback_model: str = "",
        local_fallback_base_url: str = "",
        local_fallback_model: str = "",
        local_fallback_api_key: str = "local",
        local_fallback_context_tokens: int = 2048,
        local_fallback_output_tokens: int = 256,
        context_tokens: int | None = None,
        provider_rate_limit_cooldown_sec: float = 60.0,
        request_timeout_sec: float = 120.0,
    ):
        """
        Initializes an instance of the `AsyncLLM` class.

        Parameters:
        - model (str): The model to be used for language generation.
        - base_url (str): The base URL for the OpenAI API.
        - organization_id (str, optional): The organization ID for the OpenAI API. Defaults to "z".
        - project_id (str, optional): The project ID for the OpenAI API. Defaults to "z".
        - llm_api_key (str, optional): The API key for the OpenAI API. Defaults to "z".
        - temperature (float, optional): What sampling temperature to use, between 0 and 2. Defaults to 1.0.
        - max_tokens (int, optional): Maximum tokens in response. Defaults to 256 for faster responses.
        - fallback_model (str, optional): Model to use when primary hits rate limit. Defaults to "".
        """
        self.base_url = base_url
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.fallback_model = fallback_model or ""
        self.local_fallback_base_url = (local_fallback_base_url or "").rstrip("/")
        self.local_fallback_model = local_fallback_model or ""
        self.local_fallback_context_tokens = max(512, int(local_fallback_context_tokens or 2048))
        self.local_fallback_output_tokens = max(32, int(local_fallback_output_tokens or 256))
        self.context_tokens = max(512, int(context_tokens)) if context_tokens else None
        self.provider_rate_limit_cooldown_sec = max(1.0, float(provider_rate_limit_cooldown_sec or 60.0))
        self.request_timeout_sec = float(request_timeout_sec or 120.0)
        self.client = AsyncOpenAI(
            base_url=base_url,
            organization=organization_id,
            project=project_id,
            api_key=llm_api_key,
            timeout=Timeout(timeout=self.request_timeout_sec, connect=min(10.0, self.request_timeout_sec)),
            max_retries=0,
        )
        self.local_fallback_client = (
            AsyncOpenAI(
                base_url=self.local_fallback_base_url,
                api_key=local_fallback_api_key or "local",
                timeout=Timeout(timeout=45.0, connect=5.0),
                max_retries=0,
            )
            if self.local_fallback_base_url and self.local_fallback_model
            else None
        )
        self.support_tools = True
        self.reraise_retryable_errors = False
        self.allow_gateway_fallback = True
        # Usados pela fila de fallback (core/model_chain.py): o erro real da última
        # chamada (para classificar 429/410/...) e quantos retries de cold-start fazer.
        self.last_error: BaseException | None = None
        self.cold_start_retries = 3
        self.extra_body: dict[str, Any] = {}

        # P7 Canary Opt-In (default OFF = "0"; preserves baseline base_url/model/persona/memory)
        self._baseline_base_url = base_url
        self._baseline_model = model
        self._baseline_api_key = llm_api_key
        self._baseline_organization_id = organization_id
        self._baseline_project_id = project_id
        self._canary_client: AsyncOpenAI | None = None
        self._canary_client_key: tuple[str, str] | None = None
        self._uncertain_canary_turns: set[str] = set()
        self.effective_base_url = base_url
        self.effective_model = model
        self._sync_canary_opt_in()

        logger.info(
            f"Initialized AsyncLLM with the parameters: {self.base_url}, {self.model}, max_tokens={self.max_tokens}"
            + (f", fallback_model={self.fallback_model}" if self.fallback_model else "")
            + (f", local_fallback={self.local_fallback_model}@{self.local_fallback_base_url}" if self.local_fallback_client else "")
        )

    @staticmethod
    def _is_canary_opt_in_enabled() -> bool:
        return os.environ.get("CELIA_SESSION_GATEWAY_OPT_IN", "0").strip() == "1"

    def _sync_canary_opt_in(self) -> bool:
        if self._is_canary_opt_in_enabled():
            canary_url = os.environ.get("CELIA_SESSION_GATEWAY_BASE_URL", "").strip()
            canary_model = os.environ.get("CELIA_SESSION_GATEWAY_MODEL", "").strip()
            if canary_url and canary_model:
                canary_key = os.environ.get("CELIA_SESSION_GATEWAY_API_KEY", "").strip() or self._baseline_api_key
                cache_key = (canary_url, canary_key)
                if self._canary_client is None or self._canary_client_key != cache_key:
                    self._canary_client = AsyncOpenAI(
                        base_url=canary_url,
                        organization=self._baseline_organization_id,
                        project=self._baseline_project_id,
                        api_key=canary_key,
                        timeout=Timeout(timeout=self.request_timeout_sec, connect=min(10.0, self.request_timeout_sec)),
                        max_retries=0,
                    )
                    self._canary_client_key = cache_key
                self.effective_base_url = canary_url
                self.effective_model = canary_model
                return True
        self.effective_base_url = self._baseline_base_url
        self.effective_model = self._baseline_model
        return False

    def revert_to_baseline(self) -> Dict[str, Any]:
        """Immediately reverts routing back to baseline without process restart."""
        os.environ["CELIA_SESSION_GATEWAY_OPT_IN"] = "0"
        self._sync_canary_opt_in()
        return {
            "enabled": False,
            "base_url": self.effective_base_url,
            "model": self.effective_model,
        }

    def mark_turn_uncertain(self, user_binding: str) -> None:
        if user_binding:
            self._uncertain_canary_turns.add(user_binding)

    def is_turn_uncertain(self, user_binding: str) -> bool:
        return bool(user_binding and user_binding in self._uncertain_canary_turns)

    @classmethod
    def _provider_key(cls, base_url: str) -> str:
        return (base_url or "").strip().rstrip("/").lower()

    @classmethod
    def _provider_cooldown_remaining(cls, base_url: str) -> float:
        key = cls._provider_key(base_url)
        with cls._provider_breaker_lock:
            until = cls._provider_breaker_until.get(key, 0.0)
        return max(0.0, until - time.monotonic())

    @classmethod
    def _trip_provider_breaker(cls, base_url: str, cooldown_sec: float) -> None:
        key = cls._provider_key(base_url)
        with cls._provider_breaker_lock:
            cls._provider_breaker_until[key] = max(
                cls._provider_breaker_until.get(key, 0.0),
                time.monotonic() + max(1.0, cooldown_sec),
            )

    @staticmethod
    def _estimate_message_tokens(messages: List[Dict[str, Any]]) -> int:
        serialized = json.dumps(messages, ensure_ascii=False, default=str)
        # Conservative fallback estimate measured on Portuguese/local-template traffic.
        # This is explicitly not a model tokenizer; provider usage supersedes it when exposed.
        return max(1, (len(serialized) + 2) // 3 + len(messages) * 4)

    @classmethod
    def _estimate_request_tokens(cls, messages: List[Dict[str, Any]], tools: Any = None) -> int:
        tool_list = [] if tools is None or tools is NOT_GIVEN else list(tools)
        serialized_chars = len(json.dumps({"messages": messages, "tools": tool_list}, ensure_ascii=False, default=str))
        return max(1, (serialized_chars + 2) // 3 + len(messages) * 4 + len(tool_list) * 8)

    @classmethod
    def _compact_for_context(
        cls, messages: List[Dict[str, Any]], *, context_tokens: int, output_tokens: int, tools: Any = None,
        template_reserve: int = 384,
    ) -> List[Dict[str, Any]]:
        input_budget = int(context_tokens) - int(output_tokens) - max(0, int(template_reserve))
        if input_budget <= 0:
            raise ContextBudgetExceeded(estimated_tokens=cls._estimate_request_tokens(messages, tools),
                                        input_budget=max(0, input_budget), context_tokens=int(context_tokens),
                                        output_tokens=int(output_tokens))

        system_messages = [dict(m) for m in messages if m.get("role") == "system"]
        conversation = [dict(m) for m in messages if m.get("role") != "system"]
        latest_turn: list[dict[str, Any]] = []
        if not conversation:
            essential = system_messages
            history_units: list[list[dict[str, Any]]] = []
        else:
            # Keep the latest user-led turn intact, including any tool replies after it.
            units: list[list[dict[str, Any]]] = []
            current: list[dict[str, Any]] = []
            for message in conversation:
                if message.get("role") == "user" and current:
                    units.append(current)
                    current = []
                current.append(message)
            if current:
                units.append(current)
            latest_turn = units[-1]
            essential = [*system_messages, *latest_turn]
            history_units = units[:-1]

        if cls._estimate_request_tokens(essential, tools) > input_budget:
            raise ContextBudgetExceeded(estimated_tokens=cls._estimate_request_tokens(essential, tools),
                                        input_budget=input_budget, context_tokens=int(context_tokens),
                                        output_tokens=int(output_tokens))

        selected_history: list[list[dict[str, Any]]] = []
        for unit in reversed(history_units):
            candidate_history = [unit, *selected_history]
            candidate = [*system_messages, *(message for group in candidate_history for message in group), *latest_turn]
            if cls._estimate_request_tokens(candidate, tools) > input_budget:
                break  # older turns are optional; preserve chronological history as a suffix
            selected_history = candidate_history

        result = [*system_messages, *(message for group in selected_history for message in group)]
        result.extend(latest_turn)
        return result

    @staticmethod
    def _normalize_text_only_content(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Collapse text-only multimodal blocks for stricter compatible endpoints."""
        normalized_messages = []
        for message in messages:
            content = message.get("content")
            if (
                isinstance(content, list)
                and content
                and all(
                    isinstance(part, dict) and part.get("type") == "text"
                    for part in content
                )
            ):
                message = {**message, "content": "".join(part.get("text", "") for part in content)}
            normalized_messages.append(message)
        return normalized_messages

    @staticmethod
    def _training_safe_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        safe: List[Dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role") or "")
            content = message.get("content")
            if isinstance(content, str):
                safe_content: Any = content
            elif isinstance(content, list):
                parts = []
                for part in content:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        parts.append(str(part.get("text") or ""))
                    elif part.get("type") in {"image", "image_url", "input_image"}:
                        parts.append("[IMAGE_OMITTED_FROM_TRAINING_TRACE]")
                safe_content = "\n".join(parts)
            else:
                safe_content = str(content or "")
            safe.append({"role": role, "content": safe_content})
        return safe

    async def chat_completion(
        self,
        messages: List[Dict[str, Any]],
        system: str = None,
        tools: List[Dict[str, Any]] | NotGiven = NOT_GIVEN,
    ) -> AsyncIterator[str | List[ChoiceDeltaToolCall]]:
        """
        Generates a chat completion using the OpenAI API asynchronously.

        Parameters:
        - messages (List[Dict[str, Any]]): The list of messages to send to the API.
        - system (str, optional): System prompt to use for this completion.
        - tools (List[Dict[str, str]], optional): List of tools to use for this completion.

        Yields:
        - str: The content of each chunk from the API response.
        - List[ChoiceDeltaToolCall]: The tool calls detected in the response.

        Raises:
        - APIConnectionError: When the server cannot be reached
        - RateLimitError: When a 429 status code is received
        - APIError: For other API-related errors
        """
        canary_active = self._sync_canary_opt_in()
        canary_user_binding = os.environ.get("CELIA_SESSION_GATEWAY_USER_BINDING", "").strip() if canary_active else ""
        if canary_active and canary_user_binding and self.is_turn_uncertain(canary_user_binding):
            raise RuntimeError(f"UNCERTAIN_TURN_RESEND_FORBIDDEN: {canary_user_binding}")

        stream = None
        call_started = time.monotonic()
        llm_call_id = latency_trace.llm_begin(
            role=latency_trace.current_llm_role("tool" if tools is not NOT_GIVEN else "conversation"),
            model=self.effective_model if canary_active else self.model,
            endpoint=self.effective_base_url if canary_active else self.base_url,
            measurement="openai_sdk_stream",
        )
        active_model = self.effective_model if canary_active else self.model
        fallback_used = False
        first_stream_event_seen = False
        first_text_seen = False
        first_text_at = None
        last_text_at = None
        completion_tokens = None
        prompt_tokens = None
        token_count_source = "unavailable"
        prompt_token_count_source = "unavailable"
        prompt_chars = None
        estimated_prompt_tokens = None
        estimated_prompt_token_source = "unavailable"
        output_chars = 0
        output_text_parts: list[str] = []
        tool_used = False
        call_error: str | None = None
        # Tool call related state variables
        accumulated_tool_calls = {}
        in_tool_call = False

        try:
            # If system prompt is provided, add it to the messages
            messages_with_system = self._normalize_text_only_content(messages)
            if system:
                messages_with_system = [
                    {"role": "system", "content": system},
                    *messages_with_system,
                ]
            logger.debug(
                "LLM request prepared: message_count=%d estimated_tokens=%d",
                len(messages_with_system), self._estimate_request_tokens(messages_with_system, tools),
            )

            available_tools = tools if self.support_tools and tools is not None else NOT_GIVEN

            if self.context_tokens is not None:
                try:
                    messages_with_system = self._compact_for_context(
                        messages_with_system, context_tokens=self.context_tokens,
                        output_tokens=self.max_tokens, tools=available_tools,
                    )
                except ContextBudgetExceeded as exc:
                    latency_trace.llm_event(
                        llm_call_id, "llm_context_budget_rejected",
                        estimated_tokens=exc.estimated_tokens, input_budget=exc.input_budget,
                        context_tokens=exc.context_tokens, output_tokens=exc.output_tokens,
                    )
                    call_error = type(exc).__name__
                    # Prefixo de erro: a fila pula para o próximo elo em vez de falar isto (live 25/10 e 28/10)
                    yield "Error calling the chat endpoint: The current request exceeds the configured context budget."
                    return

            def trace_request_size(messages_to_send, tools_to_send, model_name, provider_role):
                # Keep sizes only; prompt contents can contain private conversation data.
                payload = {"messages": messages_to_send}
                if tools_to_send is not NOT_GIVEN:
                    payload["tools"] = tools_to_send
                chars = len(json.dumps(payload, ensure_ascii=False, default=str))
                estimated = self._estimate_request_tokens(messages_to_send, tools_to_send)
                latency_trace.llm_event(
                    llm_call_id,
                    "llm_prompt_size",
                    model=model_name,
                    provider_role=provider_role,
                    prompt_chars=chars,
                    estimated_prompt_tokens=estimated,
                    prompt_token_count_source="estimated_chars_div_3_structured",
                    message_count=len(messages_to_send),
                    tool_count=len(tools_to_send) if tools_to_send is not NOT_GIVEN else 0,
                )
                return chars, estimated

            prompt_chars, estimated_prompt_tokens = trace_request_size(messages_with_system, available_tools, self.model, "primary")
            estimated_prompt_token_source = "estimated_chars_div_3_structured"

            # Fase 4+ (jun/2026): força 1 tool call por vez (NVIDIA llama-3.1-8b e
            # outros modelos pequenos quebram com parallel tool calls — erro 500
            # "This model only supports single tool-calls at once"). Tools que
            # precisam ser encadeadas (parar+seguir) viram 2 turns.
            create_kwargs = dict(
                messages=messages_with_system,
                model=self.effective_model if canary_active else self.model,
                stream=True,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                tools=available_tools,
            )
            if canary_active and canary_user_binding:
                create_kwargs["user"] = canary_user_binding
            if available_tools:
                create_kwargs["parallel_tool_calls"] = False
            if getattr(self, "extra_body", None):  # ex.: {"reasoning_effort": "none"} por elo da fila
                create_kwargs["extra_body"] = dict(self.extra_body)
            # Retry no 404 transitório de cold-start (NVIDIA NIM free tier às vezes responde
            # "Specified function in account is not found" quando a função de inferência está
            # fria; recupera em segundos). Retry só na CRIAÇÃO do stream (antes de qualquer
            # token) → seguro, sem output parcial. Outros erros sobem normal.
            stream = None
            self.last_error = None
            _max_retries = max(1, int(getattr(self, "cold_start_retries", 3)))
            provider_cooldown = self._provider_cooldown_remaining(self.base_url)
            if provider_cooldown > 0 and self.local_fallback_client is not None and tools is NOT_GIVEN:
                local_kwargs = dict(create_kwargs)
                local_kwargs.pop("tools", None)
                local_kwargs.pop("parallel_tool_calls", None)
                local_kwargs["model"] = self.local_fallback_model
                local_kwargs["temperature"] = 0.0
                local_kwargs["max_tokens"] = min(self.max_tokens, self.local_fallback_output_tokens)
                local_kwargs["messages"] = self._compact_for_context(
                    messages_with_system, context_tokens=self.local_fallback_context_tokens,
                    output_tokens=local_kwargs["max_tokens"],
                )
                prompt_chars, estimated_prompt_tokens = trace_request_size(
                    local_kwargs["messages"], NOT_GIVEN, self.local_fallback_model, "local_fallback_cooldown"
                )
                estimated_prompt_token_source = "estimated_chars_div_3_structured"
                logger.warning(
                    f"Provider {self.base_url} em cooldown por rate limit ({provider_cooldown:.0f}s); "
                    f"pulando direto para LOCAL fallback {self.local_fallback_model} "
                    f"({self._estimate_message_tokens(local_kwargs['messages'])} tokens estimados)"
                )
                local_attempt_started = time.monotonic()
                latency_trace.llm_event(
                    llm_call_id,
                    "llm_provider_attempt_start",
                    provider_role="local_fallback_cooldown",
                    model=self.local_fallback_model,
                    endpoint=self.local_fallback_base_url,
                    attempt=1,
                )
                try:
                    stream = await self.local_fallback_client.chat.completions.create(**local_kwargs)
                except Exception as local_cooldown_err:
                    latency_trace.llm_event(
                        llm_call_id,
                        "llm_provider_attempt_end",
                        provider_role="local_fallback_cooldown",
                        model=self.local_fallback_model,
                        endpoint=self.local_fallback_base_url,
                        status="error",
                        error_type=type(local_cooldown_err).__name__,
                        duration_ms=int((time.monotonic() - local_attempt_started) * 1000),
                    )
                    raise
                latency_trace.llm_event(
                    llm_call_id,
                    "llm_provider_attempt_end",
                    provider_role="local_fallback_cooldown",
                    model=self.local_fallback_model,
                    endpoint=self.local_fallback_base_url,
                    status="stream_open",
                    duration_ms=int((time.monotonic() - local_attempt_started) * 1000),
                )
                active_model = f"local:{self.local_fallback_model}"
                fallback_used = True
            for _attempt in range(_max_retries) if stream is None else ():
                primary_attempt_started = time.monotonic()
                latency_trace.llm_event(
                    llm_call_id,
                    "llm_provider_attempt_start",
                    provider_role="primary",
                    model=self.model,
                    endpoint=self.base_url,
                    attempt=_attempt + 1,
                )
                try:
                    target_client = self._canary_client if (canary_active and self._canary_client is not None) else self.client
                    stream = await target_client.chat.completions.create(**create_kwargs)
                    latency_trace.llm_event(
                        llm_call_id,
                        "llm_provider_attempt_end",
                        provider_role="primary",
                        model=self.model,
                        endpoint=self.base_url,
                        attempt=_attempt + 1,
                        status="stream_open",
                        duration_ms=int((time.monotonic() - primary_attempt_started) * 1000),
                    )
                    break
                except (RateLimitError, APIConnectionError, APITimeoutError, APIStatusError) as _primary_err:
                    latency_trace.llm_event(
                        llm_call_id,
                        "llm_provider_attempt_end",
                        provider_role="primary",
                        model=self.model,
                        endpoint=self.base_url,
                        attempt=_attempt + 1,
                        status="error",
                        error_type=type(_primary_err).__name__,
                        status_code=getattr(_primary_err, "status_code", None),
                        duration_ms=int((time.monotonic() - primary_attempt_started) * 1000),
                    )
                    primary_rate_limited = isinstance(_primary_err, RateLimitError) or getattr(_primary_err, "status_code", None) == 429
                    if primary_rate_limited:
                        self._trip_provider_breaker(self.base_url, self.provider_rate_limit_cooldown_sec)
                    # Fallback is safe only before streaming starts. Once any
                    # output exists, replacing a model could duplicate a reply.
                    # Character-only local fallback is allowed only for plain text turns.
                    # MCP/tool routing remains on the orchestrator/tool LLM.
                    if self.local_fallback_client is not None and tools is NOT_GIVEN:
                        local_kwargs = dict(create_kwargs)
                        local_kwargs.pop("tools", None)
                        local_kwargs.pop("parallel_tool_calls", None)
                        local_kwargs["model"] = self.local_fallback_model
                        local_kwargs["temperature"] = 0.0
                        local_kwargs["max_tokens"] = min(int(local_kwargs.get("max_tokens") or 256), self.local_fallback_output_tokens)
                        local_kwargs["messages"] = self._compact_for_context(
                            messages_with_system, context_tokens=self.local_fallback_context_tokens,
                            output_tokens=local_kwargs["max_tokens"],
                        )
                        prompt_chars, estimated_prompt_tokens = trace_request_size(
                            local_kwargs["messages"], NOT_GIVEN, self.local_fallback_model, "local_fallback"
                        )
                        estimated_prompt_token_source = "estimated_chars_div_3_structured"
                        logger.warning(
                            f"Primary model {self.model} failed before output "
                            f"({type(_primary_err).__name__}); trying LOCAL fallback {self.local_fallback_model} "
                            f"with {self._estimate_message_tokens(local_kwargs['messages'])} estimated input tokens"
                        )
                        local_attempt_started = time.monotonic()
                        latency_trace.llm_event(
                            llm_call_id,
                            "llm_provider_attempt_start",
                            provider_role="local_fallback",
                            model=self.local_fallback_model,
                            endpoint=self.local_fallback_base_url,
                            attempt=1,
                            trigger_error=type(_primary_err).__name__,
                        )
                        try:
                            stream = await self.local_fallback_client.chat.completions.create(**local_kwargs)
                            latency_trace.llm_event(
                                llm_call_id,
                                "llm_provider_attempt_end",
                                provider_role="local_fallback",
                                model=self.local_fallback_model,
                                endpoint=self.local_fallback_base_url,
                                attempt=1,
                                status="stream_open",
                                duration_ms=int((time.monotonic() - local_attempt_started) * 1000),
                            )
                            active_model = f"local:{self.local_fallback_model}"
                            fallback_used = True
                            logger.info(f"Local fallback {self.local_fallback_model} accepted the request")
                            break
                        except Exception as _local_err:
                            latency_trace.llm_event(
                                llm_call_id,
                                "llm_provider_attempt_end",
                                provider_role="local_fallback",
                                model=self.local_fallback_model,
                                endpoint=self.local_fallback_base_url,
                                attempt=1,
                                status="error",
                                error_type=type(_local_err).__name__,
                                duration_ms=int((time.monotonic() - local_attempt_started) * 1000),
                            )
                            logger.error(f"Local fallback also failed: {_local_err}")
                    if self.allow_gateway_fallback and self.fallback_model and self.model != self.fallback_model and not primary_rate_limited:
                        gateway_kwargs = dict(create_kwargs)
                        gateway_kwargs["model"] = self.fallback_model
                        logger.warning(f"Trying gateway fallback {self.fallback_model}")
                        gateway_fallback_started = time.monotonic()
                        latency_trace.llm_event(
                            llm_call_id,
                            "llm_provider_attempt_start",
                            provider_role="gateway_fallback",
                            model=self.fallback_model,
                            endpoint=self.base_url,
                            attempt=1,
                            trigger_error=type(_primary_err).__name__,
                        )
                        try:
                            stream = await self.client.chat.completions.create(**gateway_kwargs)
                            latency_trace.llm_event(
                                llm_call_id,
                                "llm_provider_attempt_end",
                                provider_role="gateway_fallback",
                                model=self.fallback_model,
                                endpoint=self.base_url,
                                attempt=1,
                                status="stream_open",
                                duration_ms=int((time.monotonic() - gateway_fallback_started) * 1000),
                            )
                            active_model = self.fallback_model
                            fallback_used = True
                            logger.info(f"Fallback {self.fallback_model} accepted the request")
                            break
                        except Exception as _fb_err:
                            latency_trace.llm_event(
                                llm_call_id,
                                "llm_provider_attempt_end",
                                provider_role="gateway_fallback",
                                model=self.fallback_model,
                                endpoint=self.base_url,
                                attempt=1,
                                status="error",
                                error_type=type(_fb_err).__name__,
                                duration_ms=int((time.monotonic() - gateway_fallback_started) * 1000),
                            )
                            logger.error(f"Gateway fallback also failed: {_fb_err}")
                            raise _primary_err
                    if primary_rate_limited and self.fallback_model and self.model != self.fallback_model:
                        logger.warning(
                            f"Skipping gateway fallback {self.fallback_model}: same provider/quota {self.base_url} is rate-limited"
                        )
                    raise
                except APIError as _api_err:
                    latency_trace.llm_event(
                        llm_call_id,
                        "llm_provider_attempt_end",
                        provider_role="primary",
                        model=self.model,
                        endpoint=self.base_url,
                        attempt=_attempt + 1,
                        status="error",
                        error_type=type(_api_err).__name__,
                        status_code=getattr(_api_err, "status_code", None),
                        duration_ms=int((time.monotonic() - primary_attempt_started) * 1000),
                    )
                    _msg = str(_api_err).lower()
                    _is_cold = (
                        getattr(_api_err, "status_code", None) == 404
                        or "not found" in _msg
                        or "specified function" in _msg
                    )
                    if _is_cold and _attempt < _max_retries - 1:
                        _wait = 1.5 * (_attempt + 1)
                        logger.warning(
                            f"NVIDIA/LLM 404 cold-start no modelo {self.model} "
                            f"(tentativa {_attempt+1}/{_max_retries}); retry em {_wait:.1f}s"
                        )
                        latency_trace.llm_event(
                            llm_call_id,
                            "llm_retry_backoff",
                            provider_role="primary",
                            model=self.model,
                            endpoint=self.base_url,
                            attempt=_attempt + 1,
                            backoff_ms=int(_wait * 1000),
                            reason="cold_start_404",
                        )
                        await asyncio.sleep(_wait)
                        continue
                    raise
            latency_trace.llm_event(
                llm_call_id,
                "llm_stream_open",
                elapsed_ms=int((time.monotonic() - call_started) * 1000),
                model=active_model,
                endpoint=self.base_url,
                measurement="sdk_stream_object_returned_not_raw_first_byte",
                gateway_queue_ms=None,
            )
            logger.debug(
                f"Tool Support: {self.support_tools}, Available tools: {available_tools}"
            )

            async for chunk in stream:
                now = time.monotonic()
                if not first_stream_event_seen:
                    first_stream_event_seen = True
                    latency_trace.llm_event(
                        llm_call_id,
                        "gateway_first_stream_event",
                        elapsed_ms=int((now - call_started) * 1000),
                        measurement="first_sdk_stream_chunk_not_raw_first_byte",
                    )
                usage = getattr(chunk, "usage", None)
                if usage is not None:
                    measured_completion = getattr(usage, "completion_tokens", None)
                    if measured_completion is not None:
                        try:
                            completion_tokens = int(measured_completion)
                            token_count_source = "provider_stream_usage"
                        except (TypeError, ValueError):
                            pass
                    measured_prompt = getattr(usage, "prompt_tokens", None)
                    if measured_prompt is not None:
                        try:
                            prompt_tokens = int(measured_prompt)
                            prompt_token_count_source = "provider_stream_usage"
                        except (TypeError, ValueError):
                            pass
                if not chunk.choices:  # None or [] = usage/heartbeat chunk; len(None) was killing every reply (2026-10-21)
                    logger.debug("Empty chunk received (usage/heartbeat), skipping")
                    continue
                if self.support_tools:
                    has_tool_calls = (
                        hasattr(chunk.choices[0].delta, "tool_calls")
                        and chunk.choices[0].delta.tool_calls
                    )

                    if has_tool_calls:
                        tool_used = True
                        logger.debug(
                            f"Tool calls detected in chunk: {chunk.choices[0].delta.tool_calls}"
                        )
                        in_tool_call = True
                        # Process tool calls in the current chunk
                        for tool_call in chunk.choices[0].delta.tool_calls:
                            index = (
                                tool_call.index if hasattr(tool_call, "index") else 0
                            )

                            # Initialize tool call for this index if needed
                            if index not in accumulated_tool_calls:
                                accumulated_tool_calls[index] = {
                                    "index": index,
                                    "id": getattr(tool_call, "id", None),
                                    "type": getattr(tool_call, "type", None),
                                    "function": {"name": "", "arguments": ""},
                                }

                            # Update tool call information
                            if hasattr(tool_call, "id") and tool_call.id:
                                accumulated_tool_calls[index]["id"] = tool_call.id
                            if hasattr(tool_call, "type") and tool_call.type:
                                accumulated_tool_calls[index]["type"] = tool_call.type

                            # Update function information
                            if hasattr(tool_call, "function"):
                                if (
                                    hasattr(tool_call.function, "name")
                                    and tool_call.function.name
                                ):
                                    accumulated_tool_calls[index]["function"][
                                        "name"
                                    ] = tool_call.function.name
                                if (
                                    hasattr(tool_call.function, "arguments")
                                    and tool_call.function.arguments
                                ):
                                    accumulated_tool_calls[index]["function"][
                                        "arguments"
                                    ] += tool_call.function.arguments

                        continue

                    # If we were in a tool call but now we're not, yield the tool call result
                    elif in_tool_call and not has_tool_calls:
                        in_tool_call = False
                        # Convert accumulated tool calls to the required format and output
                        logger.info(f"Complete tool calls: {accumulated_tool_calls}")

                        # Use the from_dict method to create a ToolCallObject instance from a dictionary
                        complete_tool_calls = [
                            ToolCallObject.from_dict(tool_data)
                            for tool_data in accumulated_tool_calls.values()
                        ]

                        yield complete_tool_calls
                        accumulated_tool_calls = {}  # Reset for potential future tool calls

                # Process regular content chunks
                if chunk.choices[0].delta.content is None:
                    chunk.choices[0].delta.content = ""
                text = chunk.choices[0].delta.content
                if not isinstance(text, str):  # Cloudflare manda resposta numérica (ex.: 5) como int
                    text = str(text)
                output_chars += len(text)
                if text:
                    token_now = time.monotonic()
                    if not first_text_seen:
                        first_text_seen = True
                        first_text_at = token_now
                        latency_trace.llm_event(
                            llm_call_id,
                            "llm_first_text_token",
                            elapsed_ms=int((token_now - call_started) * 1000),
                        )
                    last_text_at = token_now
                    output_text_parts.append(text)
                yield text

            if last_text_at is not None:
                latency_trace.llm_event(
                    llm_call_id,
                    "llm_last_text_token",
                    elapsed_ms=int((last_text_at - call_started) * 1000),
                    output_chars=output_chars,
                )

            # If stream ends while still in a tool call, make sure to yield the tool call
            if in_tool_call and accumulated_tool_calls:
                logger.info(f"Final tool call at stream end: {accumulated_tool_calls}")

                # Create a ToolCallObject instance from a dictionary using the from_dict method.
                complete_tool_calls = [
                    ToolCallObject.from_dict(tool_data)
                    for tool_data in accumulated_tool_calls.values()
                ]

                yield complete_tool_calls

        except APIConnectionError as e:
            call_error = type(e).__name__
            self.last_error = e
            if canary_active and canary_user_binding and first_stream_event_seen:
                self.mark_turn_uncertain(canary_user_binding)
            logger.error(
                f"Error calling the chat endpoint: Connection error. Failed to connect to the LLM API. \nCheck the configurations and the reachability of the LLM backend. \nSee the logs for details. \nTroubleshooting with documentation: /docs/faq#%E9%81%87%E5%88%B0-error-calling-the-chat-endpoint-%E9%94%99%E8%AF%AF%E6%80%8E%E4%B9%88%E5%8A%9E \n{e.__cause__}"
            )
            yield "Error calling the chat endpoint: Connection error. Failed to connect to the LLM API. Check the configurations and the reachability of the LLM backend. See the logs for details. Troubleshooting with documentation: [/docs/faq#%E9%81%87%E5%88%B0-error-calling-the-chat-endpoint-%E9%94%99%E8%AF%AF%E6%80%8E%E4%B9%88%E5%8A%9E]"

        except RateLimitError as e:
            call_error = type(e).__name__
            self.last_error = e
            if self.reraise_retryable_errors:
                raise
            logger.error(
                f"Error calling the chat endpoint: Rate limit exceeded: {e.response}"
            )
            yield "Error calling the chat endpoint: Rate limit exceeded. Please try again later. See the logs for details."

        except AuthenticationError as e:
            call_error = "AuthenticationError"
            self.last_error = e
            if self.reraise_retryable_errors:
                raise
            logger.error("LLM API authentication failed.")
            yield "Error calling the chat endpoint: Authentication failed. Check the API key."

        except APIError as e:
            call_error = type(e).__name__
            self.last_error = e
            if canary_active and canary_user_binding and first_stream_event_seen:
                self.mark_turn_uncertain(canary_user_binding)
            error_text = str(e)
            if "does not support tools" in error_text:
                self.support_tools = False
                logger.warning(
                    f"{self.model} does not support tools. Disabling tool support."
                )
                # Only emit this control token when native tool mode is active.
                # In normal chat mode this must never leak to user-facing output.
                if tools is not NOT_GIVEN:
                    yield "__API_NOT_SUPPORT_TOOLS__"
                else:
                    yield "Error calling the chat endpoint: Error occurred while generating response. See the logs for details."
                return
            # ── Handle provider-side tool validation failures (e.g. Groq/Llama) ──
            # Some providers reject malformed tool arguments generated by the model.
            # Instead of silently retrying without tools, force prompt-mode fallback
            # so MCP tools can still be executed via JSON extraction.
            should_retry_without_tools = (
                tools is not NOT_GIVEN
                and (
                    "tool call validation failed" in error_text
                    or "Failed to call a function" in error_text
                    or "failed_generation" in error_text
                )
            )
            if should_retry_without_tools:
                logger.warning(
                    "Tool calling failed at provider side. "
                    "Switching to prompt-mode tool fallback. "
                    f"Error: {e}"
                )
                self.support_tools = False
                yield "__API_NOT_SUPPORT_TOOLS__"
                return
            logger.error(f"LLM API: Error occurred: {e}")
            logger.info(f"Base URL: {self.base_url}")
            logger.info(f"Model: {self.model}")
            logger.info(f"Messages: {messages}")
            logger.info(f"temperature: {self.temperature}")
            yield "Error calling the chat endpoint: Error occurred while generating response. See the logs for details."

        finally:
            tokens_per_sec = None
            estimated_completion_tokens = None
            estimated_tokens_per_sec = None
            if first_text_at is not None and last_text_at is not None:
                generation_seconds = max(0.001, last_text_at - first_text_at)
                if completion_tokens is not None:
                    tokens_per_sec = round(completion_tokens / generation_seconds, 3)
                elif output_chars > 0:
                    estimated_completion_tokens = max(1, round(output_chars / 4))
                    estimated_tokens_per_sec = round(
                        estimated_completion_tokens / generation_seconds, 3
                    )
            latency_trace.llm_end(
                llm_call_id,
                model_used=active_model,
                prompt_chars=prompt_chars,
                prompt_tokens=prompt_tokens,
                estimated_prompt_tokens=estimated_prompt_tokens,
                prompt_token_count_source=prompt_token_count_source if prompt_tokens is not None else estimated_prompt_token_source,
                completion_tokens=completion_tokens,
                estimated_completion_tokens=estimated_completion_tokens,
                token_count_source=token_count_source,
                estimated_token_count_source=(
                    "estimated_chars_div_4" if estimated_completion_tokens is not None else "unavailable"
                ),
                tokens_per_sec=tokens_per_sec,
                estimated_tokens_per_sec=estimated_tokens_per_sec,
                output_chars=output_chars,
                fallback_used=fallback_used,
                success=call_error is None,
                error_type=call_error or "",
            )
            training_trace.capture(
                "llm_call_complete",
                {
                    "model_requested": self.model,
                    "model_used": active_model,
                    "base_url": self.base_url,
                    "mode": "tool" if tools is not NOT_GIVEN else "conversation",
                    "system": system or "",
                    "messages": self._training_safe_messages(messages),
                    "output": "".join(output_text_parts),
                    "latency_ms": int((time.monotonic() - call_started) * 1000),
                    "fallback_used": fallback_used,
                    "tool_used": tool_used,
                    "success": call_error is None,
                    "error_type": call_error or "",
                },
                layer="llm",
                source="openai_compatible",
                trainable=True,
            )
            llm_telemetry.record(
                model=active_model,
                mode="tool" if tools is not NOT_GIVEN else "conversation",
                latency_ms=int((time.monotonic() - call_started) * 1000),
                success=call_error is None,
                fallback_used=fallback_used,
                tool_used=tool_used,
                output_chars=output_chars,
                error_type=call_error,
            )
            # make sure the stream is properly closed
            # so when interrupted, no more tokens will being generated.
            if stream:
                logger.debug("Chat completion finished.")
                await stream.close()
                logger.debug("Stream closed.")
