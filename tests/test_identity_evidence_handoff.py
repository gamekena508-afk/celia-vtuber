"""Owner mission: semantic candidates and dated visual evidence, never string replacement."""
import asyncio
import time
from unittest.mock import AsyncMock

import pytest
from celia.core import jev
from celia.core.asr_normalization import _normalize_celia_vocative
from celia.core.visual_identity import identity_candidates
from celia.core.visual_context import EVIDENCE_RULES, sensor_context
from celia.core.screen_vision import ScreenVisionAnalyzer
from celia.core.vision_cache import vision_cache


@pytest.mark.parametrize('description,expected', [
    ('Um gato azul arredondado', ['criador_slime']),
    ('Slime azul', ['criador_slime']),
    ('VTuber de cabelo azul e óculos', ['celia']),
    ('Célia ao lado de um slime azul', ['criador_slime', 'celia']),
    ('Editor sem personagens', []),
])
def test_semantic_hints_select_comparisons_only(description, expected):
    assert identity_candidates({'summary': description}) == expected


def test_ocr_and_uncertain_entities_cannot_select_identity():
    assert identity_candidates({'summary':'Editor', 'ocr_lines':['Célia slime azul'],
        'entities':[{'name':'Célia','source':'ocr'}, {'name':'slime azul','uncertain':True}]}) == []


def test_visual_entity_features_can_select_reference_without_a_name():
    assert identity_candidates({'summary': 'Personagem de anime', 'entities': [
        {'name': 'personagem', 'source': 'visual', 'evidence': ['cabelo azul longo']}]
    }) == ['celia']


@pytest.mark.parametrize('text', ['Essa situação é séria.', 'The seller has a blue cat.',
    'O Sérgio é meu amigo.', 'Estou vendo uma série.'])
def test_phonetic_dictionary_preserves_ordinary_words(text):
    assert _normalize_celia_vocative(text)[0] == text


@pytest.mark.parametrize('text,expected', [('Sélia, olha a tela.','Célia, olha a tela.'),
    ('Séria, está vendo?','Célia, está vendo?'), ('Seller, olha isso.','Célia, olha isso.'),
    ('O avatar da Sélia é azul.','O avatar da Célia é azul.')])
def test_phonetic_context_is_separate_from_identity(text, expected):
    assert _normalize_celia_vocative(text)[0] == expected


def test_tool_text_cannot_rejuvenate_or_create_observation():
    vision_cache.clear()
    jev.observe_tool_result('vision_extract_text','NVIDIA error Minecraft')
    assert vision_cache.get() == (None,None)
    note=vision_cache.put('screen','old scene',analyzed_at=time.monotonic()-80)
    jev.observe_tool_result('vision_describe_visual','old scene')
    assert vision_cache.get()[0] is note
    assert vision_cache.get()[1] >= 80_000
    vision_cache.clear()


def test_sensor_provenance_and_provider_separation():
    note=sensor_context('O que tem na tela?',{'summary':'editor','frame_id':42,'generation':8,'analyzed_at':123},1)
    assert '42' in note and '123' in note
    assert 'OCR is text' in EVIDENCE_RULES and 'actual failed request' in EVIDENCE_RULES
    assert 'Minecraft text does not prove' in EVIDENCE_RULES
    assert 'old scene' not in sensor_context('O que tem na tela?',{'summary':'old scene'},80)


@pytest.mark.parametrize('result,age', [({}, None), ({'summary': 'old scene'}, 80)])
def test_missing_frame_does_not_diagnose_service_outage(result, age):
    import json
    note = sensor_context('O que tem na tela?', result, age)
    data = json.loads(note.split('[VISUAL SENSOR] ', 1)[1])
    assert data['reason'] == 'no_recent_analysis'
    assert data['service_status'] == 'unknown'
    assert 'Missing or expired frame' in EVIDENCE_RULES


