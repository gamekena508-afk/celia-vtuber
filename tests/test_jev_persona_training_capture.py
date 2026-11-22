import json
import inspect
import pytest
from celia.agent.agents import basic_memory_agent as module
from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
from celia.agent.output_types import SentenceOutput, DisplayText, Actions
from celia.agent.input_types import BatchInput, TextData, TextSource
from celia.core.training_trace import TrainingTraceRecorder
from celia.core.observability import observability


def rows(path):
    f = next(path.glob('celia-training-*.jsonl'))
    return [json.loads(line) for line in f.read_text().splitlines()]


def user_turn():
    return BatchInput(texts=[TextData(TextSource.INPUT, 'Olha a tela')], metadata={'turn_id': 'turn-1'})


def test_public_chat_not_shadowed(monkeypatch):
    async def fake(_):
        yield SentenceOutput(DisplayText('Oi'), 'Oi', Actions())
    monkeypatch.setattr(BasicMemoryAgent, '_chat_function_factory', lambda self: fake)
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._set_llm(object())
    assert inspect.ismethod(agent.chat)
    assert agent._chat_func_cache is fake


@pytest.mark.asyncio
async def test_persona_final_and_context_saved(tmp_path, monkeypatch):
    monkeypatch.setenv('CELIA_TRAINING_TRACE_ENABLED', '1')
    tracer = TrainingTraceRecorder(base_dir=tmp_path)
    monkeypatch.setattr(module, 'training_trace', tracer)
    async def fake(_):
        tracer.capture('jev_context', {'intent':'comentario'}, layer='jev', turn_id='turn-1', trainable=False)
        tracer.capture('tool_result', {'tool_name': 'vision_describe_visual', 'text': 'tela'}, layer='tool', tool_id='tool-1', trainable=False)
        yield {'type': 'tool_call_status', 'tool_name': 'vision_describe_visual', 'status': 'completed', 'content': 'tela'}
        yield SentenceOutput(DisplayText('Que jogada!'), 'Que jogada!', Actions())
        yield SentenceOutput(DisplayText('Muito boa.'), 'Muito boa.', Actions())
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._chat_func_cache = fake
    with observability.trace('persona_training_regression'):
        result = [o async for o in agent.chat(user_turn())]
    assert len(result) == 3
    events = rows(tmp_path)
    assert [e['event_type'] for e in events] == ['turn_input','jev_context','tool_result','surface_status','final_response']
    assert {e['turn_id'] for e in events if e['layer'] != 'tool'} == {'turn-1'}
    assert len({e['trace_id'] for e in events}) == 1
    assert events[0]['trace_id']
    assert events[0]['payload']['texts'][0]['content'] == 'Olha a tela'
    assert events[-1]['payload']['text'] == 'Que jogada! Muito boa.'
    assert events[-1]['trainable'] is False


@pytest.mark.asyncio
async def test_interrupted_not_complete(tmp_path, monkeypatch):
    monkeypatch.setenv('CELIA_TRAINING_TRACE_ENABLED', '1')
    monkeypatch.setattr(module, 'training_trace', TrainingTraceRecorder(base_dir=tmp_path))
    async def fake(_):
        yield SentenceOutput(DisplayText('Primeira.'), 'Primeira.', Actions())
        yield SentenceOutput(DisplayText('Segunda.'), 'Segunda.', Actions())
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    agent._chat_func_cache = fake
    stream = agent.chat(user_turn())
    await stream.__anext__()
    await stream.aclose()
    assert [e['event_type'] for e in rows(tmp_path)] == ['turn_input','partial_response']
