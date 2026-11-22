"""Production vision fails closed and never turns provider errors into scene text."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from celia.core.vision_policy import vision_policy, VisionUnavailable, MODEL, DEFAULT_URL
from celia.core.local_vision import LocalVisionClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'mcp'))
import vision_mcp_server as vm


@pytest.fixture(autouse=True)
def local_config(monkeypatch):
    for key in ('CELIA_VISION_BACKEND', 'CELIA_LOCAL_VISION_URL', 'CELIA_LOCAL_VISION_MODEL',
                'CELIA_LOCAL_VISION_ENABLED', 'CELIA_LOCAL_VISION_TIMEOUT',
                'CELIA_LOCAL_VISION_TIMEOUT_SEC', 'CELIA_VISION_TIMEOUT',
                'NVIDIA_VISION_API_KEY', 'NVIDIA_VISION_MODEL'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(vm, 'encode_image', lambda image: 'AA==')


@pytest.mark.parametrize('failure,code', [('http','http_error'), ('timeout','timeout'),
    ('empty','empty_response'), ('invalid','invalid_response'), ('model','unexpected_model')])
def test_mcp_failure_calls_only_local_once(monkeypatch, failure, code):
    calls=[]
    def post(url, **kwargs):
        calls.append((url,kwargs))
        assert url == DEFAULT_URL
        assert 'headers' not in kwargs, 'vision must not use provider credentials'
        assert kwargs['allow_redirects'] is False
        assert kwargs['timeout'] == 7.5
        if failure=='timeout': raise vm.requests.Timeout('private provider diagnostic')
        data={'model':MODEL,'choices':[{'message':{'content':' ' if failure=='empty' else 'valid'}}]}
        if failure=='model': data['model']='wrong-model'
        if failure=='invalid': data=[]
        return SimpleNamespace(status_code=503 if failure=='http' else 200,json=lambda:data)
    monkeypatch.setattr(vm.requests,'post',post)
    with pytest.raises(VisionUnavailable) as err:
        vm.analyze_with_custom_prompt(Image.new('RGB',(8,8)), 'describe')
    assert err.value.code==code and len(calls)==1
    payload=err.value.as_dict()
    assert payload['description'] is None and payload['ok'] is False
    assert 'NVIDIA' not in str(err.value) and 'private' not in str(err.value)


@pytest.mark.parametrize('tool', ['vision_understand_context','vision_list_options','vision_describe_visual','vision_custom_analysis'])
async def test_tool_returns_structured_error_not_scene(monkeypatch,tool):
    monkeypatch.setattr(vm,'capture_screen',lambda: Image.new('RGB',(8,8)))
    monkeypatch.setattr(vm,'_encontrar_pessoa_na_tela',lambda image:None)
    monkeypatch.setattr(vm,'_analise_da_cena_pelo_servidor',lambda:None)
    monkeypatch.setattr(vm,'detect_dominant_color_v15',lambda image:'Blue')
    def fail(*a,**kw): raise VisionUnavailable('timeout')
    monkeypatch.setattr(vm,'analyze_with_custom_prompt',fail)
    result=await vm.handle_call_tool(tool,{'prompt':'describe'})
    assert result.isError
    data=json.loads(result.content[0].text)
    assert data['ok'] is False and data['description'] is None and data['error']['code']=='timeout'


@pytest.mark.parametrize('key,value', [('CELIA_VISION_BACKEND','remote'),
    ('CELIA_LOCAL_VISION_URL','https://integrate.api.nvidia.com/v1/chat/completions'),
    ('CELIA_LOCAL_VISION_MODEL','old-model'),('CELIA_LOCAL_VISION_ENABLED','0')])
def test_invalid_policy_never_sends_image(monkeypatch,key,value):
    monkeypatch.setenv(key,value)
    monkeypatch.setattr(vm.requests,'post',lambda *a,**k: pytest.fail('must fail before HTTP'))
    with pytest.raises(VisionUnavailable): vm.analyze_with_custom_prompt(None,'test')


def test_canonical_timeout_and_legacy_aliases(monkeypatch):
    assert vision_policy().url==DEFAULT_URL and vision_policy().model==MODEL
    monkeypatch.setenv('CELIA_LOCAL_VISION_TIMEOUT_SEC','8')
    assert LocalVisionClient().timeout==8
    monkeypatch.setenv('CELIA_LOCAL_VISION_TIMEOUT','6')
    assert vision_policy().timeout==LocalVisionClient().timeout==6


async def test_mcp_success_without_nvidia_credentials_and_schema_compatibility(monkeypatch):
    monkeypatch.setattr(vm.requests,'post',lambda url,**kw: SimpleNamespace(status_code=200,json=lambda:{'model':MODEL,'choices':[{'message':{'content':'Uma interface azul.'}}]}))
    assert vm.analyze_with_custom_prompt(None,'test')[0]=='Uma interface azul.'
    tools=await vm.handle_list_tools()
    assert {t.name for t in tools}>={'vision_extract_text','vision_understand_context','vision_list_options',
        'vision_describe_visual','vision_custom_analysis','vision_reconhecer_se_mesma','registrar_pessoa_na_tela'}


async def test_startup_logs_announce_actual_policy(monkeypatch,capsys):
    class Stop(RuntimeError):pass
    def stop():raise Stop
    monkeypatch.setattr(vm,'stdio_server',stop)
    with pytest.raises(Stop): await vm.main()
    log=capsys.readouterr().err
    assert MODEL in log and DEFAULT_URL in log and 'local-only' in log and 'NVIDIA API' not in log


@pytest.mark.parametrize('failure', ['http','timeout','empty','connection'])
async def test_async_client_failures_are_structured_and_local(monkeypatch,failure):
    calls=[]
    def handler(request):
        calls.append(str(request.url)); assert str(request.url)==DEFAULT_URL
        if failure=='timeout':raise httpx.ReadTimeout('timeout',request=request)
        if failure=='connection':raise httpx.ConnectError('local service refused',request=request)
        return httpx.Response(404 if failure=='http' else 200,json={'choices':[{'message':{'content':''}}]})
    client=LocalVisionClient();client._http=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(VisionUnavailable) as exc:await client.request(prompt='p',image='data:image/jpeg;base64,AA==',max_tokens=5)
        assert exc.value.as_dict()['description'] is None and len(calls)==1
        if failure == 'connection':
            assert exc.value.code == 'connection_error'
    finally:await client._http.aclose()
