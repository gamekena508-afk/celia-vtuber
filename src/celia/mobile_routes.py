"""Authenticated portrait broadcast signaling on the existing FastAPI server.

The PC browser is the only publisher. The phone has only presentation controls,
never the existing privileged bridge/executor APIs.
"""
from __future__ import annotations

import asyncio
import json
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse

from .mobile_auth import MOBILE_COOKIE, mobile_auth
from .core.obs_subtitles import obs_subtitles

ASSETS = Path(__file__).resolve().parents[2] / "frontend" / "mobile"
PALETTES = {"black", "violet", "blue", "green", "studio"}
MODELS = {"celia_v3_web.vrm", "celia_v4_web.vrm"}


def is_local(client) -> bool:
    return bool(client and client.host in {"127.0.0.1", "::1", "testclient"})


def authorized(request: Request) -> bool:
    return mobile_auth.valid_session(request.cookies.get(MOBILE_COOKIE))


def headers() -> dict:
    return {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer", "X-Frame-Options": "SAMEORIGIN"}


def require_session(request: Request) -> None:
    if not authorized(request):
        raise HTTPException(401, "mobile_auth_required", headers=headers())


def caption_snapshot() -> dict:
    with obs_subtitles._lock:
        state = obs_subtitles._state.copy()
    return {"text": str(state.get("text", ""))[:512] if state.get("type") == "subtitle" and state.get("status") == "playing" else "",
            "started_at": state.get("started_at"), "duration": state.get("duration")}


class LanMobileBoundary:
    """Protect the main Célia UI and privileged WebSockets from LAN bypasses.

    The existing authenticated /roteiro surface is preserved; the /mobile
    surface enforces its own session. Every other route remains loopback-only.
    Not a firewall substitute: the owner still needs a scoped UFW rule.
    """
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        client = scope.get("client")
        host = client[0] if client else None
        path = scope.get("path", "")
        exposed = any(path == prefix or path.startswith(prefix + "/")
                      for prefix in ("/mobile", "/roteiro"))
        if host not in {"127.0.0.1", "::1", "testclient"} and not exposed:
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 4403})
            else:
                body = b"LAN endpoint restricted"
                await send({"type": "http.response.start", "status": 403,
                            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                                        (b"cache-control", b"no-store")]})
                await send({"type": "http.response.body", "body": body})
            return
        return await self.app(scope, receive, send)


class MobileHub:
    def __init__(self):
        self.publisher: WebSocket | None = None
        # Page-load time (ms) the current publisher announced; 0 = an older page without a claim.
        self.publisher_claim = 0.0
        self.viewers: dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()

    async def join(self, ws: WebSocket, studio: bool, claim: float = 0.0) -> str | None:
        async with self.lock:
            if studio:
                if self.publisher is not None:
                    # Newest studio page wins, deterministically: only a strictly larger load-time
                    # claim takes the seat. An older page (or one with no claim) that reconnects
                    # is refused, so two pages can never trade the seat back and forth.
                    if not claim > self.publisher_claim:
                        return None
                    old = self.publisher
                    self.publisher = None
                    try:
                        await old.send_json({"type": "studio-replaced"})
                    except Exception:
                        pass
                    try:
                        await old.close(code=4410)
                    except Exception:
                        pass
                await ws.accept()
                self.publisher = ws
                self.publisher_claim = claim
                await ws.send_json({"type": "ready", "role": "studio", "viewers": list(self.viewers)})
                for client in list(self.viewers.values()):
                    try:
                        await client.send_json({"type": "studio-online"})
                    except Exception:
                        pass
                return "studio"
            if len(self.viewers) >= 4:
                return None
            ident = secrets.token_urlsafe(12)
            await ws.accept()
            self.viewers[ident] = ws
            await ws.send_json({"type": "ready", "role": "viewer", "online": bool(self.publisher)})
            if self.publisher:
                await self.publisher.send_json({"type": "viewer-joined", "id": ident})
            return ident

    async def leave(self, ws: WebSocket, ident: str) -> None:
        async with self.lock:
            if ident == "studio" and self.publisher is ws:
                self.publisher = None
                self.publisher_claim = 0.0
                for viewer in list(self.viewers.values()):
                    try:
                        await viewer.send_json({"type": "studio-offline"})
                    except Exception:
                        pass
            elif self.viewers.get(ident) is ws:
                del self.viewers[ident]
                if self.publisher:
                    try:
                        await self.publisher.send_json({"type": "viewer-left", "id": ident})
                    except Exception:
                        pass

    async def handle(self, ident: str, msg: dict) -> None:
        kind = msg.get("type")
        if kind == "signal":
            data = msg.get("data")
            if not isinstance(data, dict) or data.get("type") not in {"offer", "answer", "candidate"}:
                return
            receiver = (self.viewers.get(msg.get("id")) if isinstance(msg.get("id"), str) else None) if ident == "studio" else self.publisher
            if receiver:
                try:
                    await receiver.send_json({"type": "signal", "id": ident, "data": data})
                except Exception:
                    pass
            return
        if ident != "studio" and kind == "control":
            action = msg.get("action")
            if action not in {"move", "zoom", "center", "scene", "body", "model"}:
                return
            cleaned = {"type": "control", "action": action}
            if action == "move":
                dx, dy = msg.get("dx"), msg.get("dy")
                if not all(type(v) in (int, float) and abs(v) <= 1 for v in (dx, dy)):
                    return
                cleaned.update(dx=float(dx), dy=float(dy))
            elif action == "zoom":
                v = msg.get("step")
                if type(v) not in (int, float) or not -0.5 <= v <= 0.5:
                    return
                cleaned["step"] = float(v)
            elif action in {"scene", "body", "model"}:
                v = msg.get("value")
                if v not in (PALETTES if action == "scene" else ({"2d", "3d"} if action == "body" else MODELS)):
                    return
                cleaned["value"] = v
            if self.publisher:
                try:
                    await self.publisher.send_json(cleaned)
                except Exception:
                    pass
        elif ident == "studio" and kind == "stage-state":
            state = msg.get("state")
            if not isinstance(state, dict):
                return
            safe = {k: state[k] for k in ("scene", "body", "model", "zoom")
                    if k in state and type(state[k]) in (str, int, float)}
            for viewer in list(self.viewers.values()):
                try:
                    await viewer.send_json({"type": "stage-state", "state": safe})
                except Exception:
                    pass


