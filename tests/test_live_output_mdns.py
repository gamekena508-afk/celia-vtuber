from unittest.mock import AsyncMock, patch
import pytest
from celia.live_output_routes import resolve_mdns_candidate


def candidate(host):
    return {'type':'signal', 'data':{'type':'candidate', 'candidate':{
        'candidate':f'candidate:1 1 udp 2113937151 {host} 36901 typ host',
        'sdpMid':'0', 'sdpMLineIndex':0}}}

@pytest.mark.asyncio
async def test_mdns_resolves_without_mutating_original():
    host='42695a19-dd00-4b4b-8995-eebd282b6aee.local'
    proc=AsyncMock(); proc.returncode=0
    proc.communicate.return_value=(f'{host}\t192.168.0.104\n'.encode(),b'')
    message=candidate(host)
    with patch('asyncio.create_subprocess_exec',AsyncMock(return_value=proc)) as launch:
        result=await resolve_mdns_candidate(message)
    assert '192.168.0.104 36901 typ host' in result['data']['candidate']['candidate']
    assert host in message['data']['candidate']['candidate']
    assert result['data']['candidate']['sdpMid']=='0'
    assert launch.call_args.args==('/usr/bin/avahi-resolve-host-name','-4',host)

@pytest.mark.asyncio
@pytest.mark.parametrize('host',['192.168.0.104','evil.example','--help.local','$(id).local'])
async def test_arbitrary_names_never_resolved(host):
    message=candidate(host)
    with patch('asyncio.create_subprocess_exec',AsyncMock()) as launch:
        assert await resolve_mdns_candidate(message) is message
        launch.assert_not_called()

@pytest.mark.asyncio
async def test_lookup_failure_keeps_original():
    message=candidate('42695a19-dd00-4b4b-8995-eebd282b6aee.local')
    with patch('asyncio.create_subprocess_exec',AsyncMock(side_effect=OSError)):
        assert await resolve_mdns_candidate(message) is message
