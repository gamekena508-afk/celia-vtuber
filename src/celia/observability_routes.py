"""Local-only HTTP surface for the Célia execution observer."""

from __future__ import annotations

import asyncio
import json
from importlib.metadata import PackageNotFoundError, version
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from .core.observability import observability


_RUNTIME_PACKAGES = (
    "fastapi",
    "uvicorn",
    "numpy",
    "pydantic",
    "httpx",
    "mcp",
    "pysbd",
    "sentence-transformers",
    "sounddevice",
    "faiss-cpu",
)


def _runtime_package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package in _RUNTIME_PACKAGES:
        try:
            versions[package] = version(package)
        except PackageNotFoundError:
            versions[package] = "NOT INSTALLED"
    return versions


def runtime_snapshot(context) -> dict:
    """Return allowlisted facts from the loaded process, never configuration secrets."""
    character = getattr(context, "character_config", None)
    agent_config = getattr(character, "agent_config", None)
    agent_name = getattr(agent_config, "conversation_agent_choice", None)
    agent_settings = getattr(getattr(agent_config, "agent_settings", None), agent_name or "", None)
    provider_name = getattr(agent_settings, "llm_provider", None)
    provider = getattr(getattr(agent_config, "llm_configs", None), provider_name or "", None)
    tts_engine = getattr(context, "tts_engine", None)

    try:
        active_preset = tts_engine.get_active_preset() if tts_engine else None
    except Exception:
        active_preset = None

    try:
        from .core.emotion_bus import barramento

        emotion_bus = barramento.diagnostico()
    except Exception:
        emotion_bus = {"state": "NOT AVAILABLE"}

    try:
        from .core.continuous_behavior import continuous_behavior

        behavior = continuous_behavior.safe_snapshot()
    except Exception:
        behavior = {"state": "NOT AVAILABLE"}

    try:
        from .core.llm_telemetry import llm_telemetry

        llm_calls = llm_telemetry.safe_snapshot()
    except Exception:
        llm_calls = {"available": False, "reason": "NOT AVAILABLE"}

    try:
        from .core.guardiao import guardiao

        guardian = guardiao.diagnostico()
    except Exception:
        guardian = {"state": "NOT AVAILABLE"}

    max_history = getattr(agent_settings, "max_history", None)
    return {
        "available": character is not None,
        "source": "loaded Célia process configuration and Python environment",
        "agent": {
            "name": agent_name or "NOT AVAILABLE",
            "llm_provider": provider_name or "NOT AVAILABLE",
            "max_history_turns": max_history,
            "local_role_message_cap": max_history * 2 if isinstance(max_history, int) else None,
            "segment_method": getattr(agent_settings, "segment_method", None),
            "mcp_enabled": getattr(agent_settings, "use_mcpp", None),
            "memory_book_enabled": getattr(agent_settings, "memory_book_enabled", None),
            "memory_book_embeddings": getattr(agent_settings, "memory_book_embeddings_enabled", None),
            "memory_book_embedding_model": getattr(agent_settings, "memory_book_embeddings_model", None),
        },
        "llm": {
            "model": getattr(provider, "model", None),
            "base_url": getattr(provider, "base_url", None),
            "temperature": getattr(provider, "temperature", None),
            "max_output_tokens": getattr(provider, "max_tokens", None),
            "provider_context_window_tokens": None,
            "context_window_state": "NOT AVAILABLE: loaded configuration does not report a provider context window",
        },
        "tool_agent": {
            "enabled": getattr(agent_settings, "tool_agent_enabled", None),
            "model": getattr(agent_settings, "tool_agent_model", None),
            "base_url": getattr(agent_settings, "tool_agent_base_url", None),
            "temperature": getattr(agent_settings, "tool_agent_temperature", None),
            "max_output_tokens": getattr(agent_settings, "tool_agent_max_tokens", None),
        },
        "voice": {
            "engine_loaded": tts_engine is not None,
            "active_preset": active_preset,
            "transport_health": "NOT PROBED by this endpoint",
        },
        "emotion_bus": emotion_bus,
        "continuous_behavior": behavior,
        "llm_calls": llm_calls,
        "guardian": guardian,
        "libraries": _runtime_package_versions(),
    }


