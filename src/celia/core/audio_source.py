"""Shared Discord/window ASR policy; no credentials or audio in the state files."""
import json
import time
import re
import unicodedata

from .raizes import VAR
from .state_store import atomic_write_json, file_lock

LEASE_SECONDS = 5.0
MODES = {"auto", "discord", "window"}

def _read(name):
    try:
        value = json.loads((VAR / name).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def _write(name, value):
    atomic_write_json(VAR / name, value, backup=False)

def config():
    data = _read("audio_source.json")
    mode = data.get("mode", "auto")
    return {"mode": mode if mode in MODES else "auto",
            "follow": data.get("follow", True) is not False}

def configure(*, mode=None, follow=None):
    if mode is not None and mode not in MODES:
        raise ValueError("invalid audio source")
    if follow is not None and type(follow) is not bool:
        raise ValueError("invalid follow state")
    path = VAR / "audio_source.json"
    with file_lock(path):
        data = config()
        if mode is not None:
            data["mode"] = mode
        if follow is not None:
            data["follow"] = follow
        atomic_write_json(path, data, already_locked=True)
    return data

def publish_discord(connected, channel_id=None):
    _write("discord_audio_lease.json", {"connected": bool(connected),
           "channel_id": str(channel_id) if channel_id else None,
           "at": time.time()})

def status():
    data = config()
    lease = _read("discord_audio_lease.json")
    at = lease.get("at")
    connected = (lease.get("connected") is True and isinstance(at, (int, float))
                 and 0 <= time.time() - at <= LEASE_SECONDS)
    mode = data["mode"]
    source = "window" if mode == "window" else (
        "discord" if connected else ("window" if mode == "auto" else "none"))
    return {**data, "source": source, "discord_connected": connected,
            "channel_id": lease.get("channel_id") if connected else None}

def active_source():
    return status()["source"]


def follow_intent(text):
    normalized = unicodedata.normalize("NFKD", str(text))
    normalized = "".join(c for c in normalized if not unicodedata.combining(c))
    command = re.sub(r"[^a-z0-9]+", " ", normalized.lower()).strip()
    command = re.sub(r"^(?:celia )|(?: celia)$", "", command).strip()
    if command in {"me segue", "vem comigo", "volta a me seguir", "volte a me seguir"}:
        return True
    if command in {"para de seguir", "para de me seguir", "pare de me seguir", "fica ai", "fique ai"}:
        return False
    return None


def publish_owner_level(level):
    _write("discord_owner_level.json", {"level": max(0.0, min(1.0, float(level))), "at": time.time()})


def owner_level():
    if active_source() != "discord":
        return 0.0
    data = _read("discord_owner_level.json")
    at = data.get("at")
    if not isinstance(at, (int, float)) or not 0 <= time.time() - at < 0.6:
        return 0.0
    return max(0.0, min(1.0, float(data.get("level", 0))))
