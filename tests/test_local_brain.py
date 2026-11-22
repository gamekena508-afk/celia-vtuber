import asyncio
import json

import pytest

from celia.core.local_brain import BrainConfigError, DecisionState, LocalBrain
from celia.core.vision_cache import VisionCache, select_vision_for_request, vision_cache


def _state(text="abre o jogo", mode="chat"):
    return DecisionState(
        turn_id="t1",
        text=text,
        mode=mode,
        celia_speaking=False,
        tool_running=False,
        screen_changed=False,
        vision_age_ms=None,
        app=None,
    )


def test_decision_state_puts_stable_fields_before_text():
    block = _state("fala final").block()
    assert block.index("modo:") < block.index("fala:")
    assert block.endswith("fala: fala final")


def test_pick_letter_prefers_highest_valid_logprob():
    payload = {
        "completion_probabilities": [
            {
                "top_logprobs": [
                    {"token": " B", "logprob": -0.2},
                    {"token": "A", "logprob": -1.0},
                    {"token": "Z", "logprob": -0.01},
                ]
            }
        ]
    }
    assert LocalBrain._pick_letter(payload, {"A": "chat", "B": "tool"}) == "B"


def test_pick_letter_falls_back_to_content():
    assert LocalBrain._pick_letter(
        {"content": " A"},
        {"A": "chat", "B": "tool"},
    ) == "A"


@pytest.mark.asyncio
async def test_decide_walks_category_and_tool(monkeypatch):
    brain = LocalBrain()
    answers = iter(["D", "B", "A"])

    async def fake_ask(*args, **kwargs):
        return next(answers)

    monkeypatch.setattr(brain, "ask", fake_ask)
    decision = await brain.decide(_state("abre o jogo da velha"))
    assert decision.route == "ferramenta"
    assert decision.category == "jogo"
    assert decision.tool == "abrir_jogo"


@pytest.mark.asyncio
async def test_roteiro_mode_cannot_choose_tool(monkeypatch):
    brain = LocalBrain()

    async def fake_ask(state, question, options, **kwargs):
        assert "D" not in options
        return "A"

    monkeypatch.setattr(brain, "ask", fake_ask)
    decision = await brain.decide(_state("vamos gravar", mode="roteiro"))
    assert decision.route == "conversa"
    assert decision.tool is None


def test_config_is_validated():
    LocalBrain._validate_config(
        {
            "modes": {
                "chat": {
                    "q1": {
                        "question": "tipo?",
                        "options": {"A": "conversa"},
                    }
                }
            }
        }
    )
    with pytest.raises(BrainConfigError):
        LocalBrain._validate_config({"modes": {"chat": {}}})


def test_vision_cache_touch_does_not_renew_analysis_age(monkeypatch):
    # 2026-10-04: touch() renews only the re-check time (verified_ts). The age that
    # matters for freshness is when the image was actually analysed.
    cache = VisionCache()
    times = iter([10.0, 12.0, 12.5])
    monkeypatch.setattr("celia.core.vision_cache.time.monotonic", lambda: next(times))
    cache.put("editor", "erro vermelho")
    cache.touch()
    note, age = cache.get()
    assert note and note.summary == "erro vermelho"
    assert age == 2500  # analysed at t=10.0, now t=12.5
    assert note.verified_ts == 12.0 and note.ts == 10.0


@pytest.mark.asyncio
async def test_boolean_strategy_uses_argmax_without_nested_scheduler(monkeypatch):
    brain = LocalBrain()
    monkeypatch.setenv("CELIA_BRAIN_STRATEGY", "boolean")
    scores = {"conversa": -0.4, "estado": 1.7, "visao": 0.2}

    async def fake_score(state, question, candidate, *, stage):
        return scores[candidate]

    monkeypatch.setattr(brain, "_raw_boolean_score", fake_score)
    chosen = await brain.ask(
        _state("como esta?"),
        "tipo?",
        {"A": "conversa", "B": "estado", "C": "visao"},
        stage="brain_q1",
        use_scheduler=False,
    )
    assert chosen == "B"


@pytest.mark.asyncio
async def test_runtime_requires_gpu_lane_before_local_brain(monkeypatch):
    import celia.core.local_brain as module
    from celia.core.gpu_scheduler import GpuScheduler
    from celia.core.local_brain import LocalBrainRuntime

    scheduler = GpuScheduler()
    monkeypatch.setattr(module, "gpu_scheduler", scheduler)
    monkeypatch.setenv("CELIA_LOCAL_BRAIN", "1")
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "0")
    runtime = LocalBrainRuntime(LocalBrain())

    with pytest.raises(RuntimeError, match="CELIA_GPU_SCHEDULER=1"):
        await runtime.decide(_state())


@pytest.mark.asyncio
async def test_decide_propagates_scheduler_bypass_to_internal_questions(monkeypatch):
    brain = LocalBrain()
    calls = []

    async def fake_ask(state, question, options, *, stage, use_scheduler=True):
        calls.append((stage, use_scheduler))
        return "A"

    monkeypatch.setattr(brain, "ask", fake_ask)
    decision = await brain.decide(_state("oi"), use_scheduler=False)
    assert decision.route == "conversa"
    assert calls == [("brain_q1", False)]