def test_candidates_preserve_original_description_and_use_verifier():
    analyzer=ScreenVisionAnalyzer();analyzer.scene.state.last_frame_id=7;analyzer._last_result_ts=time.monotonic()
    analyzer.verify_identity=AsyncMock(return_value={'identity':'criador_slime','confirmed':True,'relation':'creator_avatar'})
    data={'ok':True,'source':'screen','frame_id':7,'summary':'gato azul'}
    out=asyncio.run(analyzer.identify_scene_candidates(data,time.monotonic()+2))
    assert out['original_description']=='gato azul' and out['summary']=='gato azul'
    assert out['identities'][0]['confirmed']
    analyzer.verify_identity.assert_awaited_once_with('criador_slime','screen')


def test_scene_change_during_comparison_invalidates_result():
    analyzer=ScreenVisionAnalyzer();analyzer.scene.state.last_frame_id=8
    analyzer.verify_identity=AsyncMock(return_value={'confirmed':False})
    out=asyncio.run(analyzer.identify_scene_candidates({'source':'screen','frame_id':7,'summary':'gato azul'},time.monotonic()+2))
    assert not out['ok']


def test_unconfirmed_identity_does_not_prime_names_or_relationships():
    from celia.core.visual_context import identity_context
    raw={'identity':'criador_slime','relation':'creator_avatar','confirmed':False,
         'evidence':['matching ears'],'shape_check':{'blob_present':False}}
    context=identity_context(raw)
    assert 'criador_slime' not in context and 'creator_avatar' not in context
    assert 'matching ears' not in context and 'unconfirmed_reference_match' in context
    raw['confirmed']=True
    assert 'creator_avatar' in identity_context(raw)


def test_self_avatar_handoff_disambiguates_observer_from_creator():
    from celia.core.visual_context import identity_context
    note = identity_context({'confirmed': True, 'identity': 'celia', 'relation': 'self_avatar'})
    assert 'a própria assistente' in note and 'não Criador' in note
    assert 'hypothesis, not proof' in EVIDENCE_RULES


def test_current_visual_turn_does_not_prime_old_answer_but_keeps_memory_requests():
    from celia.core.visual_context import current_visual_messages
    messages=[{'role':'user','content':'Eu trabalhava no Minecraft.'},
              {'role':'assistant','content':'Vejo um jogo aberto.'}]
    out=current_visual_messages(messages,'O que tem na tela agora?')
    assert len(out)==1 and len(messages)==2
    assert current_visual_messages(messages,'Lembra do avatar de ontem?')==messages
    assert current_visual_messages(messages,'Explica threads.')==messages


def test_current_game_presence_question_requires_visual_evidence():
    from celia.core.visual_context import visual_relevant,visual_user_evidence
    text='Tem Minecraft rodando agora ou só texto sobre ele?'
    assert visual_relevant(text)
    note=visual_user_evidence(text,{'summary':'Editor sem jogo visível.','frame_id':8},1)
    assert 'Editor sem jogo' in note and 'frame_id' in note


def test_old_visual_user_presuppositions_do_not_override_new_subject():
    from celia.core.visual_context import current_visual_messages
    messages = [{'role': 'user', 'content': 'Agora confirmou o meu Slime.'},
                {'role': 'assistant', 'content': 'Representa Criador.'}]
    assert current_visual_messages(messages, 'Reconhece o seu avatar?') == []
    assert current_visual_messages(messages, 'Lembra do meu Slime?') == messages


def test_masked_second_pass_uses_non_disposable_queue(monkeypatch):
    from types import SimpleNamespace
    from celia.core import screen_vision as module
    analyzer = ScreenVisionAnalyzer()
    monkeypatch.setattr(module, 'mask_text', lambda *args: 'masked')
    monkeypatch.setattr(module.vscene, 'blank_content', lambda image: False)
    monkeypatch.setattr(module.local_vision_client, 'request', AsyncMock(return_value=SimpleNamespace(raw='{}')))
    monkeypatch.setattr(analyzer, '_parse', lambda raw: {'summary': 'visual scene'})
    kinds = []

    async def run_job(kind, fn, **kwargs):
        kinds.append(kind)
        return await fn()

    monkeypatch.setattr(module.gpu_scheduler, 'run_job', run_job)
    result = asyncio.run(analyzer._visual_only_pass('frame', 9, ['Célia'], {}))
    assert kinds == ['vision_request'] and result['summary'] == 'visual scene'


