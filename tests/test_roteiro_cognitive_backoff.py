import asyncio
import time
from unittest.mock import AsyncMock

from test_operational_profiles import behavior
from celia.core.brain_router import BrainResult, BrainRouter
from celia.core.cognitive_loop import CognitiveLoop


def test_brain_router_provider_backoff_caps_and_success_recovers(behavior):
    behavior.select_profile("roteiro")
    router = BrainRouter()
    provider = router.providers()[0]
    expected = [15, 30, 60, 120, 180, 180]
    for seconds in expected:
        started = time.monotonic()
        router._mark_failure(provider, TimeoutError("measured unavailable provider"))
        state = router._state_for(provider.name)
        assert seconds <= state["disabled_until"] - started <= seconds + 1
    router._mark_success(provider, 123)
    state = router._state_for(provider.name)
    assert state["failures"] == 0
    assert state["disabled_until"] == 0.0
    assert state["last_latency_ms"] == 123


def test_cognitive_loop_has_no_hidden_direct_provider_fallback(behavior, monkeypatch):
    behavior.select_profile("cohost")
    loop = CognitiveLoop()
    route = AsyncMock(return_value=None)
    monkeypatch.setattr("celia.core.cognitive_loop.brain_router.route", route)
    result = asyncio.run(loop._semantic_think("test"))
    assert result is None
    route.assert_awaited_once()
    assert not hasattr(loop, "_gemini_think")
    assert not hasattr(loop, "_nim_think")
    assert not hasattr(loop, "_router_think")


def test_cognitive_loop_returns_the_winning_peer_brain(behavior, monkeypatch):
    behavior.select_profile("autonomous")
    loop = CognitiveLoop()
    result = BrainResult(
        value={
            "internal_thought": "Contribuir agora",
            "live2d_expression": "neutral",
            "should_speak": True,
            "confidence": 0.8,
            "urgency": 0.5,
            "social_value": 0.8,
        },
        provider="grok45",
        model="grok-cli/grok-4.5-low",
        latency_ms=321,
    )
    route = AsyncMock(return_value=result)
    monkeypatch.setattr("celia.core.cognitive_loop.brain_router.route", route)
    thought = asyncio.run(loop._semantic_think("test"))
    assert thought and thought["provider"] == "grok45"
    route.assert_awaited_once()
