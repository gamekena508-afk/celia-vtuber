"""Ctrl+N gesture: send the last unanswered line straight to Célia (2026-10-02).

Owner's decision 3, key changed by the owner from Ctrl+S (already saves the Pet
pose in frontend/celia-pet.js) to Ctrl+N. The Pet Electron registers it as a
global shortcut, because during a recording the focus is on the site or on
Blender, not on Célia's page. It posts to the loopback route
/bridge/direct-address, which submits the last line routed ROUTING_CONTEXT_ONLY
(at most 30 s old) with the last screen analysis, once.
"""
import asyncio
import inspect
from pathlib import Path

import httpx
from fastapi import FastAPI

from celia.core import direct_address

ROOT = Path(__file__).resolve().parents[1]


def test_unanswered_line_is_taken_once(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(direct_address.time, "monotonic", lambda: clock[0])
    direct_address.forget_unanswered()
    assert direct_address.take_unanswered() is None
    direct_address.remember_unanswered("Tá vendo isso aqui", "u1")
    taken = direct_address.take_unanswered()
    assert taken["text"] == "Tá vendo isso aqui" and taken["utterance_id"] == "u1"
    assert direct_address.take_unanswered() is None  # single use


def test_unanswered_line_expires(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(direct_address.time, "monotonic", lambda: clock[0])
    direct_address.remember_unanswered("fala velha", "u2")
    clock[0] += direct_address.UNANSWERED_MAX_AGE_SEC + 1
    assert direct_address.take_unanswered() is None


def _post(app, client=("testclient", 50000)):
    async def go():
        transport = httpx.ASGITransport(app=app, client=client)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            return await c.post("/bridge/direct-address")
    return asyncio.run(go())


def _app(monkeypatch, submitted):
    from celia import bridge_routes
    from celia.celia_singleton import CeliaSingleton

    class FakeSingleton:
        async def submit_input(self, text, **kwargs):
            submitted.append((text, kwargs))
            return True

    monkeypatch.setattr(CeliaSingleton, "get_instance", staticmethod(lambda: FakeSingleton()))
    app = FastAPI()
    app.include_router(bridge_routes.init_bridge_routes(None))
    return app


# 2026-10-04: Meta+N no longer means "resend the last unanswered line". The endpoint
# is the explicit "Célia, olha isso" visual trigger and works without a pending
# line; its tests live in tests/test_olha_isso_meta_n.py. The pending-line store
# above is kept (and tested) for other purposes.
def test_endpoint_is_loopback_only(monkeypatch):
    direct_address.remember_unanswered("x y z", "u4")
    r = _post(_app(monkeypatch, []), client=("192.168.0.121", 50000))
    assert r.status_code == 403


def test_routes_remembers_context_only_lines():
    import celia.routes as routes
    src = inspect.getsource(routes)
    remember = src.index("direct_address.remember_unanswered(")
    ctx = src.index('"ROUTING_CONTEXT_ONLY",', src.index("direct_address.should_address_celia("))
    assert abs(ctx - remember) < 2500


def test_pet_does_not_take_ctrl_n():
    # Owner, 2026-10-02: the gesture is Meta+N, a KDE custom command shortcut.
    # Ctrl+N opens a new window in Chrome / new file in Blender, and the
    # XWayland Pet cannot grab keys while a Wayland window has focus.
    main = (ROOT / "tools/pet-electron/main.js").read_text(encoding="utf-8")
    assert '"Control+N"' not in main
