import asyncio

from celia.core.cognitive_loop import CognitiveLoop
from celia.core.continuous_behavior import ContinuousBehaviorRuntime


def test_parse_thought_keeps_bounded_social_scores():
    parsed = CognitiveLoop._parse_thought(
        '{"internal_thought":"Tenho algo útil.","live2d_expression":"curious",'
        '"should_speak":true,"confidence":1.8,"urgency":-2,"social_value":0.83}'
    )
    assert parsed["internal_thought"] == "Tenho algo útil."
    assert parsed["should_speak"] is True
    assert parsed["confidence"] == 1.0
    assert parsed["urgency"] == 0.0
    assert parsed["social_value"] == 0.83


def test_should_speak_becomes_candidate_not_direct_voice(tmp_path, monkeypatch):
    import celia.core.continuous_behavior as behavior_module

    behavior = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    behavior.select_profile("cohost")
    monkeypatch.setattr(behavior_module, "continuous_behavior", behavior)
    loop = CognitiveLoop()
    dispatched = []

    async def fake_dispatch():
        dispatched.append(loop._pending_candidate_id)
        return False

    monkeypatch.setattr(loop, "try_dispatch_pending", fake_dispatch)
    asyncio.run(loop._publish_thought_candidate({
        "internal_thought": "Agora vale comentar a troca de assunto.",
        "should_speak": True,
        "confidence": 0.9,
        "urgency": 0.5,
        "social_value": 0.9,
    }))

    assert len(behavior.candidates) == 1
    candidate = next(iter(behavior.candidates.values()))
    assert candidate.intent_type == "cognitive_contribution"
    assert candidate.status == "WAITING"
    assert dispatched == [candidate.id]


def test_low_participation_placeholder_is_reused_by_cognition(tmp_path, monkeypatch):
    import celia.core.continuous_behavior as behavior_module

    behavior = ContinuousBehaviorRuntime(tmp_path / "profile-participation.json")
    behavior.select_profile("cohost")
    placeholder = behavior.participation_opportunity()
    assert placeholder is not None
    assert behavior.participation_opportunity().id == placeholder.id
    monkeypatch.setattr(behavior_module, "continuous_behavior", behavior)

    loop = CognitiveLoop()
    async def no_dispatch():
        return False
    monkeypatch.setattr(loop, "try_dispatch_pending", no_dispatch)

    asyncio.run(loop._publish_thought_candidate({
        "internal_thought": "Tenho uma piada curta que encaixa agora.",
        "should_speak": True,
        "confidence": 0.88,
        "urgency": 0.55,
        "social_value": 0.92,
    }))

    candidate = behavior.candidates[placeholder.id]
    assert candidate.intent_type == "cognitive_contribution"
    assert candidate.semantic_intent.startswith("Tenho uma piada")
    assert loop._pending_candidate_id == placeholder.id
    assert behavior.safe_snapshot()["metrics"]["participation_opportunities"] == 1
