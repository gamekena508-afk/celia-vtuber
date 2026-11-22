import asyncio

import httpx
from fastapi import FastAPI

from celia import bridge_routes
from celia.core.raizes import RAIZ


def _app():
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    return app


def test_desktop_pet_launcher_is_a_fixed_loopback_action(monkeypatch):
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(bridge_routes.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)
    monkeypatch.setattr(bridge_routes.sys, "platform", "linux")

    async def request():
        transport = httpx.ASGITransport(app=_app(), client=("testclient", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/bridge/desktop-pet/open")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert response.json() == {"ok": True, "action": "desktop_pet_launch_requested"}
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args == (str((RAIZ / "tools" / "run_pet.sh").resolve()),)
    assert kwargs["cwd"] == str(RAIZ)
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] is asyncio.subprocess.DEVNULL
    assert kwargs["stderr"] is asyncio.subprocess.DEVNULL


def test_desktop_pet_launcher_uses_powershell_on_windows(monkeypatch):
    calls = []

    async def fake_create_subprocess_exec(*args, **kwargs):
        calls.append((args, kwargs))
        return object()

    monkeypatch.setattr(bridge_routes.sys, "platform", "win32")
    monkeypatch.setattr(bridge_routes.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)

    async def request():
        transport = httpx.ASGITransport(app=_app(), client=("testclient", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/bridge/desktop-pet/open")

    response = asyncio.run(request())

    assert response.status_code == 200
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:5] == ("powershell.exe", "-NoLogo", "-NoProfile", "-ExecutionPolicy", "Bypass")
    assert args[5] == "-File"
    assert args[6] == str((RAIZ / "tools" / "run_pet.ps1").resolve())
    assert kwargs["cwd"] == str(RAIZ)
    assert kwargs["start_new_session"] is True
    assert kwargs["stdout"] is asyncio.subprocess.DEVNULL
    assert kwargs["stderr"] is asyncio.subprocess.DEVNULL


def test_desktop_pet_launcher_rejects_non_loopback_clients():
    async def request():
        transport = httpx.ASGITransport(app=_app(), client=("203.0.113.7", 50000))
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post("/bridge/desktop-pet/open")

    response = asyncio.run(request())

    assert response.status_code == 403
    assert response.json()["detail"] == "Local behavior control only"
