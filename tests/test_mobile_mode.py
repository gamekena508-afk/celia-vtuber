"""No Célia process, phone, GPU or secrets are needed for these contracts."""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from celia.mobile_auth import MOBILE_COOKIE, MOBILE_PASSWORD_HASH_ENV, mobile_auth
from celia.mobile_routes import init_mobile_routes, mobile_hub
from celia.roteiro_auth import hash_password

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.delenv(MOBILE_PASSWORD_HASH_ENV, raising=False)
    mobile_auth._sessions.clear()
    mobile_auth._failures.clear()
    mobile_auth.max_failures = 5
    mobile_hub.publisher = None
    mobile_hub.viewers.clear()
    app = FastAPI()
    app.include_router(init_mobile_routes())
    with TestClient(app) as test_client:
        yield test_client
    mobile_auth._sessions.clear()
    mobile_auth._failures.clear()
    mobile_hub.publisher = None
    mobile_hub.viewers.clear()


def login(client, monkeypatch):
    monkeypatch.setenv(MOBILE_PASSWORD_HASH_ENV, hash_password("passphrase-for-mobile-test"))
    response = client.post("/mobile/login", json={"password": "passphrase-for-mobile-test"})
    assert response.status_code == 200
    return response


def test_fail_closed_without_a_password_and_no_leak(client):
    response = client.get("/mobile/")
    assert response.status_code == 200
    assert "Senha" in response.text
    assert "source-stage" not in response.text
    assert client.post("/mobile/login", json={"password": "anything"}).status_code == 503
    assert client.get("/mobile/app", follow_redirects=False).status_code == 303
    assert client.get("/mobile/static/app.js").status_code == 401
    assert client.get("/mobile/status").status_code == 401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/mobile/ws"):
            pass
    assert exc.value.code == 4401


def test_login_cookie_unlocks_only_mobile_and_logout_revokes(client, monkeypatch):
    response = login(client, monkeypatch)
    cookie = response.headers["set-cookie"]
    assert MOBILE_COOKIE in cookie
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/mobile" in cookie
    assert "passphrase-for-mobile-test" not in cookie
    assert client.get("/mobile/app").status_code == 200
    assert 'id="move-character"' in client.get("/mobile/app").text
    assert client.get("/mobile/static/app.js").status_code == 200
    assert client.get("/mobile/static/studio.js").status_code == 200  # same authenticated session
    assert client.get("/mobile/static/enquete-base.webp").headers["content-type"] == "image/webp"  # arte da enquete da live
    assert client.get("/mobile/static/no-such-file.js").status_code == 404
    assert client.get("/mobile/status").json() == {"online": False}
    assert client.post("/mobile/logout").status_code == 200
    assert client.get("/mobile/static/app.js").status_code == 401


def test_bad_password_lockout(client, monkeypatch):
    monkeypatch.setenv(MOBILE_PASSWORD_HASH_ENV, hash_password("correct"))
    mobile_auth.max_failures = 2
    assert client.post("/mobile/login", json={"password": "wrong"}).status_code == 401
    assert client.post("/mobile/login", json={"password": "wrong"}).status_code == 401
    response = client.post("/mobile/login", json={"password": "correct"})
    assert response.status_code == 429
    assert int(response.headers["retry-after"]) >= 1


def test_studio_loopback_only_and_assets_gate(client, monkeypatch):
    assert client.get("/mobile/studio").status_code == 200
    assert client.get("/mobile/studio/status").status_code == 200
    import celia.mobile_routes as routes
    monkeypatch.setattr(routes, "is_local", lambda client: False)
    assert client.get("/mobile/studio").status_code == 403
    assert client.get("/mobile/studio/status").status_code == 403
    assert client.get("/mobile/static/studio.js").status_code == 401


