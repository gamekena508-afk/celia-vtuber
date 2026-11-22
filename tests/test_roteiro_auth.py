from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from celia.roteiro_auth import PASSWORD_HASH_ENV, hash_password, roteiro_auth
from celia.roteiro_routes import init_roteiro_routes


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv(PASSWORD_HASH_ENV, hash_password("correct-pass"))
    roteiro_auth._sessions.clear()
    roteiro_auth._failures.clear()
    app = FastAPI()
    app.include_router(init_roteiro_routes())
    with TestClient(app) as test_client:
        yield test_client
    roteiro_auth._sessions.clear()
    roteiro_auth._failures.clear()


def test_login_page_does_not_deliver_the_teleprompter(client):
    response = client.get("/roteiro/")

    assert response.status_code == 200
    assert "Senha" in response.text
    assert "ROTEIRO AO VIVO" not in response.text
    assert "/roteiro/app.js" not in response.text


def test_unauthenticated_app_redirects_without_delivering_content(client):
    response = client.get("/roteiro/app", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/roteiro/"
    assert response.content == b""


def test_wrong_password_does_not_create_session_or_expose_snapshot(client):
    response = client.post("/roteiro/login", json={"password": "wrong-pass"})

    assert response.status_code == 401
    assert response.json() == {"ok": False, "error": "invalid_credentials"}
    assert client.get("/roteiro/status").status_code == 401
    assert client.get("/roteiro/app.js").status_code == 401


def test_correct_password_unlocks_status_assets_and_websocket(client):
    login = client.post("/roteiro/login", json={"password": "correct-pass"})

    assert login.status_code == 200
    assert login.json() == {"ok": True}
    cookie = login.headers["set-cookie"]
    assert "celia_roteiro_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/roteiro" in cookie
    assert client.get("/roteiro/app").status_code == 200
    assert client.get("/roteiro/app.js").status_code == 200
    assert client.get("/roteiro/status").status_code == 200

    with client.websocket_connect("/roteiro/ws") as websocket:
        message = websocket.receive_json()
        assert message["type"] == "roteiro.snapshot"


def test_websocket_without_session_is_rejected(client):
    with pytest.raises(WebSocketDisconnect) as error:
        with client.websocket_connect("/roteiro/ws"):
            pass
    assert error.value.code == 4401


def test_login_cooldown_limits_repeated_failures(client):
    roteiro_auth.max_failures = 2
    client.post("/roteiro/login", json={"password": "wrong-pass"})
    client.post("/roteiro/login", json={"password": "wrong-pass"})

    response = client.post("/roteiro/login", json={"password": "correct-pass"})

    assert response.status_code == 429
    assert response.headers["cache-control"] == "no-store"


def test_logout_revokes_session(client):
    assert client.post("/roteiro/login", json={"password": "correct-pass"}).status_code == 200
    assert client.post("/roteiro/logout").status_code == 200
    assert client.get("/roteiro/status").status_code == 401
