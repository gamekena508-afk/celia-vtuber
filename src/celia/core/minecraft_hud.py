"""Read-only bridge from the central to the Jev daemon's `/hud` facts (the overlay never talks to the daemon itself).

The daemon is the only owner of the Mineflayer snapshot and of the SQLite map; the central forwards one GET with the
owner-only control credential and caches the answer for a fraction of a second. Failure is reported as an honest
"unavailable" payload, never as stale or invented game values.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

from .raizes import RAIZ, VAR

DAEMON_URL = os.getenv("CELIA_JEV_URL", f"http://127.0.0.1:{os.getenv('CELIA_JEV_PORT', '8784')}")
CACHE_SECONDS = 0.05  # HUD consulta a 8 Hz; 0,05 s so evita rajada de varias abas (daemon responde em ~2 ms)
TIMEOUT_SECONDS = 1.5
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "payload": None}


def _unavailable(reason: str) -> dict[str, Any]:
    return {"ok": True, "available": False, "reason": reason, "at": time.time()}


def _token() -> str:
    root = Path(RAIZ) / "integrations" / "minecraft"
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from celia_minecraft.control import read_control_token

    return read_control_token(Path(os.getenv("CELIA_MINECRAFT_CONTROL_FILE", Path(VAR) / "runtime" / "minecraft-control.token")))


def fetch_hud(now: float | None = None) -> dict[str, Any]:
    """Blocking; call through asyncio.to_thread. At most one upstream request per CACHE_SECONDS."""
    moment = time.monotonic() if now is None else now
    with _lock:
        if _cache["payload"] is not None and moment - _cache["at"] < CACHE_SECONDS:
            return _cache["payload"]
        try:
            request = urllib.request.Request(f"{DAEMON_URL}/hud", headers={"X-Jev-Token": _token()}, method="GET")
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # loopback only
                payload = json.loads(response.read().decode("utf-8"))
            if not isinstance(payload, dict) or payload.get("ok") is not True:
                payload = _unavailable("bad_daemon_answer")
        except ModuleNotFoundError as exc:
            if exc.name not in ("celia_minecraft", "celia_minecraft.control"):
                raise
            payload = _unavailable("jev_daemon_offline")
        except (OSError, ValueError, PermissionError):
            payload = _unavailable("jev_daemon_offline")
        _cache.update(at=moment, payload=payload)
        return payload