def test_forced_identity_filter_preserves_actual_current_question():
    from celia.core.visual_context import current_visual_messages
    current = {'role': 'user', 'content': 'Você se reconhece nesse avatar na tela?'}
    previous = {'role': 'user', 'content': 'Meu Slime é azul.'}
    assert current_visual_messages([previous, current], current['content'], preserve_current=True) == [current]


@pytest.mark.parametrize('kind,ok', [('static', True), ('major', False)])
def test_new_frame_number_requires_actual_visual_change_to_invalidate(monkeypatch, kind, ok):
    from types import SimpleNamespace
    from celia.core import screen_vision as module
    analyzer = ScreenVisionAnalyzer()
    analyzer._last_result = {'frame_id': 7}
    analyzer._last_result_signature = object()
    analyzer._last_frame_by_source['screen'] = 'current frame'
    analyzer.scene.state.last_frame_id = 8
    analyzer._last_result_ts = time.monotonic()
    analyzer.verify_identity = AsyncMock(return_value={'confirmed': False})
    monkeypatch.setattr(module.vscene, 'signature_of', lambda image: object())
    monkeypatch.setattr(module.vscene, 'compare', lambda a, b: SimpleNamespace(kind=kind))
    data = {'ok': True, 'source': 'screen', 'frame_id': 7, 'summary': 'slime azul'}
    out = asyncio.run(analyzer.identify_scene_candidates(data, time.monotonic()+2))
    assert out['ok'] is ok


def test_new_analysis_during_identity_cannot_refresh_original_evidence_age():
    analyzer = ScreenVisionAnalyzer()
    analyzer.scene.state.last_frame_id = 7
    analyzer._last_result_ts = time.monotonic() - 16

    async def compare(*args):
        analyzer._last_result_ts = time.monotonic()
        return {'confirmed': False}

    analyzer.verify_identity = compare
    out = asyncio.run(analyzer.identify_scene_candidates(
        {'source': 'screen', 'frame_id': 7, 'summary': 'slime azul'}, time.monotonic()+2))
    assert out['error'] == 'scene_expired_during_identity'


def test_actual_reference_result_replaces_pretool_missing_frame_instruction():
    from celia.core.visual_context import identity_user_evidence, visual_user_evidence
    text = 'Você se reconhece nesse avatar na tela?'
    current = '=== FALA DO USUÁRIO ===\nVisitante: ' + text + visual_user_evidence(text, {}, None)
    updated = identity_user_evidence(current, text,
        {'identity': 'celia', 'relation': 'self_avatar', 'confirmed': True})
    assert text in updated and 'Visitante:' in updated
    assert 'no_recent_analysis' not in updated and 'status": "unavailable' not in updated
    assert 'confirmed_reference_match' in updated and 'self_avatar' in updated


def test_identity_enrichment_is_bounded_by_original_observation_freshness(monkeypatch):
    from celia.core import screen_vision as module
    analyzer = ScreenVisionAnalyzer()
    analyzer.scene.state.last_frame_id = 7
    analyzer._last_result_ts = time.monotonic() - 14
    analyzer.verify_identity = AsyncMock()
    timeouts = []

    async def wait_for(awaitable, timeout):
        awaitable.close()
        timeouts.append(timeout)
        raise asyncio.TimeoutError

    monkeypatch.setattr(module.asyncio, 'wait_for', wait_for)
    out = asyncio.run(analyzer.identify_scene_candidates(
        {'ok': True, 'source': 'screen', 'frame_id': 7, 'summary': 'slime azul'}, time.monotonic()+20))
    assert 0 < timeouts[0] < 1
    assert out['ok'] is True and out['identity_status'] == 'deadline'
    assert out['identities'] == []
