from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from celia.agent.stateless_llm.openai_compatible_llm import AsyncLLM
from celia.core.llm_telemetry import LLMCallTelemetry


class _Stream:
    def __init__(self) -> None:
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._sent:
            raise StopAsyncIteration
        self._sent = True
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="pronta", tool_calls=None))])

    async def close(self):
        pass


def test_pre_output_connection_failure_uses_configured_fallback():
    llm = AsyncLLM(
        model="primary", fallback_model="fallback", base_url="http://example.invalid/v1"
    )
    calls = []

    async def create(**kwargs):
        calls.append(kwargs["model"])
        if kwargs["model"] == "primary":
            raise APIConnectionError(request=httpx.Request("POST", "http://example.invalid"))
        return _Stream()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "oi"}])]

    assert asyncio.run(collect()) == ["pronta"]
    assert calls == ["primary", "fallback"]


def test_llm_telemetry_never_keeps_prompt_text():
    telemetry = LLMCallTelemetry()
    telemetry.record(
        model="model-a", mode="conversation", latency_ms=12, success=True,
        fallback_used=False, tool_used=False, output_chars=20,
    )

    item = telemetry.safe_snapshot()["recent_calls"][0]

    assert item["tokens_out_estimate"] == 5
    assert "prompt" not in item
    assert "text" not in item


def test_complete_request_size_is_traced_without_persisting_prompt_content(tmp_path, monkeypatch):
    import json
    import celia.agent.stateless_llm.openai_compatible_llm as llm_module
    from celia.core.latency_trace import LatencyTraceRecorder

    monkeypatch.setenv("CELIA_LATENCY_TRACE_ENABLED", "1")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_DIR", str(tmp_path))
    recorder = LatencyTraceRecorder()
    monkeypatch.setattr(llm_module, "latency_trace", recorder)
    llm = AsyncLLM(model="test-model", base_url="http://example.invalid/v1")

    async def create(**kwargs):
        return _Stream()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    secret_prompt = "private-persona-context-unique-marker"

    async def collect():
        return [part async for part in llm.chat_completion(
            [{"role": "user", "content": "oi"}], system=secret_prompt,
            tools=[{"type": "function", "function": {"name": "test_tool"}}],
        )]

    assert asyncio.run(collect()) == ["pronta"]
    assert recorder.flush()
    rows = [json.loads(line) for path in tmp_path.glob("celia-latency-*.jsonl") for line in path.read_text().splitlines()]
    measured = next(row for row in rows if row.get("event") == "llm_prompt_size")
    assert measured["prompt_chars"] > len(secret_prompt)
    assert measured["estimated_prompt_tokens"] >= measured["prompt_chars"] // 3
    assert measured["message_count"] == 2 and measured["tool_count"] == 1
    assert secret_prompt not in "\n".join(path.read_text() for path in tmp_path.glob("*.jsonl"))


def test_context_compaction_preserves_all_system_and_latest_turn_without_truncation():
    messages = [
        {"role": "system", "content": "persona " * 40},
        {"role": "system", "content": "permissions " * 20},
        {"role": "user", "content": "old request " * 300},
        {"role": "assistant", "content": "old response " * 300},
        {"role": "user", "content": "current request " * 5},
        {"role": "assistant", "content": "current tool call"},
        {"role": "tool", "content": "current tool result"},
    ]
    compacted = AsyncLLM._compact_for_context(messages, context_tokens=2048, output_tokens=256)

    assert compacted[:2] == messages[:2]
    assert compacted[-3:] == messages[-3:]
    for original in [*messages[:2], *messages[-3:]]:
        assert original in compacted
    assert len(compacted) < len(messages)


def test_context_compaction_rejects_oversized_essential_content_and_counts_tools():
    from celia.agent.stateless_llm.openai_compatible_llm import ContextBudgetExceeded

    messages = [{"role": "system", "content": "persona " * 1000}, {"role": "user", "content": "keep me"}]
    with pytest.raises(ContextBudgetExceeded):
        AsyncLLM._compact_for_context(messages, context_tokens=2048, output_tokens=256)

    plain = AsyncLLM._estimate_request_tokens([{ "role": "user", "content": "hi" }])
    with_tools = AsyncLLM._estimate_request_tokens(
        [{"role": "user", "content": "hi"}],
        [{"type": "function", "function": {"name": "long_tool", "parameters": {"description": "schema " * 300}}}],
    )
    assert with_tools > plain


def _rate_limit_error():
    from openai import RateLimitError
    response = httpx.Response(429, request=httpx.Request("POST", "http://gateway.invalid/v1/chat/completions"))
    return RateLimitError("quota exhausted", response=response, body={"error": {"message": "quota exhausted"}})


def test_local_fallback_compacts_large_context_and_preserves_latest_user():
    messages = [{"role": "system", "content": "persona " * 200}]
    for idx in range(12):
        messages.append({"role": "assistant" if idx % 2 else "user", "content": (f"hist-{idx} " * 180)})
    messages.append({"role": "user", "content": "PEDIDO ATUAL IMPORTANTE"})

    compacted = AsyncLLM._compact_for_context(messages, context_tokens=2048, output_tokens=256)

    assert compacted[0]["role"] == "system"
    assert compacted[-1]["content"] == "PEDIDO ATUAL IMPORTANTE"
    assert AsyncLLM._estimate_message_tokens(compacted) <= 1664
    assert len(compacted) < len(messages)


