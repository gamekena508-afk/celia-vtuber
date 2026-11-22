import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from celia.live_output_routes import init_live_output_routes
from celia.mobile_routes import init_mobile_routes, mobile_hub

@pytest.fixture
def client():
    mobile_hub.publisher = None
    mobile_hub.viewers.clear()
    app = FastAPI()
    app.include_router(init_live_output_routes())
    app.include_router(init_mobile_routes())
    with TestClient(app) as client:
        yield client
    mobile_hub.publisher = None
    mobile_hub.viewers.clear()

def test_remote_and_foreign_origin_refused(client, monkeypatch):
    import celia.live_output_routes as routes
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect('/live-output/ws', headers={'origin':'https://evil.example'}):
            pass
    assert e.value.code == 4403
    monkeypatch.setattr(routes, 'is_local', lambda _:False)
    assert client.get('/live-output/receiver').status_code == 403
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect('/live-output/ws'):
            pass
    assert e.value.code == 4403

def test_receiver_negotiates_but_cannot_control_or_publish(client):
    with client.websocket_connect('/mobile/ws?role=studio') as publisher:
        assert publisher.receive_json()['role'] == 'studio'
        with client.websocket_connect('/live-output/ws') as receiver:
            assert receiver.receive_json()['role'] == 'viewer'
            ident = publisher.receive_json()['id']
            publisher.send_json({'type':'signal','id':ident,'data':{'type':'offer','sdp':'offer'}})
            assert receiver.receive_json()['data']['sdp'] == 'offer'
            receiver.send_json({'type':'control','action':'body','value':'3d'})
            receiver.send_json({'type':'signal','data':{'type':'offer','sdp':'fake publisher'}})
            receiver.send_json({'type':'signal','data':{'type':'answer','sdp':'valid answer'}})
            # The next publisher message must be the answer, never the forbidden messages.
            answer = publisher.receive_json()
            assert answer == {'type':'signal','id':ident,'data':{'type':'answer','sdp':'valid answer'}}

def test_asset_allowlist(client):
    assert client.get('/live-output/secrets').status_code == 404
