"""Authenticated, loopback-only P5 bot status and process-control routes."""

from __future__ import annotations

import hmac
import ipaddress
import os

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict

from .schemas import BotHeartbeatV2
from ..core.bot_process_manager import BotProcessManager, BotStatus


class BotStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bot_id: str
    state: str
    pid: int | None
    process_alive: bool
    connected: bool
    last_heartbeat_mono: float | None
    queue_depth: int
    restart_count: int
    error_code: str | None
    instance_id: str | None
    enabled: bool
    launchable: bool
    readiness: str
    phase: str


def _require_loopback(request: Request) -> None:
    client = request.client
    try:
        if client is None or not ipaddress.ip_address(client.host).is_loopback:
            raise ValueError
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="loopback_only") from exc


def _require_bearer(request: Request, expected: str, *, unconfigured_code: str) -> None:
    expected = expected.strip()
    if not expected:
        raise HTTPException(status_code=503, detail=unconfigured_code)
    authorization = request.headers.get("authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied:
        raise HTTPException(status_code=401, detail="bearer_required")
    if not hmac.compare_digest(supplied.strip(), expected):
        raise HTTPException(status_code=401, detail="invalid_bearer")


def _admin_auth(request: Request) -> None:
    _require_loopback(request)
    _require_bearer(
        request,
        os.environ.get("CELIA_ADMIN_BOT_TOKEN", ""),
        unconfigured_code="admin_auth_unconfigured",
    )


def _status_payload(manager: BotProcessManager, bot_id: str) -> dict:
    status: BotStatus = manager.status(bot_id)
    entry = manager.registry.get(bot_id)
    if entry is None:
        raise HTTPException(status_code=404, detail="unknown_bot")
    return BotStatusResponse(
        bot_id=status.bot_id,
        state=status.state,
        pid=status.pid,
        process_alive=status.process_alive,
        connected=status.connected,
        last_heartbeat_mono=status.last_heartbeat_mono,
        queue_depth=status.queue_depth,
        restart_count=status.restart_count,
        error_code=status.error_code,
        instance_id=status.instance_id,
        enabled=entry.enabled,
        launchable=entry.manually_launchable,
        readiness=entry.readiness,
        phase=entry.phase,
    ).model_dump()


def init_bot_admin_routes(manager: BotProcessManager) -> APIRouter:
    router = APIRouter()

    @router.get("/bridge/v2/bots")
    async def list_bots(request: Request):
        _admin_auth(request)
        return {"bots": [_status_payload(manager, status.bot_id) for status in manager.list_status()]}

    @router.get("/bridge/v2/bots/{bot_id}")
    async def bot_status(bot_id: str, request: Request):
        _admin_auth(request)
        if manager.registry.get(bot_id) is None:
            raise HTTPException(status_code=404, detail="unknown_bot")
        return _status_payload(manager, bot_id)

    @router.get("/admin/api/bots")
    async def admin_list_bots(request: Request):
        _admin_auth(request)
        return {"bots": [_status_payload(manager, status.bot_id) for status in manager.list_status()]}

    @router.get("/admin/api/bots/{bot_id}")
    async def admin_bot_status(bot_id: str, request: Request):
        _admin_auth(request)
        if manager.registry.get(bot_id) is None:
            raise HTTPException(status_code=404, detail="unknown_bot")
        return _status_payload(manager, bot_id)

    @router.post("/admin/api/bots/{bot_id}/start")
    async def admin_start_bot(bot_id: str, request: Request):
        _admin_auth(request)
        if manager.registry.get(bot_id) is None:
            raise HTTPException(status_code=404, detail="unknown_bot")
        status = await manager.start(bot_id)
        if status.state == "disabled":
            raise HTTPException(status_code=409, detail={"code": "bot_not_launchable", "status": _status_payload(manager, bot_id)})
        if status.state == "error":
            raise HTTPException(status_code=503, detail={"code": status.error_code or "bot_start_failed", "status": _status_payload(manager, bot_id)})
        return _status_payload(manager, bot_id)

    @router.post("/admin/api/bots/{bot_id}/stop")
    async def admin_stop_bot(bot_id: str, request: Request):
        _admin_auth(request)
        entry = manager.registry.get(bot_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown_bot")
        if manager.status(bot_id).state == "disabled":
            raise HTTPException(status_code=409, detail="bot_not_launchable")
        await manager.stop(bot_id)
        return _status_payload(manager, bot_id)

    @router.post("/bridge/v2/bots/{bot_id}/heartbeat")
    async def bot_heartbeat(bot_id: str, heartbeat: BotHeartbeatV2, request: Request):
        _require_loopback(request)
        entry = manager.registry.get(bot_id)
        if entry is None:
            raise HTTPException(status_code=404, detail="unknown_bot")
        if heartbeat.bot_id != bot_id:
            raise HTTPException(status_code=422, detail="bot_identity_mismatch")
        token_name = f"CELIA_BOT_HEARTBEAT_TOKEN_{bot_id.upper()}"
        _require_bearer(
            request,
            os.environ.get(token_name, ""),
            unconfigured_code="bot_heartbeat_auth_unconfigured",
        )
        accepted = manager.heartbeat(
            bot_id,
            instance_id=heartbeat.instance_id,
            queue_depth=heartbeat.queue_depth,
            reported_state=heartbeat.state,
        )
        if not accepted:
            raise HTTPException(status_code=409, detail="process_not_owned_or_instance_stale")
        return {"accepted": True, "status": _status_payload(manager, bot_id)}

    return router
