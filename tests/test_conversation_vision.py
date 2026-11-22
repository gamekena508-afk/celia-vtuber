"""Regression contracts for sensor isolation, actual MCP wire errors and identities."""
import asyncio
import json
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from celia.core.visual_context import human_text, sensor_context, visual_relevant
from celia.core.visual_identity import verdict, VisualIdentity
from celia.core.vision_policy import VisionUnavailable


@pytest.mark.parametrize('text', ['O que que tá vendo, Célia?', 'Tá vendo aí?', 'Descreva essa tela'])
def test_explicit_spoken_request_can_refresh(text):
    from celia.core.visual_context import asks_current_visual
    assert asks_current_visual(text)


@pytest.mark.parametrize('text', ['Eu tava vendo um filme ontem.', 'Olha, eu prefiro Python.',
    'Explica thread e processo.', 'A visão falhou, mas continua a explicação.'])
def test_common_conversation_does_not_force_vision(text):
    from celia.core.visual_context import asks_current_visual
    assert not asks_current_visual(text)


def test_spoken_visual_request_keeps_actual_vision_tools():
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent
    assert BasicMemoryAgent._is_visual_request('O que que tá vendo, Célia?')
    agent = BasicMemoryAgent.__new__(BasicMemoryAgent)
    call = agent._forced_system_tool_call('Você se reconhece na tela?')
    assert call['name'] == 'vision_identify_character' and call['input'] == {'identity': 'celia'}
    call = agent._forced_system_tool_call('Reconhece esse slime?')
    assert call['input'] == {'identity': 'criador_slime'}


def test_visual_identity_tools_keep_public_denial():
    from celia.core.tool_authorization import authorize_tool
    from celia.core.ingress_security import new_context
    public = new_context('youtube_live_chat', external=True)
    for tool in ['vision_identify_character', 'vision_reconhecer_se_mesma']:
        assert not authorize_tool(tool, {}, public).allowed


def test_large_scene_change_invalidates_cached_sensor(monkeypatch):
    import base64, io
    from PIL import Image
    from celia.core.screen_vision import ScreenVisionAnalyzer
    from celia.core.vision_cache import vision_cache
    from celia.core import visual_scene
    analyzer = ScreenVisionAnalyzer()
    def frame(color):
        buf = io.BytesIO()
        Image.new('RGB', (320, 240), color).save(buf, format='JPEG')
        return 'data:image/jpeg;base64,' + base64.b64encode(buf.getvalue()).decode()
    analyzer.remember_frame(frame('black'), 'screen')
    analyzer._last_result_source = 'screen'
    analyzer._last_result = {'summary': 'OLD WINDOW'}
    analyzer._last_result_ts = 1
    analyzer.scene.state.summary = 'OLD WINDOW'
    analyzer.scene.state.source = 'screen'
    vision_cache.put('old', 'OLD WINDOW')
    analyzer.remember_frame(frame('white'), 'screen')
    assert analyzer.last_analysis() == ({}, None)
    assert vision_cache.get()[0] is None
    assert analyzer.scene.state.last_frame_id == analyzer._frame_seq


@pytest.mark.parametrize('text', ['Oi Célia, tudo bem?', 'Explica async e await.',
    'Você vai perder essa competição, hein!', 'Agora vamos falar de música.', 'Conta uma piada.'])
def test_irrelevant_screen_does_not_change_conversation(text):
    scene = {'summary': 'Terminal com computador NVIDIA erro pesquisar código tela'}
    assert sensor_context(text, scene, 1) == sensor_context(text, {}, None) == ''


@pytest.mark.parametrize('age', [16, 51, 59, 90, None])
def test_expired_analysis_never_repeats_scene(age):
    note = sensor_context('O que está na tela?', {'summary': 'OLD WINDOW'}, age)
    assert 'OLD WINDOW' not in note and 'unavailable' in note


def test_legacy_prefix_is_not_user_intent():
    raw = '[VISAO - ultima analise da tela, ha 59s: pesquisar computador NVIDIA]\nO código é dela?'
    assert human_text(raw) == 'O código é dela?'
    assert not visual_relevant(raw)


def test_fresh_sensor_bounded_and_identity_separate():
    note = sensor_context('Reconhece este avatar?', {'summary': 'x' * 1000,
        'identities': [{'identity': 'criador_slime', 'relation': 'creator_avatar', 'confidence': 'high', 'confirmed': True},
                       {'identity': 'celia', 'confirmed': False}]}, 2)
    assert len(note) < 700 and 'creator_avatar' in note and '"identity": "celia"' not in note


