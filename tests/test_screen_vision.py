import asyncio

import pytest

from celia.core.cognitive_loop import CognitiveLoop
from celia.core.screen_vision import ScreenVisionAnalyzer
from celia.core.brain_router import BrainResult
from celia.core.local_vision import LocalVisionResult


def test_screen_vision_parse_compact_json_and_fences():
    parsed = ScreenVisionAnalyzer._parse(
        '```json\n{"summary":"Damas abertas, vez do vermelho.","topic":"damas",'
        '"salience":1.4,"should_comment":"true"}\n```'
    )
    assert parsed == {
        "summary": "Damas abertas, vez do vermelho.",
        "topic": "damas",
        "salience": 1.0,
        "should_comment": True,
    }


@pytest.mark.asyncio
async def test_screen_vision_delivers_only_fresh_summary(monkeypatch):
    import celia.core.cognitive_loop as cognitive_module

    # Fresh/stale delivery through the production local-only backend.
    monkeypatch.setenv("CELIA_VISION_BACKEND", "local")
    analyzer = ScreenVisionAnalyzer()
    analyzer._generation = 1
    delivered = []
    wakes = []
    async def routed_vision(**kwargs):
        return LocalVisionResult(
            raw='{"summary":"Editor aberto com timeline em reprodução.","topic":"editor","salience":0.9,"should_comment":true}',
            model="celia-qwen3.5-2b", latency_ms=120)

    class Scheduler:
        enabled = True
        async def run_job(self, kind, fn, **kwargs):
            return await fn()
    monkeypatch.setattr("celia.core.screen_vision.gpu_scheduler", Scheduler())
    monkeypatch.setattr("celia.core.screen_vision.local_vision_client.request", routed_vision)
    monkeypatch.setattr(
        cognitive_module.cognitive_loop,
        "set_screen_context",
        lambda text: delivered.append(text),
    )
    monkeypatch.setattr(
        cognitive_module.cognitive_loop,
        "request_think",
        lambda reason: wakes.append(reason) or True,
    )
    await analyzer._analyze(1, "data:image/jpeg;base64,AA==", 0.9)
    assert delivered and "timeline" in delivered[0]
    assert wakes == ["vision_screen_change"]
    assert analyzer.metrics["completed"] == 1

    analyzer._generation = 2
    delivered.clear()
    await analyzer._analyze(1, "data:image/jpeg;base64,AA==", 0.9)
    assert delivered == []
    assert analyzer.metrics["stale_dropped"] == 1


@pytest.mark.asyncio
async def test_cognitive_semantic_uses_brain_router(monkeypatch):
    loop = CognitiveLoop()

    async def routed(*args, **kwargs):
        return BrainResult(
            {
                "internal_thought": "Vale esperar.",
                "live2d_expression": "neutral",
                "should_speak": False,
                "confidence": 0.9,
                "urgency": 0.1,
                "social_value": 0.3,
            },
            "codex_luna",
            "codex/gpt-5.6-luna",
            90,
        )

    monkeypatch.setattr("celia.core.cognitive_loop.brain_router.route", routed)
    result = await loop._semantic_think("estado")
    assert result["provider"] == "codex_luna"
    assert result["internal_thought"] == "Vale esperar."

@pytest.mark.asyncio
async def test_cognitive_drops_thought_when_revision_changes(monkeypatch, tmp_path):
    import celia.core.continuous_behavior as behavior_module
    import celia.core.cognitive_loop as cognitive_module
    from celia.core.continuous_behavior import ContinuousBehaviorRuntime

    behavior = ContinuousBehaviorRuntime(tmp_path / "profile-stale.json")
    behavior.select_profile("cohost")
    monkeypatch.setattr(behavior_module, "continuous_behavior", behavior)
    monkeypatch.setattr(cognitive_module.orchestrator.context_log, "recent_summary", lambda max_chars=700: "Criador: assunto A")

    loop = CognitiveLoop()

    async def no_mc():
        return None

    async def semantic_then_change(prompt):
        behavior.revision += 1
        return {
            "internal_thought": "Comentário sobre o assunto A.",
            "live2d_expression": "think",
            "should_speak": True,
            "confidence": 0.9,
            "urgency": 0.4,
            "social_value": 0.8,
            "provider": "fake",
        }

    monkeypatch.setattr(loop, "_refresh_minecraft_context", no_mc)
    monkeypatch.setattr(loop, "_semantic_think", semantic_then_change)
    await loop._think_once()

    assert loop.last_thought() == {}
    assert loop.debug_snapshot()["stale_thoughts"] == 1


@pytest.mark.asyncio
async def test_screen_vision_local_backend_uses_gpu_lane_without_remote_router(monkeypatch):
    import celia.core.cognitive_loop as cognitive_module
    import celia.core.screen_vision as vision_module

    class FakeScheduler:
        enabled = True

        async def run_job(self, kind, fn, **kwargs):
            assert kind == "vision"
            return await fn()

        def debug_snapshot(self):
            return {"enabled": True, "running": None, "pending": {}}

    analyzer = ScreenVisionAnalyzer()
    analyzer._generation = 1
    delivered = []
    remote_called = []

    async def local_request(**kwargs):
        return LocalVisionResult(
            raw='{"summary":"Discord aberto sem erro.","topic":"discord",'
                '"salience":0.7,"should_comment":false}',
            model="q2-local-test",
            latency_ms=42,
        )

    async def remote_route(*args, **kwargs):
        remote_called.append(True)
        return None

    monkeypatch.setenv("CELIA_VISION_BACKEND", "local")
    monkeypatch.setattr(vision_module, "gpu_scheduler", FakeScheduler())
    monkeypatch.setattr(vision_module.local_vision_client, "request", local_request)
    monkeypatch.setattr(vision_module.brain_router, "route", remote_route)
    monkeypatch.setattr(
        cognitive_module.cognitive_loop,
        "set_screen_context",
        lambda text: delivered.append(text),
    )
    monkeypatch.setattr(
        cognitive_module.cognitive_loop,
        "request_think",
        lambda reason: True,
    )

    await analyzer._analyze(1, "data:image/jpeg;base64,AA==", 0.9)
    assert remote_called == []
    assert analyzer._last_provider == "local_vlm"
    assert analyzer._last_model == "q2-local-test"
    assert delivered and "Discord aberto" in delivered[0]
