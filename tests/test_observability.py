import asyncio

import httpx
from celia.core.observability import ObservabilityRecorder
from fastapi import FastAPI

from celia.observability_routes import init_observability_routes, is_loopback_host, runtime_snapshot


def test_trace_events_are_correlated_and_redacted():
    recorder = ObservabilityRecorder(max_events=16)

    with recorder.trace("browser.conversation", {"entrypoint": "browser", "text": "must-not-leak"}) as trace:
        with trace.span("agent.basic_memory", {"input_length": 12, "content": "must-not-leak"}):
            recorder.record_tool_status("vision_describe", "running")

    events = recorder.snapshot()
    assert [event["kind"] for event in events] == ["trace_start", "span_start", "tool_status", "span_end", "trace_end"]
    assert {event["trace_id"] for event in events} == {events[0]["trace_id"]}
    assert events[1]["parent_span_id"] == events[0]["span_id"]
    assert events[2]["parent_span_id"] == events[1]["span_id"]
    assert events[0]["metadata"] == {"entrypoint": "browser.conversation"}
    assert events[1]["metadata"] == {"input_length": 12}
    assert events[2]["metadata"] == {"tool_name": "vision_describe", "tool_status": "running"}
    assert "must-not-leak" not in str(events)


def test_ring_buffer_is_bounded_and_loopback_gate_is_explicit():
    recorder = ObservabilityRecorder(max_events=2)
    with recorder.trace("bridge.process"):
        recorder.emit("one", "node.one")
        recorder.emit("two", "node.two")
        recorder.emit("three", "node.three")

    assert len(recorder.snapshot()) == 2
    assert is_loopback_host("127.0.0.1")
    assert is_loopback_host("::1")
    assert not is_loopback_host("192.168.1.2")


def test_runtime_snapshot_exposes_allowlisted_process_facts_only():
    class Value:
        def __init__(self, **values):
            self.__dict__.update(values)

    context = Value(
        character_config=Value(
            agent_config=Value(
                conversation_agent_choice="basic_memory_agent",
                agent_settings=Value(
                    basic_memory_agent=Value(
                        llm_provider="openai_compatible_llm",
                        max_history=25,
                        segment_method="pysbd",
                        use_mcpp=True,
                        memory_book_enabled=True,
                        memory_book_embeddings_enabled=True,
                        memory_book_embeddings_model="embedding-model",
                        tool_agent_enabled=True,
                        tool_agent_model="cf",
                        tool_agent_base_url="http://127.0.0.1:20127/v1",
                        tool_agent_temperature=0.2,
                        tool_agent_max_tokens=4096,
                    )
                ),
                llm_configs=Value(
                    openai_compatible_llm=Value(
                        model="cf", base_url="http://127.0.0.1:20127/v1", temperature=0.2, max_tokens=4096
                    )
                ),
            )
        ),
        tts_engine=Value(get_active_preset=lambda: {"label": "Natural"}),
    )

    snapshot = runtime_snapshot(context)

    assert snapshot["agent"]["max_history_turns"] == 25
    assert snapshot["agent"]["local_role_message_cap"] == 50
    assert snapshot["llm"]["model"] == "cf"
    assert snapshot["llm"]["provider_context_window_tokens"] is None
    assert snapshot["voice"]["active_preset"] == {"label": "Natural"}
    assert "api_key" not in str(snapshot).lower()


def test_runtime_route_is_loopback_only_and_returns_the_safe_snapshot():
    app = FastAPI()
    app.include_router(init_observability_routes())

    async def request_runtime():
        transport = httpx.ASGITransport(app=app, client=("testclient", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/debug/observability/runtime")

    response = asyncio.run(request_runtime())

    assert response.status_code == 200
    assert response.json()["llm"]["context_window_state"].startswith("NOT AVAILABLE")


def test_runtime_snapshot_exposes_profile_change_metadata_without_prompt_or_secret():
    class Value:
        pass

    snapshot = runtime_snapshot(Value())
    behavior = snapshot["continuous_behavior"]

    assert "profile_capabilities" in behavior
    assert "recent_events" in behavior
    assert "profile_prompt" not in str(behavior).lower()
    assert "api_key" not in str(behavior).lower()