@pytest.mark.parametrize('identity', ['celia', 'criador_slime', 'criador_human'])
@pytest.mark.parametrize('real,control,confirmed', [('yes', 'no', True), ('yes', 'yes', False),
    ('yes', 'unsure', False), ('unsure', 'no', False), ('no', 'no', False)])
def test_identity_requires_discrimination(identity, real, control, confirmed):
    result = verdict(identity, {'present': real, 'confidence': .95, 'evidence': ['distinctive glasses and accessory']},
                     {'present': control}, shape={'blob_present': True, 'confidence': .95})
    assert result['confirmed'] == confirmed


def test_identity_low_confidence_or_no_evidence_fails():
    for score, evidence in [(.6, ['blue']), (.99, [])]:
        assert not verdict('celia', {'present': 'yes', 'confidence': score, 'evidence': evidence},
                           {'present': 'no'})['confirmed']


@pytest.mark.parametrize('shape', [None, {'blob_present': False, 'confidence': .99},
                                  {'blob_present': True, 'confidence': .5}])
def test_blue_catgirl_cannot_be_confirmed_as_slime(shape):
    assert not verdict('criador_slime',
        {'present': 'yes', 'confidence': .99, 'evidence': ['blue cat with matching blue tips']},
        {'present': 'no'}, shape=shape)['confirmed']


async def test_blurred_identity_does_not_call_model(monkeypatch):
    from celia.core import visual_identity as module
    from types import SimpleNamespace
    monkeypatch.setattr(module.scene, 'image_quality', lambda _: SimpleNamespace(blurry=True))
    result = await VisualIdentity().verify('blurred', 'celia')
    assert not result['confirmed'] and result['reason'] == 'insufficient_image_quality'


def test_sensor_updates_do_not_repeat_brain_wakes(monkeypatch):
    from celia.core.screen_vision import ScreenVisionAnalyzer
    from celia.core.cognitive_loop import cognitive_loop
    calls = []
    monkeypatch.setattr(cognitive_loop, 'set_screen_context', lambda _: None)
    monkeypatch.setattr(cognitive_loop, 'request_think', calls.append)
    analyzer = ScreenVisionAnalyzer()
    for text, reason in [('scene', 'vision_screen_change'), ('scene', 'vision_screen_change'),
                         ('new OCR', 'vision_text_change')]:
        analyzer._deliver(text, reason=reason, run={})
    assert calls == ['vision_screen_change']


def test_analysis_freshness_not_rejuvenated_by_cache_touch(monkeypatch):
    from celia.core.vision_cache import VisionCache, VisionSelection, format_vision_context
    from celia.core import vision_cache as module
    now = [10.]
    monkeypatch.setattr(module.time, 'monotonic', lambda: now[0])
    cache = VisionCache()
    cache.put('old window', 'OLD CONTENT')
    now[0] = 69.
    cache.touch()
    note, age, verified = cache.get_ages()
    assert age == 59000 and verified == 0
    assert 'OLD CONTENT' not in format_vision_context(VisionSelection(note, age, 'cache', 'cache', verified))


async def test_mcp_error_survives_installed_sdk_and_mcpc(tmp_path):
    # A separate stdio process exercises the installed SDK, not just the Python handler.
    root = Path(__file__).resolve().parents[1]
    code = '''import sys,asyncio
sys.path.insert(0, 'mcp')
import vision_mcp_server as v
async def fail(): raise v.VisionUnavailable('timeout')
v.vision_understand_context=fail
asyncio.run(v.main())
'''
    params = StdioServerParameters(command=sys.executable, args=['-c', code], cwd=str(root))
    with (tmp_path/'wire.log').open('w') as log:
        async with stdio_client(params, errlog=log) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool('vision_understand_context', {})
                assert result.isError
                payload = json.loads(result.content[0].text)
                assert payload['error']['code'] == 'timeout'
                assert payload['description'] is None
                # The production consumer must keep the error channel distinct.
                from celia.mcpp.mcp_client import MCPClient
                from celia.mcpp.server_registry import ServerRegistry
                client = MCPClient(ServerRegistry())
                async def connected(_): return session
                monkey = pytest.MonkeyPatch()
                monkey.setattr(client, '_ensure_server_running_and_get_session', connected)
                try:
                    consumed = await client.call_tool('vision-analysis', 'vision_understand_context', {})
                    assert consumed['content_items'][0]['type'] == 'error'
                    assert 'validation errors' not in consumed['content_items'][0]['text']
                finally:
                    monkey.undo()
