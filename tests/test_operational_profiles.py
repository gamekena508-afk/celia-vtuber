"""Acceptance coverage for the canonical operational profile matrix."""
import asyncio
import json
import wave
import math
import struct
from pathlib import Path
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from celia.core.continuous_behavior import ContinuousBehaviorRuntime, PROFILE_NAMES
from celia.core.cognitive_loop import CognitiveLoop
from celia.config_manager.continuous_behavior import ContinuousBehaviorConfig


@pytest.fixture
def behavior(tmp_path, monkeypatch):
    import celia.core.continuous_behavior as core
    import celia.conversations.single_conversation as conversation
    import celia.conversations.conversation_utils as conversation_utils
    import celia.conversations.tts_manager as tts
    import celia.agent.agents.basic_memory_agent as agent
    instance = ContinuousBehaviorRuntime(tmp_path / 'profile.json')
    for module in (core, conversation, conversation_utils, tts, agent):
        monkeypatch.setattr(module, 'continuous_behavior', instance)
    return instance


def test_operational_profiles_persist_and_gravacao_is_hybrid(tmp_path):
    path = tmp_path / 'profile.json'
    path.write_text('{"selected_profile":"autonomous"}')
    instance = ContinuousBehaviorRuntime(path)
    assert PROFILE_NAMES == {'conversation_autonomous', 'chat', 'roteiro', 'gravacao', 'cohost', 'autonomous'}
    assert instance.profile == 'autonomous'
    assert json.loads(path.read_text())['selected_profile'] == 'autonomous'
    assert instance.select_profile('cohost')['profile'] == 'cohost'
    assert instance.select_profile('autonomous')['profile'] == 'autonomous'
    config = ContinuousBehaviorConfig()
    assert set(config.profiles) == PROFILE_NAMES
    assert config.profiles['gravacao'].script_tracking is True
    assert config.profiles['gravacao'].screen_awareness == 'enabled'
    assert config.profiles['autonomous'].script_tracking is False
    assert config.profiles['autonomous'].speaking_threshold < config.profiles['cohost'].speaking_threshold


def test_autonomous_is_selectable_in_frontend():
    selector = (Path(__file__).parents[1] / "frontend" / "celia-perfis.js").read_text()
    assert 'const TRAVADOS = new Set();' in selector
    assert 'id: "gravacao"' in selector
    assert 'id: "autonomous"' in selector
    assert 'status: "EM DESENVOLVIMENTO"' not in selector
    assert PROFILE_NAMES == {"conversation_autonomous", "chat", "roteiro", "gravacao", "cohost", "autonomous"}


def test_chat_has_no_social_candidate_or_semantic_call(behavior, monkeypatch):
    loop = CognitiveLoop()
    semantic = AsyncMock(side_effect=AssertionError('chat invoked cognition'))
    monkeypatch.setattr(loop, '_semantic_think', semantic)
    asyncio.run(loop._think_once())
    assert behavior.prepare_direct_turn('Oi', 'microphone')['decision'] == 'SPEAK'
    assert not behavior.candidates
    assert not loop.request_think('social')
    semantic.assert_not_called()


def test_switch_suspends_inflight_tick_and_reactivates(behavior, monkeypatch):
    async def exercise():
        loop = CognitiveLoop()
        loop._interval = 0.01
        started = asyncio.Event()
        cancelled = asyncio.Event()
        async def tick():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        monkeypatch.setattr(loop, '_think_once', tick)
        loop.start()
        assert loop.debug_snapshot()['suspended']
        assert not loop.request_think()
        behavior.select_profile('cohost')
        await asyncio.wait_for(started.wait(), 1)
        behavior.select_profile('chat')
        await asyncio.wait_for(cancelled.wait(), 1)
        assert not loop.debug_snapshot()['running']
        behavior.select_profile('roteiro')
        assert loop.debug_snapshot()['running']
        await loop.stop()
    asyncio.run(exercise())