def _short(text, limit: int = 150) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def topology_snapshot(context) -> dict:
    """Architecture derived from the loaded process, never from a hand-kept list.

    Exposes only what the observer needs to draw the map: server names, tool
    names, short descriptions and counts. Never input schemas, arguments,
    results, prompts, keys or file paths.
    """
    character = getattr(context, "character_config", None)
    agent_config = getattr(character, "agent_config", None)
    agent_name = getattr(agent_config, "conversation_agent_choice", None)
    agent_settings = getattr(getattr(agent_config, "agent_settings", None), agent_name or "", None)
    enabled = list(getattr(agent_settings, "mcp_enabled_servers", None) or [])

    registry = getattr(context, "mcp_server_registery", None)
    registered = sorted((getattr(registry, "servers", None) or {}).keys())

    tool_manager = getattr(context, "tool_manager", None)
    raw_tools = getattr(tool_manager, "tools", None) or {}

    servers: dict[str, dict] = {}
    for name in registered or enabled:
        servers[name] = {
            "enabled": name in enabled,
            "tools": [],
            "tool_count": 0,
            "description": _short(getattr((getattr(registry, "servers", None) or {}).get(name, None), "description", "")),
        }
    orphan_tools = []
    for tool_name, tool in raw_tools.items():
        server = getattr(tool, "related_server", None) or "unknown"
        entry = {"name": str(tool_name), "description": _short(getattr(tool, "description", ""))}
        if server not in servers:
            servers[server] = {"enabled": server in enabled, "tools": [], "tool_count": 0, "description": ""}
            orphan_tools.append(str(tool_name))
        servers[server]["tools"].append(entry)
    for data in servers.values():
        data["tools"].sort(key=lambda t: t["name"])
        data["tool_count"] = len(data["tools"])

    tts_engine = getattr(context, "tts_engine", None)
    asr_engine = getattr(context, "asr_engine", None)
    tts_cfg = getattr(getattr(character, "tts_config", None), "tts_model", None)
    asr_cfg = getattr(getattr(character, "asr_config", None), "asr_model", None)

    return {
        "available": True,
        "source": "loaded process",
        "agent": {"name": agent_name, "tool_agent_enabled": bool(getattr(agent_settings, "tool_agent_enabled", False))},
        "mcp": {
            "registered": sorted(servers.keys()),
            "enabled": enabled,
            "disabled": sorted(s for s in servers if s not in enabled),
            "server_count": len(servers),
            "enabled_count": len([s for s in servers if s in enabled]),
            "total_tools": sum(d["tool_count"] for d in servers.values()),
            "servers": servers,
            "tools_without_registered_server": orphan_tools,
        },
        "subsystems": {
            "tts": {"configured": tts_cfg, "engine_loaded": tts_engine is not None},
            "asr": {"configured": asr_cfg, "engine_loaded": asr_engine is not None},
            "live2d": {"model": getattr(character, "live2d_model_name", None)},
            "character": {"conf_name": getattr(character, "conf_name", None)},
        },
    }


def is_loopback_host(host: str | None) -> bool:
    return (host or "").lower() in {"127.0.0.1", "::1", "localhost", "testclient"}


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if not is_loopback_host(host):
        raise HTTPException(status_code=403, detail="Local observability endpoint")


def init_observability_routes(context=None) -> APIRouter:
    router = APIRouter(prefix="/debug/observability", tags=["observability"])

    @router.get("/runtime")
    async def runtime(request: Request):
        _require_loopback(request)
        return runtime_snapshot(context)

    @router.get("/topology")
    async def topology(request: Request):
        _require_loopback(request)
        return topology_snapshot(context)

    def jev_catalog():
        engine = getattr(context, "agent_engine", None)
        llm = getattr(engine, "_tool_llm", None)
        links = getattr(llm, "_links", ())
        return list(dict.fromkeys(link.model for link in links
                                  if not getattr(link, "local_gpu_job", False)))

    @router.get("/settings-preferences")
    async def settings_preferences(request: Request):
        _require_loopback(request)
        from .core.settings_preferences import get_preferences
        return {"preferences": get_preferences(), "jev_models": jev_catalog()}

    @router.put("/settings-preferences")
    async def save_settings_preferences(request: Request):
        _require_loopback(request)
        from .core.settings_preferences import update_preferences
        # JSON only, and reject cross-origin browser writes.
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
            raise HTTPException(403, "Cross-origin settings write")
        if request.headers.get("content-type", "").split(";")[0] != "application/json":
            raise HTTPException(415, "JSON required")
        try:
            return {"preferences": update_preferences(await request.json(), allowed_models=jev_catalog()),
                    "jev_models": jev_catalog()}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("/settings-status")
    async def settings_status(request: Request):
        _require_loopback(request)
        from .core.speech_coordinator import speech_coordinator
        from .asr.asr_interface import transcription_snapshot
        from .conversations.tts_manager import tts_queue_snapshot

        sources = (
            "direct_user", "discord_direct", "critical_game_event", "system",
            "bridge", "game_commentary", "live", "ambient",
        )
        counts = dict.fromkeys(("pending", "generated", "dispatched", "playing", "cancel_requested"), 0)
        for item in speech_coordinator.snapshot()["active"]:
            if item["status"] in counts:
                counts[item["status"]] += 1
        character = getattr(context, "character_config", None)
        asr_config = getattr(character, "asr_config", None)
        engine_name = getattr(asr_config, "asr_model", None)
        engine_config = getattr(asr_config, engine_name or "", None)
        loaded_asr = getattr(context, "asr_engine", None)
        return {
            "priorities": {source: speech_coordinator.source_priority(source) for source in sources},
            "speech": counts,
            "tts_queue": tts_queue_snapshot(),
            "asr": {"engine": engine_name,
                    "model": getattr(loaded_asr, "model", None) if isinstance(getattr(loaded_asr, "model", None), str) else None,
                    "configured_model": getattr(engine_config, "model", None),
                    "queue_state": "Entrada ASR assíncrona do núcleo",
                    **transcription_snapshot()},
        }

    @router.get("/snapshot")
    async def snapshot(request: Request, after: int = 0):
        _require_loopback(request)
        return {
            "available": True,
            "latest_sequence": observability.latest_sequence(),
            "events": observability.snapshot(max(0, after)),
        }

    @router.get("/events")
    async def events(request: Request, after: int = 0):
        _require_loopback(request)
        last_sequence = max(0, after)

        async def stream() -> AsyncIterator[str]:
            nonlocal last_sequence
            while not await request.is_disconnected():
                pending = observability.snapshot(last_sequence)
                for event in pending:
                    last_sequence = event["sequence"]
                    payload = json.dumps(event, separators=(",", ":"))
                    yield f"id: {last_sequence}\nevent: trace\ndata: {payload}\n\n"
                if not pending:
                    yield ": keepalive\n\n"
                await asyncio.sleep(0.25)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return router
