import asyncio
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock
import pytest
from celia.core.live_comment_readback import read_live_comment

def request(**kw):
    return NS(**dict(read_aloud_input=True, fonte='tiktok_live_chat',
        autor='Maria Nome Completo',text='mensagem completa '*100,message_id='room:message',**kw))

def test_full_author_message_existing_tts_and_ack():
    r=request()
    speak=AsyncMock(return_value=NS(ok=True,timed_out=False,clients=1,playback_status='ended'))
    asyncio.run(read_live_comment(r,speak,NS))
    payload=speak.call_args.args[0]
    assert payload.text == f'{r.autor} disse: {r.text}'
    assert payload.wait_for_playback and not payload.record_context
    assert payload.speech_event_key == 'tiktok:room:message'

@pytest.mark.parametrize('status,clients,timeout',[('timeout',1,True),('local_duration_elapsed',0,False),('pending',1,False)])
def test_no_reply_until_actual_playback(status,clients,timeout):
    speak=AsyncMock(return_value=NS(ok=True,timed_out=timeout,clients=clients,playback_status=status))
    with pytest.raises(RuntimeError,match='not_confirmed'):
        asyncio.run(read_live_comment(request(),speak,NS))

def test_other_sources_cannot_enable_readback():
    r=request();r.fonte='owner'
    speak=AsyncMock()
    with pytest.raises(ValueError):asyncio.run(read_live_comment(r,speak,NS))
    speak.assert_not_called()

def test_disabled_is_noop():
    r=request();r.read_aloud_input=False
    speak=AsyncMock()
    asyncio.run(read_live_comment(r,speak,NS));speak.assert_not_called()