def test_roteiro_prompt_and_candidate_without_cohost_role(behavior, monkeypatch):
    behavior.select_profile('roteiro')
    behavior.load_script({'id': 'test', 'beats': [{'text': 'GPU com 72 GB', 'facts': ['72 GB']}]})
    loop = CognitiveLoop()
    prompt = loop._build_prompt('GPU', '', '', '', '')
    assert 'diretora metodica' in prompt and 'GPU com 72 GB' in prompt
    monkeypatch.setattr(loop, 'try_dispatch_pending', AsyncMock(return_value=False))
    asyncio.run(loop._publish_thought_candidate({'should_speak': True, 'internal_thought': 'Explicar GPU'}))
    assert len(behavior.candidates) == 1
    behavior.select_profile('cohost')
    assert not behavior.policy['script']
    assert 'GPU com 72 GB' not in loop._build_prompt('', '', '', '', '')
    assert behavior.participation_opportunity() is not None


def test_gravacao_combines_script_screen_and_resets_session(behavior):
    behavior.select_profile('gravacao')
    behavior.load_script({
        'id': 'discord',
        'title': 'Discord',
        'beats': [{'id': 'b1', 'text': 'explicar intents e permissões', 'facts': ['intents != permissões']}],
    })
    loop = CognitiveLoop()
    prompt = loop._build_prompt(
        'eu esqueci essa parte',
        '',
        '',
        'Developer Portal mostra Message Content Intent desligado',
        'profile=gravacao state=possible_turn_end',
    )
    assert 'MODO GRAVACAO' in prompt
    assert 'explicar intents e permissões' in prompt
    assert 'Message Content Intent desligado' in prompt
    behavior._human_talk_ms = 1000
    behavior._celia_talk_ms = 9000
    behavior._recent_context.append(('Criador', 'microphone', 'contexto velho'))
    reset = behavior.reset_session(clear_script=True)
    assert reset['talk_ratio'] == {'celia': 0.0, 'human': 0.0}
    assert reset['script']['loaded'] is False
    assert behavior._recent_context == []


def test_gravacao_syncs_loaded_script_to_mobile_teleprompter(behavior, tmp_path):
    from celia.core.roteiro_live import RoteiroLiveRuntime

    behavior.load_script({
        'id': 'discord-tutorial',
        'title': 'Tutorial Discord',
        'start': '01_abertura',
        'beats': [
            {
                'id': '01_abertura',
                'text': 'abrir o tutorial do Discord',
                'title': 'Abertura',
                'lines': [{'speaker': 'Criador', 'text': 'Hoje vamos montar o Discord.'}],
            },
            {
                'id': '02_bot_permissoes',
                'text': 'explicar bot e permissões',
                'title': 'Bot e permissões',
            },
        ],
    })
    runtime = RoteiroLiveRuntime(tmp_path / 'missing.json')

    async def exercise():
        await runtime.observe_message({'type': 'profile.changed', 'profile': 'gravacao'})
        snapshot = runtime.snapshot()
        assert snapshot['active'] is True
        assert snapshot['profile'] == 'gravacao'
        assert snapshot['script']['id'] == 'discord-tutorial'
        assert snapshot['script']['title'] == 'Tutorial Discord'
        assert snapshot['current_beat']['id'] == '01_abertura'

    asyncio.run(exercise())


def test_recording_reset_endpoint_clears_old_script_and_accepts_gravacao(behavior, monkeypatch):
    import celia.bridge_routes as routes
    import celia.core.cognitive_loop as cognitive
    import celia.core.screen_vision as vision
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    behavior.select_profile('cohost')
    behavior.load_script({'id': 'old', 'beats': [{'id': 'x', 'text': 'IA vs Criador antigo'}]})
    behavior._human_talk_ms = 1000
    behavior._celia_talk_ms = 9000
    monkeypatch.setattr(routes, 'continuous_behavior', behavior)

    fake_singleton = SimpleNamespace(broadcast=AsyncMock())
    monkeypatch.setattr(routes.CeliaSingleton, 'get_instance', lambda: fake_singleton)
    reset_cognitive = monkeypatch.setattr(cognitive.cognitive_loop, 'reset_session_context', lambda: None)
    reset_vision = monkeypatch.setattr(vision.screen_vision, 'reset_session', lambda: None)

    app = FastAPI()
    app.include_router(routes.init_bridge_routes(None))
    client = TestClient(app)

    reset = client.post('/bridge/behavior/session/reset', json={'clear_script': True})
    assert reset.status_code == 200
    assert reset.json()['script']['loaded'] is False
    assert reset.json()['talk_ratio'] == {'celia': 0.0, 'human': 0.0}

    profile = client.post('/bridge/behavior/profile', json={'profile': 'gravacao'})
    assert profile.status_code == 200
    assert profile.json()['profile'] == 'gravacao'
    assert profile.json()['policy']['script_tracking'] is True
    assert profile.json()['policy']['screen_awareness'] == 'enabled'


