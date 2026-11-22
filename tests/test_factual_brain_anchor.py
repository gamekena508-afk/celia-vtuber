import asyncio

from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.core.brain_router import BrainResult


def _agent():
    return BasicMemoryAgent.__new__(BasicMemoryAgent)


def test_static_factual_classifier_accepts_stable_knowledge():
    agent = _agent()
    assert agent._is_static_factual_query("qual é a capital do Brasil?")
    assert agent._is_static_factual_query("quem foi Albert Einstein?")
    assert agent._is_static_factual_query("o que é Kubernetes?")
    assert agent._is_static_factual_query("quanto é 7 x 8?")


def test_static_factual_classifier_rejects_live_personal_and_tool_queries():
    agent = _agent()
    assert not agent._is_static_factual_query("qual é o presidente atual do Brasil?")
    assert not agent._is_static_factual_query("qual a cotação do dólar?")
    assert not agent._is_static_factual_query("quem é você, Célia?")
    assert not agent._is_static_factual_query("Criador é teu pai?")


def test_factual_anchor_parser_requires_confident_nonempty_fact():
    agent = _agent()
    assert agent._parse_factual_anchor('{"fact":"Brasília é a capital do Brasil.","confidence":0.99}') == {
        "fact": "Brasília é a capital do Brasil.",
        "confidence": 0.99,
    }
    assert agent._parse_factual_anchor('{"fact":"","confidence":0.99}') is None
    assert agent._parse_factual_anchor('{"fact":"talvez Brasília","confidence":0.4}') is None


def test_anchor_instruction_makes_persona_style_only():
    text = _agent()._factual_anchor_instruction("Brasília é a capital do Brasil.")
    assert "FATO-BASE: Brasília é a capital do Brasil." in text
    assert "IMUTAVEL" in text
    assert "nao substitua 'desenvolveu' por 'inventou'" in text
    assert "nem troque" in text


def test_resolve_factual_anchor_uses_brain_router(monkeypatch):
    import celia.agent.agents.basic_memory_agent as module

    async def fake_route(prompt, parser, **kwargs):
        assert "FORA da persona" in prompt
        assert "capital do Brasil" in prompt
        value = parser('{"fact":"Brasília é a capital do Brasil.","confidence":0.99}')
        return BrainResult(value, "codex_luna", "codex/gpt-5.6-luna", 123)

    monkeypatch.setattr(module.brain_router, "route", fake_route)
    monkeypatch.setattr(module.training_trace, "capture", lambda *a, **k: None)
    fact = asyncio.run(
        _agent()._resolve_factual_anchor("qual é a capital do Brasil?", turn_id="t1")
    )
    assert fact == "Brasília é a capital do Brasil."


def test_resolve_factual_anchor_skips_live_question(monkeypatch):
    import celia.agent.agents.basic_memory_agent as module

    called = False

    async def fake_route(*args, **kwargs):
        nonlocal called
        called = True
        return None

    monkeypatch.setattr(module.brain_router, "route", fake_route)
    fact = asyncio.run(
        _agent()._resolve_factual_anchor("qual é o presidente atual do Brasil?", turn_id="t2")
    )
    assert fact is None
    assert not called