def _drain_scheduler_sync_point():
    """Yield until the single-lane runner has no current and no pending job."""

    async def _wait(scheduler):
        for _ in range(100):
            if scheduler.running is None and not scheduler.pending_count():
                return
            await asyncio.sleep(0)

    return _wait


@pytest.mark.asyncio
async def test_maybe_shadow_requires_flag_and_never_blocks(monkeypatch):
    import celia.core.local_brain as module
    from celia.core.gpu_scheduler import GpuScheduler
    from celia.core.local_brain import LocalBrainRuntime

    monkeypatch.delenv("CELIA_LOCAL_BRAIN", raising=False)
    monkeypatch.delenv("CELIA_BRAIN_SHADOW", raising=False)
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    scheduler = GpuScheduler()
    monkeypatch.setattr(module, "gpu_scheduler", scheduler)
    runtime = LocalBrainRuntime(LocalBrain())
    state = _state("abre o jogo")

    # Safe default: shadow OFF submits nothing.
    assert runtime.maybe_shadow(state, legacy_decision="conversa") is False
    assert scheduler.metrics["submitted_brain"] == 0

    monkeypatch.setenv("CELIA_BRAIN_SHADOW", "1")
    assert runtime.maybe_shadow(state, legacy_decision="conversa") is True
    assert scheduler.metrics["submitted_brain"] == 1

    await _drain_scheduler_sync_point()(scheduler)
    scheduler._runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await scheduler._runner


@pytest.mark.asyncio
async def test_maybe_shadow_records_new_vs_old_in_trace(monkeypatch, tmp_path):
    import celia.core.local_brain as module
    from celia.core.gpu_scheduler import GpuScheduler
    from celia.core.latency_trace import LatencyTraceRecorder
    from celia.core.local_brain import Decision, LocalBrain, LocalBrainRuntime

    monkeypatch.delenv("CELIA_LOCAL_BRAIN", raising=False)
    monkeypatch.setenv("CELIA_BRAIN_SHADOW", "1")
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_ENABLED", "1")
    monkeypatch.setenv("CELIA_LATENCY_TRACE_DIR", str(tmp_path))

    recorder = LatencyTraceRecorder(max_turns=8)
    scheduler = GpuScheduler()
    monkeypatch.setattr(module, "gpu_scheduler", scheduler)
    monkeypatch.setattr(module, "latency_trace", recorder)
    runtime = LocalBrainRuntime(LocalBrain())

    async def fake_decide(brain_state, *, use_scheduler=True):
        assert use_scheduler is False
        return Decision(route="ferramenta", category="jogo", tool="abrir_jogo", q1="D")

    monkeypatch.setattr(runtime.brain, "decide", fake_decide)
    assert (
        runtime.maybe_shadow(
            _state("abre o jogo da velha"), legacy_decision="conversa"
        )
        is True
    )

    await _drain_scheduler_sync_point()(scheduler)
    assert recorder.flush(timeout_sec=2.0)

    rows = []
    for path in tmp_path.glob("celia-latency-*.jsonl"):
        rows.extend(
            json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        )
    shadow = next(row for row in rows if row.get("event") == "brain_shadow")
    assert shadow["new"]["route"] == "ferramenta"
    assert shadow["new"]["tool"] == "abrir_jogo"
    assert shadow["old"] == "conversa"
    assert shadow["match"] is False

    scheduler._runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await scheduler._runner


@pytest.mark.asyncio
async def test_maybe_shadow_is_disposable_when_gpu_busy(monkeypatch):
    import celia.core.local_brain as module
    from celia.core.gpu_scheduler import GpuScheduler
    from celia.core.local_brain import LocalBrainRuntime

    monkeypatch.delenv("CELIA_LOCAL_BRAIN", raising=False)
    monkeypatch.setenv("CELIA_BRAIN_SHADOW", "1")
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    scheduler = GpuScheduler()
    monkeypatch.setattr(module, "gpu_scheduler", scheduler)
    runtime = LocalBrainRuntime(LocalBrain())

    hold = asyncio.Event()

    async def blocker():
        await hold.wait()

    blocker_f = scheduler.submit("brain", blocker)
    await asyncio.sleep(0)

    assert (
        runtime.maybe_shadow(_state("olha isso"), legacy_decision="conversa")
        is False
    )
    assert scheduler.metrics["shadow_skipped_busy"] == 1

    hold.set()
    await blocker_f
    scheduler._runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await scheduler._runner


def test_vision_request_policy_is_explicit(monkeypatch):
    vision_cache.clear()
    off = select_vision_for_request("olha isso", policy="off")
    assert off.action == "refresh"

    vision_cache.put("editor", "timeline aberta", "erro vermelho")
    cached = select_vision_for_request("o que tem ai", policy="cache", max_age_ms=8000)
    assert cached.action == "cache"
    assert cached.note and cached.note.summary == "timeline aberta"

    fresh = select_vision_for_request("olha agora", policy="refresh", max_age_ms=8000)
    assert fresh.action == "refresh"

    monkeypatch.setattr(
        "celia.core.vision_cache.time.monotonic",
        lambda: 999999.0,
    )
    stale = select_vision_for_request("e ai?", policy="ask", max_age_ms=1)
    assert stale.action == "ask"
    vision_cache.clear()