def test_429_uses_compacted_local_fallback_and_skips_same_provider_gateway_fallback():
    AsyncLLM._provider_breaker_until.clear()
    llm = AsyncLLM(
        model="primary",
        fallback_model="same-provider-fallback",
        base_url="http://gateway.invalid/v1",
        local_fallback_base_url="http://local.invalid/v1",
        local_fallback_model="celia-v5",
        local_fallback_context_tokens=2048,
        local_fallback_output_tokens=256,
    )
    primary_calls = []
    local_calls = []

    async def primary_create(**kwargs):
        primary_calls.append(kwargs["model"])
        raise _rate_limit_error()

    async def local_create(**kwargs):
        local_calls.append(kwargs)
        return _Stream()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create)))
    llm.local_fallback_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=local_create)))
    messages = [{"role": "user", "content": "historico " * 1600}, {"role": "user", "content": "pedido atual"}]

    async def collect():
        return [part async for part in llm.chat_completion(messages)]

    assert asyncio.run(collect()) == ["pronta"]
    assert primary_calls == ["primary"]
    assert len(local_calls) == 1
    assert local_calls[0]["model"] == "celia-v5"
    assert AsyncLLM._estimate_message_tokens(local_calls[0]["messages"]) <= 1664
    assert llm._provider_cooldown_remaining(llm.base_url) > 0


def test_open_provider_breaker_bypasses_primary_on_next_plain_text_turn():
    AsyncLLM._provider_breaker_until.clear()
    llm = AsyncLLM(
        model="primary",
        base_url="http://gateway.invalid/v1",
        local_fallback_base_url="http://local.invalid/v1",
        local_fallback_model="celia-v5",
    )
    llm._trip_provider_breaker(llm.base_url, 30)
    primary_calls = []
    local_calls = []

    async def primary_create(**kwargs):
        primary_calls.append(kwargs)
        return _Stream()

    async def local_create(**kwargs):
        local_calls.append(kwargs)
        return _Stream()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create)))
    llm.local_fallback_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=local_create)))

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "oi"}])]

    assert asyncio.run(collect()) == ["pronta"]
    assert primary_calls == []
    assert len(local_calls) == 1


def test_rotating_tool_agent_does_not_retry_same_provider_model_when_all_keys_cool_down(monkeypatch):
    from celia.agent.stateless_llm.groq_rotating_llm import GroqRotatingLLM

    llm = GroqRotatingLLM(
        model="primary-tool",
        api_keys="k1",
        base_url="http://same-provider.invalid/v1",
        fallback_model="same-provider-fallback",
    )
    llm._cooldown_until = [9999999999.0]
    attempted = []

    async def should_not_call(*args, **kwargs):
        attempted.append((args, kwargs))
        if False:
            yield None

    monkeypatch.setattr(AsyncLLM, "chat_completion", should_not_call)

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "abre"}])]

    output = asyncio.run(collect())
    assert attempted == []
    assert output == ["As ferramentas estao temporariamente ocupadas. Tente de novo em instantes."]


def test_tool_agent_429_switches_to_independent_provider_before_output():
    from celia.agent.stateless_llm.groq_rotating_llm import GroqRotatingLLM

    llm = GroqRotatingLLM(
        model="gemini/gemini-3.1-flash-lite",
        api_keys="local-gateway",
        base_url="http://gateway.invalid/v1",
        fallback_model="cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast",
    )
    calls = []

    async def primary_create(**kwargs):
        calls.append(("primary", kwargs["model"], kwargs["tools"]))
        raise _rate_limit_error()

    async def fallback_create(**kwargs):
        calls.append(("fallback", kwargs["model"], kwargs["tools"]))
        return _Stream()

    llm._clients = [SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create)))]
    llm._quota_fallback.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )
    tool_schema = [{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}]

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "abre"}], tools=tool_schema)]

    assert asyncio.run(collect()) == ["pronta"]
    assert [(where, model) for where, model, _ in calls] == [
        ("primary", "gemini/gemini-3.1-flash-lite"),
        ("fallback", "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast"),
    ]
    assert all(schema == tool_schema for _, _, schema in calls)
    assert llm._cooldown_reason == ["quota"]

    calls.clear()
    assert asyncio.run(collect()) == ["pronta"]
    assert [where for where, _, _ in calls] == ["fallback"]