def test_recording_loads_real_beats_and_restores(behavior, tmp_path):
    from celia.core.roteiro_live import RoteiroLiveRuntime
    runtime = RoteiroLiveRuntime()
    behavior.load_script({
        'id': 'stale-cache',
        'version': 1,
        'start': 'old',
        'beats': [{'id': 'old', 'lines': [{'speaker': 'Criador', 'text': 'roteiro antigo'}]}],
    })
    script = runtime.prepare_operational_script(behavior)
    assert script['version'] == 2
    assert script['start'] == '00_abertura_host'
    assert script['beats'][0]['id'] == '00_abertura_host'
    assert len(script['beats']) > 5
    assert script['beats'][0]['lines']
    restored = ContinuousBehaviorRuntime(behavior._state_path)
    assert restored.script.export() == script
    missing = RoteiroLiveRuntime(tmp_path / 'absent.json')
    empty = ContinuousBehaviorRuntime(tmp_path / 'other' / 'profile.json')
    with pytest.raises(ValueError, match='unavailable'):
        missing.prepare_operational_script(empty)
    with pytest.raises(ValueError, match='empty'):
        runtime.prepare_operational_script(behavior, {'beats': []})
    assert behavior.script.beats


@pytest.fixture
def bank(behavior, tmp_path):
    from celia.core.roteador import Decisao
    behavior.select_profile('roteiro')
    behavior.load_script({'id': 'script', 'version': 3, 'beats': [{'id': 'b1', 'text': 'GPU'}]})
    path = tmp_path / 'approved.wav'
    with wave.open(str(path), 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        # Non-silent test signal, confined to tmp_path; never a production voice.
        wav.writeframes(b''.join(struct.pack('<h', int(1000 * math.sin(i * 0.1))) for i in range(4800)))
    ctx = {'contexto': 'roteiro', 'script_id': 'script', 'script_version': 3, 'beat_id': 'b1'}
    asset = {'estado': 'publicado', 'audio_path': str(path), 'texto': 'Resposta preparada.'}
    memory = SimpleNamespace(ativo=lambda _: asset, verificar_audio=lambda _: True)
    decision = Decisao('BANK_HIT', ativo_id='a1', regra={'restricoes': {k:v for k,v in ctx.items() if k != 'contexto'}}, audio_path=str(path), aplicar=True)
    return SimpleNamespace(memoria=memory), decision, ctx, path


def test_bank_rejects_wrong_context_revision_or_integrity(behavior, bank):
    from celia.conversations.single_conversation import eligible_bank_audio
    singleton, decision, ctx, path = bank
    assert eligible_bank_audio(singleton, decision, ctx, behavior.revision)
    assert not eligible_bank_audio(singleton, decision, {**ctx, 'beat_id': 'other'}, behavior.revision)
    assert not eligible_bank_audio(singleton, decision, ctx, behavior.revision - 1)
    assert not eligible_bank_audio(singleton, replace(decision, aplicar=False), ctx, behavior.revision)
    singleton.memoria.verificar_audio = lambda _: False
    assert not eligible_bank_audio(singleton, decision, ctx, behavior.revision)


def test_bank_hit_bypasses_tts_and_preserves_asset_miss_generates(behavior, bank, monkeypatch):
    from celia.conversations.tts_manager import TTSTaskManager
    from celia.agent.output_types import DisplayText
    import celia.conversations.tts_manager as module
    _, decision, _, path = bank
    original = path.read_bytes()
    monkeypatch.setattr(module.training_trace, 'archive_tts_output', lambda *a, **kw: None)
    async def exercise():
        manager = TTSTaskManager()
        engine = SimpleNamespace(async_generate_audio=AsyncMock(return_value=str(path)))
        sent = []
        async def send(payload):
            sent.append(json.loads(payload))
        kwargs = dict(tts_text='Resposta preparada.', display_text=DisplayText('Resposta preparada.'), actions=None, live2d_model=None, tts_engine=engine, websocket_send=send)
        await manager.speak(**kwargs, prepared_audio=str(path), prepared_revision=behavior.revision)
        await asyncio.gather(*manager.task_list)
        await manager.wait_for_payload_delivery()
        engine.async_generate_audio.assert_not_called()
        assert any(item.get('speech_id') for item in sent)
        assert path.read_bytes() == original
        await manager.speak(**kwargs)
        await asyncio.gather(*manager.task_list)
        await manager.wait_for_payload_delivery()
        assert engine.async_generate_audio.await_count == 1
        assert engine.async_generate_audio.call_args.kwargs['fast'] is True
        manager._sender_task.cancel()
        await asyncio.gather(manager._sender_task, return_exceptions=True)
    asyncio.run(exercise())


def test_chat_greeting_skips_tool_llm_and_language_judge(behavior, monkeypatch):
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
    from celia.config_manager.tts_preprocessor import TTSPreprocessorConfig
    from celia.conversations.conversation_utils import create_batch_input
    import celia.agent.agents.basic_memory_agent as module
    class LLM:
        def __init__(self): self.calls = 0
        async def chat_completion(self, *args, **kwargs):
            self.calls += 1
            yield 'Oi, tudo certo.'
    main, tool = LLM(), LLM()
    agent = BasicMemoryAgent(main, 'Celia', None, tool_llm=tool, memory_book_enabled=False, tts_preprocessor_config=TTSPreprocessorConfig(remove_special_char=True, translator_config={"translate_audio": False, "translate_provider": "deeplx"}))
    monkeypatch.setattr(agent, '_read_game_mood', lambda *_a, **_k: '')
    async def exercise():
        return [item async for item in agent.chat(create_batch_input('Oi', images=None, from_name='Criador', metadata={'origin': 'owner'}))]
    output = asyncio.run(exercise())
    assert output and main.calls == 1 and tool.calls == 0


def test_manual_beat_change_invalidates_candidate_and_audio(behavior, tmp_path):
    from celia.core.roteiro_live import RoteiroLiveRuntime
    runtime = RoteiroLiveRuntime(tmp_path / 'absent.json')
    behavior.select_profile('roteiro')
    script = runtime.prepare_operational_script(behavior, {'id': 'session', 'beats': [
        {'id': 'a', 'text': 'First', 'next': ['b']}, {'id': 'b', 'text': 'Second'},
    ]})
    async def exercise():
        await runtime.activate(script=script)
        chunk = behavior.create_tts_chunk('First')
        candidate = behavior.create_candidate(intent_type='completion', semantic_intent='First')
        await runtime.control({'action': 'next'})
        assert behavior.script.position == 1
        assert not behavior.tts_chunk_valid(chunk)
        assert behavior.candidates[candidate.id].status == 'CANCELLED'
    asyncio.run(exercise())


def test_recording_start_endpoint_fails_without_script(behavior, tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import celia.roteiro_routes as routes
    from celia.core.roteiro_live import RoteiroLiveRuntime
    monkeypatch.setattr(routes, 'continuous_behavior', behavior)
    monkeypatch.setattr(routes, 'roteiro_live', RoteiroLiveRuntime(tmp_path / 'missing.json'))
    app = FastAPI()
    app.include_router(routes.init_roteiro_routes())
    response = TestClient(app).post('/bridge/roteiro/start')
    assert response.status_code == 422
    assert response.json()['detail'] == 'recording_script_unavailable'
    assert behavior.profile == 'chat'
    assert not routes.roteiro_live.active


@pytest.mark.parametrize('hit', [True, False])
@pytest.mark.parametrize('tool_command', [False, True])
def test_full_conversation_bank_hit_or_miss(behavior, bank, monkeypatch, hit, tool_command):
    import celia.conversations.single_conversation as module
    from celia.celia_singleton import CeliaSingleton
    from celia.agent.output_types import SentenceOutput, DisplayText, Actions
    singleton, decision, ctx, path = bank
    singleton.observar_banco = lambda *a, **kw: decision if hit else replace(decision, resultado='BANK_MISS', aplicar=False)
    singleton.registrar_turno_operacional = lambda **kw: None
    monkeypatch.setattr(CeliaSingleton, 'get_instance', lambda: singleton)
    class Agent:
        calls = 0
        async def chat(self, batch):
            self.calls += 1
            yield SentenceOutput(DisplayText('Nova resposta.'), 'Nova resposta.', Actions())
    agent = Agent()
    engine = SimpleNamespace(async_generate_audio=AsyncMock(return_value=str(path)))
    context = SimpleNamespace(
        system_config=SimpleNamespace(audio_output_device=None),
        character_config=SimpleNamespace(human_name='Criador', character_name='Celia', avatar='', conf_uid='test', agent_config=SimpleNamespace(llm_provider='fake')),
        history_uid=None, live2d_model=None, asr_engine=None, translate_engine=None,
        agent_engine=agent, tts_engine=engine,
    )
    sent = []
    async def send(payload): sent.append(json.loads(payload))
    async def finalize(tts_manager, **kwargs):
        await asyncio.gather(*tts_manager.task_list)
        await tts_manager.wait_for_payload_delivery()
    monkeypatch.setattr(module, 'finalize_conversation_turn', finalize)
    metadata = {'origin': 'owner'}
    if tool_command:
        metadata['scripted_reply'] = {'line_id': 'b1:1', 'beat_id': 'b1', 'text': 'Pending script line.'}
    result = asyncio.run(module.process_single_conversation(
        context, send, 'test', 'Célia, Open Browser.' if tool_command else 'GPU', metadata=metadata,
    ))
    effective_hit = hit and not tool_command
    assert result == ('Resposta preparada.' if effective_hit else 'Nova resposta.')
    assert agent.calls == (0 if effective_hit else 1)
    assert engine.async_generate_audio.await_count == (0 if effective_hit else 1)
    assert any(item.get('speech_id') and not item.get('tts_error') for item in sent)


def test_scripted_roteiro_reply_on_bank_miss_uses_exact_line_without_llm(behavior, bank, monkeypatch):
    import celia.conversations.single_conversation as module
    from celia.celia_singleton import CeliaSingleton

    singleton, decision, _, path = bank
    singleton.observar_banco = lambda *a, **kw: replace(
        decision, resultado='BANK_MISS', aplicar=False, audio_path=None
    )
    singleton.registrar_turno_operacional = lambda **kw: None
    monkeypatch.setattr(CeliaSingleton, 'get_instance', lambda: singleton)

    class Agent:
        calls = 0
        async def chat(self, batch):
            self.calls += 1
            raise AssertionError('scripted recording line must not call the LLM')
            yield

    agent = Agent()
    engine = SimpleNamespace(async_generate_audio=AsyncMock(return_value=str(path)))
    context = SimpleNamespace(
        system_config=SimpleNamespace(audio_output_device=None),
        character_config=SimpleNamespace(
            human_name='Criador', character_name='Celia', avatar='', conf_uid='test',
            agent_config=SimpleNamespace(llm_provider='fake'),
        ),
        history_uid=None, live2d_model=None, asr_engine=None, translate_engine=None,
        agent_engine=agent, tts_engine=engine,
    )
    sent = []
    async def send(payload):
        sent.append(json.loads(payload))
    async def finalize(tts_manager, **kwargs):
        await asyncio.gather(*tts_manager.task_list)
        await tts_manager.wait_for_payload_delivery()
    monkeypatch.setattr(module, 'finalize_conversation_turn', finalize)

    result = asyncio.run(module.process_single_conversation(
        context, send, 'test', 'Fala, pessoal.',
        metadata={
            'origin': 'microphone',
            'scripted_reply': {
                'beat_id': 'b1', 'line_id': 'b1:1', 'text': 'Finalmente.'
            },
        },
    ))
    assert result == 'Finalmente.'
    assert agent.calls == 0
    assert engine.async_generate_audio.await_count == 1
    assert not any(item.get('type') == 'celia.text.approved' for item in sent)
    assert any(item.get('speech_id') for item in sent)


@pytest.mark.parametrize('profile', ['chat', 'roteiro', 'gravacao', 'cohost', 'autonomous'])
def test_debug_reports_actual_resources(behavior, monkeypatch, profile):
    import celia.bridge_routes as routes
    import celia.core.cognitive_loop as cognitive
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    behavior.select_profile(profile)
    monkeypatch.setattr(routes, 'continuous_behavior', behavior)
    singleton = SimpleNamespace(context=SimpleNamespace(agent_engine=SimpleNamespace(_use_mcpp=True, _tool_executor=object(), _tool_manager=object())), is_processing=False, bank_mode='ativo' if profile == 'roteiro' else 'shadow')
    monkeypatch.setattr(routes.CeliaSingleton, 'get_instance', lambda: singleton)
    monkeypatch.setattr(cognitive.cognitive_loop, 'debug_snapshot', lambda: {'running': profile != 'chat'})
    app = FastAPI()
    app.include_router(routes.init_bridge_routes(None))
    view = TestClient(app).get('/bridge/debug/state').json()
    assert set(view['profiles']) == PROFILE_NAMES
    assert view['resources'] == {'brain':'off' if profile == 'chat' else 'on', 'script':'on' if profile in {'roteiro', 'gravacao'} else 'off', 'tools':'on', 'bank':'active' if profile == 'roteiro' else 'shadow'}


@pytest.mark.parametrize('profile', ['chat', 'roteiro', 'gravacao', 'cohost', 'autonomous'])
def test_owner_effect_tool_executes_but_destructive_is_denied(behavior, profile):
    from test_guardian_ingress_and_mcp_auth import _McpClient, _ToolManager, _updates
    from celia.mcpp.tool_executor import ToolExecutor
    from celia.core.ingress_security import new_context
    behavior.select_profile(profile)
    owner = new_context('local_owner', external=False, authenticated=True, authorization_scope=('memory.write',))
    async def exercise():
        client = _McpClient()
        executor = ToolExecutor(client, _ToolManager('salvar_memoria'))
        updates = await _updates(executor, {'id':'write', 'name':'salvar_memoria', 'input':{}}, owner)
        assert any(item.get('status') == 'completed' for item in updates)
        assert len(client.calls) == 1
        denied = _McpClient()
        executor = ToolExecutor(denied, _ToolManager('execute_command'))
        updates = await _updates(executor, {'id':'destructive', 'name':'execute_command', 'input':{'command':'echo test'}}, owner)
        assert any(item.get('status') == 'denied' for item in updates)
        assert not denied.calls
    asyncio.run(exercise())


def test_final_turn_does_not_refresh_stale_cognitive_candidate(behavior):
    behavior.select_profile('cohost')
    candidate = behavior.create_candidate(intent_type='cognitive_contribution', semantic_intent='Old topic')
    behavior.observe('speech.final', source='microphone', text='New unrelated topic')
    assert behavior.candidates[candidate.id].status == 'CANCELLED'


def test_provider_diagnostic_is_identified_before_display_or_tts():
    from celia.conversations.single_conversation import is_provider_failure_output

    assert is_provider_failure_output(
        "Error calling the chat endpoint: Rate limit exceeded.",
        "Error calling the chat endpoint: Rate limit exceeded.",
    )
    assert not is_provider_failure_output("Finalmente.", "Finalmente.")