mobile_hub = MobileHub()


def init_mobile_routes() -> APIRouter:
    router = APIRouter()

    @router.get("/mobile")
    @router.get("/mobile/")
    async def login_page(request: Request):
        if authorized(request):
            return RedirectResponse("/mobile/app", status_code=303, headers=headers())
        return FileResponse(ASSETS / "login.html", headers=headers())

    @router.post("/mobile/login")
    async def login(request: Request):
        ip = request.client.host if request.client else "unknown"
        allowed = mobile_auth.login_allowed(ip)
        if not allowed.allowed:
            return JSONResponse({"ok": False}, status_code=429, headers={**headers(), "Retry-After": str(allowed.retry_after)})
        if not mobile_auth.configured:
            return JSONResponse({"ok": False}, status_code=503, headers=headers())
        raw = await request.body()
        if len(raw) > 4096:
            return JSONResponse({"ok": False}, status_code=400, headers=headers())
        try:
            payload = json.loads(raw)
            pwd = payload.get("password") if isinstance(payload, dict) else None
        except (UnicodeDecodeError, ValueError):
            pwd = None
        if not isinstance(pwd, str) or not mobile_auth.authenticate(pwd, ip):
            return JSONResponse({"ok": False}, status_code=401, headers=headers())
        response = JSONResponse({"ok": True}, headers=headers())
        response.set_cookie(MOBILE_COOKIE, mobile_auth.create_session(), httponly=True,
                            secure=request.url.scheme == "https", samesite="lax",
                            max_age=mobile_auth.session_ttl, path="/mobile")
        return response

    @router.post("/mobile/logout")
    async def logout(request: Request):
        mobile_auth.revoke(request.cookies.get(MOBILE_COOKIE))
        response = JSONResponse({"ok": True}, headers=headers())
        response.delete_cookie(MOBILE_COOKIE, path="/mobile", samesite="lax")
        return response

    @router.get("/mobile/app")
    async def app(request: Request):
        if not authorized(request):
            return RedirectResponse("/mobile/", status_code=303, headers=headers())
        return FileResponse(ASSETS / "app.html", headers=headers())

    @router.get("/mobile/studio")
    async def studio(request: Request):
        if not is_local(request.client):
            raise HTTPException(403, "studio_local_only")
        return FileResponse(ASSETS / "studio.html", headers=headers())

    @router.get("/mobile/studio/status")
    async def studio_status(request: Request):
        if not is_local(request.client):
            raise HTTPException(403, "studio_local_only")
        return JSONResponse({"caption": caption_snapshot()}, headers=headers())

    @router.get("/mobile/status")
    async def status(request: Request):
        require_session(request)
        return JSONResponse({"online": bool(mobile_hub.publisher)}, headers=headers())

    @router.get("/mobile/static/{filename}")
    async def assets(filename: str, request: Request):
        # Local PC studio assets may load before a mobile password exists; the
        # mobile app bundle itself always requires the viewer session.
        if filename == "app.js" or not is_local(request.client):
            require_session(request)
        # enquete-base.webp: arte da enquete da live desenhada pelo estudio (2026-10-25).
        if filename not in {"mobile.css", "app.js", "studio.js", "enquete-base.webp", "selo-ia.png"}:
            raise HTTPException(404, "asset_not_found")
        media = "image/webp" if filename.endswith(".webp") else None
        return FileResponse(ASSETS / filename, headers=headers(), media_type=media)

    @router.websocket("/mobile/ws")
    async def ws_endpoint(ws: WebSocket):
        studio = ws.query_params.get("role") == "studio"
        origin = ws.headers.get("origin")
        parsed = urlsplit(origin) if origin else None
        same_origin = not parsed or (parsed.scheme in {"http", "https"} and parsed.netloc == ws.headers.get("host"))
        allowed = is_local(ws.client) if studio else mobile_auth.valid_session(ws.cookies.get(MOBILE_COOKIE))
        if not same_origin or not allowed:
            await ws.close(code=4401)
            return
        try:
            claim = float(ws.query_params.get("claim", "0"))
        except ValueError:
            claim = 0.0
        if not (claim >= 0.0 and claim < 1e15):  # also rejects NaN and inf
            claim = 0.0
        ident = await mobile_hub.join(ws, studio, claim)
        if ident is None:
            await ws.close(code=4409)
            return
        try:
            while True:
                if ident != "studio" and not mobile_auth.valid_session(ws.cookies.get(MOBILE_COOKIE)):
                    await ws.close(code=4401)
                    break
                # Revalidate idle viewer sessions too: an expired cookie may not
                # keep a mobile stream open indefinitely without sending input.
                try:
                    raw = await asyncio.wait_for(ws.receive_text(), timeout=5.0)
                except asyncio.TimeoutError:
                    continue
                if len(raw) > 65536:
                    await ws.close(code=1009)
                    break
                try:
                    msg = json.loads(raw)
                except (UnicodeDecodeError, ValueError):
                    continue
                if isinstance(msg, dict):
                    await mobile_hub.handle(ident, msg)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await mobile_hub.leave(ws, ident)

    return router