def test_tool_agent_connection_error_switches_to_independent_provider():
    from celia.agent.stateless_llm.groq_rotating_llm import GroqRotatingLLM

    llm = GroqRotatingLLM(
        model="gemini/gemini-3.1-flash-lite",
        api_keys="local-gateway",
        base_url="http://gateway.invalid/v1",
        fallback_model="cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast",
        hedge_delay_sec=0.01,
        request_budget_sec=0.2,
    )
    calls = []

    async def primary_create(**kwargs):
        calls.append(("primary", kwargs["model"]))
        raise APIConnectionError(request=httpx.Request("POST", "http://gateway.invalid/v1/chat/completions"))

    async def fallback_create(**kwargs):
        calls.append(("fallback", kwargs["model"]))
        return _Stream()

    llm._clients = [SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create)))]
    llm._quota_fallback.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "abre"}])]

    result = asyncio.run(collect())
    assert result == ["pronta"]
    assert calls == [
        ("primary", "gemini/gemini-3.1-flash-lite"),
        ("fallback", "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast"),
    ]


def test_tool_agent_circuit_breaker_skips_primary_after_two_slow_losses():
    from celia.agent.stateless_llm.groq_rotating_llm import GroqRotatingLLM

    llm = GroqRotatingLLM(
        model="gemini/gemini-3.1-flash-lite",
        api_keys="local-gateway",
        base_url="http://gateway.invalid/v1",
        fallback_model="cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast",
        hedge_delay_sec=0.01,
        request_budget_sec=0.2,
        breaker_failures=2,
        breaker_cooldown_sec=30,
    )
    calls = []

    async def primary_create(**kwargs):
        calls.append("primary")
        await asyncio.sleep(0.12)
        return _Stream()

    async def fallback_create(**kwargs):
        calls.append("fallback")
        return _Stream()

    llm._clients = [SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create)))]
    llm._quota_fallback.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    async def once():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "consulta"}])]

    assert asyncio.run(once()) == ["pronta"]
    assert asyncio.run(once()) == ["pronta"]
    assert llm._primary_breaker_open()
    before_third = list(calls)
    assert asyncio.run(once()) == ["pronta"]
    third_calls = calls[len(before_third):]
    assert third_calls == ["fallback"]


def test_tool_agent_hedges_slow_primary_without_waiting_for_primary_timeout():
    from celia.agent.stateless_llm.groq_rotating_llm import GroqRotatingLLM

    llm = GroqRotatingLLM(
        model="gemini/gemini-3.1-flash-lite",
        api_keys="local-gateway",
        base_url="http://gateway.invalid/v1",
        fallback_model="cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast",
        hedge_delay_sec=0.02,
        request_budget_sec=0.25,
    )
    calls = []

    async def primary_create(**kwargs):
        calls.append(("primary", kwargs["model"]))
        await asyncio.sleep(0.2)
        return _Stream()

    async def fallback_create(**kwargs):
        calls.append(("fallback", kwargs["model"]))
        return _Stream()

    llm._clients = [SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=primary_create)))]
    llm._quota_fallback.client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fallback_create))
    )

    async def collect():
        started = asyncio.get_running_loop().time()
        output = [part async for part in llm.chat_completion(
            [{"role": "user", "content": "consulta"}],
            tools=[{"type": "function", "function": {"name": "ping", "parameters": {"type": "object"}}}],
        )]
        return output, asyncio.get_running_loop().time() - started

    output, elapsed = asyncio.run(collect())
    assert output == ["pronta"]
    assert elapsed < 0.15
    assert calls == [
        ("primary", "gemini/gemini-3.1-flash-lite"),
        ("fallback", "cf/@cf/meta/llama-3.1-70b-instruct-fp8-fast"),
    ]


def test_numeric_delta_content_is_text_not_a_crash():
    """Cloudflare manda resposta numérica como int no delta; antes virava ERR no elo."""

    class _IntStream:
        def __init__(self) -> None:
            self._items = [5, " ok"]

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self._items:
                raise StopAsyncIteration
            content = self._items.pop(0)
            return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content, tool_calls=None))])

        async def close(self):
            pass

    llm = AsyncLLM(model="primary", base_url="http://example.invalid/v1")

    async def create(**kwargs):
        return _IntStream()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "quanto é 2+3?"}])]

    assert "".join(asyncio.run(collect())) == "5 ok"


class _StreamWithNullChoices:
    """A provider chunk can carry usage with ``choices=None`` (not an empty list). It is a heartbeat, not an error."""

    def __init__(self) -> None:
        self._parts = [
            SimpleNamespace(choices=None, usage=SimpleNamespace(prompt_tokens=10, completion_tokens=0)),
            SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="resposta", tool_calls=None))]),
        ]

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._parts:
            raise StopAsyncIteration
        return self._parts.pop(0)

    async def close(self):
        pass


def test_chunk_with_null_choices_is_skipped_not_reported_as_provider_failure():
    # 2026-10-21: every Célia link failed with "object of type 'NoneType' has no len()" (len(chunk.choices) on None),
    # so the persona stayed silent in the recording. An empty/None choices chunk must be skipped.
    llm = AsyncLLM(model="test-model", base_url="http://example.invalid/v1")

    async def create(**kwargs):
        return _StreamWithNullChoices()

    llm.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def collect():
        return [part async for part in llm.chat_completion([{"role": "user", "content": "oi"}])]

    assert asyncio.run(collect()) == ["resposta"]