def test_ws_rejects_bad_origin_and_unauthenticated_studio(client, monkeypatch):
    login(client, monkeypatch)
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/mobile/ws", headers={"origin": "https://evil.example"}):
            pass
    assert exc.value.code == 4401
    import celia.mobile_routes as routes
    monkeypatch.setattr(routes, "is_local", lambda client: False)
    with pytest.raises(WebSocketDisconnect) as other:
        with client.websocket_connect("/mobile/ws?role=studio"):
            pass
    assert other.value.code == 4401


def test_authenticated_signaling_video_offer_and_drag_control(client, monkeypatch):
    login(client, monkeypatch)
    with client.websocket_connect("/mobile/ws") as viewer:
        assert viewer.receive_json() == {"type": "ready", "role": "viewer", "online": False}
        with client.websocket_connect("/mobile/ws?role=studio") as studio:
            greeting = studio.receive_json()
            assert greeting["role"] == "studio"
            ident = greeting["viewers"][0]
            assert viewer.receive_json() == {"type": "studio-online"}
            studio.send_json({"type": "signal", "id": ident, "data": {"type": "offer", "sdp": "example"}})
            assert viewer.receive_json()["data"] == {"type": "offer", "sdp": "example"}
            viewer.send_json({"type": "signal", "data": {"type": "answer", "sdp": "reply"}})
            answer = studio.receive_json()
            assert answer["id"] == ident and answer["data"]["type"] == "answer"
            viewer.send_json({"type": "control", "action": "move", "dx": .25, "dy": -.1})
            assert studio.receive_json() == {"type": "control", "action": "move", "dx": .25, "dy": -.1}
            viewer.send_json({"type": "control", "action": "scene", "value": "violet"})
            assert studio.receive_json() == {"type": "control", "action": "scene", "value": "violet"}
            studio.send_json({"type": "stage-state", "state": {"scene": "violet", "private": "secret"}})
            state = viewer.receive_json()
            assert state == {"type": "stage-state", "state": {"scene": "violet"}}
        assert viewer.receive_json() == {"type": "studio-offline"}


def test_subtitle_center_and_drag_handle_are_separate_by_source_contract():
    studio_js = (ROOT / "frontend/mobile/studio.js").read_text(encoding="utf8")
    mobile_js = (ROOT / "frontend/mobile/app.js").read_text(encoding="utf8")
    html = (ROOT / "frontend/mobile/app.html").read_text(encoding="utf8")
    # 2026-10-25: legenda continua centralizada na horizontal; a altura virou ajustavel ("Legenda: altura" -> capY).
    assert "cx = W / 2;" in studio_js and "H * capY" in studio_js
    assert "paintCaption();" in studio_js
    assert 'id="move-character"' in html
    assert "move.setPointerCapture(e.pointerId)" in mobile_js
    assert 'action("move", {dx, dy})' in mobile_js
    assert "pose.x + message.dx" in studio_js
    assert "pose.y + message.dy" in studio_js
    # 2026-10-25: o corpo e desenhado pela pose efetiva (a pose do Pai, ou o reenquadramento abaixo da enquete da live).
    assert "ctx.translate(W * (0.5 + pe.x), H * (0.5 + pe.y))" in studio_js and "function poseEfetiva()" in studio_js

def test_lan_boundary_blocks_main_interface_and_websocket_bypass():
    from celia.mobile_routes import LanMobileBoundary

    inner = FastAPI()
    inner.include_router(init_mobile_routes())

    @inner.get("/client-ws")
    async def dangerous_route():
        return {"private": True}

    inner.add_middleware(LanMobileBoundary)

    class RemoteDevice:
        async def __call__(self, scope, receive, send):
            new = dict(scope)
            if scope["type"] in {"http", "websocket"}:
                new["client"] = ("192.168.0.77", 55000)
            await inner(new, receive, send)

    with TestClient(RemoteDevice()) as phone:
        assert phone.get("/mobile/").status_code == 200
        assert phone.get("/client-ws").status_code == 403
        assert phone.get("/mobile/studio").status_code == 403
        with pytest.raises(WebSocketDisconnect) as exc:
            with phone.websocket_connect("/client-ws"):
                pass
        assert exc.value.code == 4403
