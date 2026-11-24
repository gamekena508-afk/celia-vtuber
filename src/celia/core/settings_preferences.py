"""Local operator preferences. No keys, tool grants or global device settings."""
from pathlib import Path
import json
import os
import tempfile
import threading
from .raizes import VAR

STATE_PATH = VAR / "settings_v3_preferences.json"
_LOCK = threading.RLock()
_DEFAULT = {"jev_model": "", "volume": 100, "playback_speed": 1.0, "discord_reply_output": "auto"}

def get_preferences():
    with _LOCK:
        try:
            raw = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return dict(_DEFAULT)
        except (OSError, ValueError):
            return dict(_DEFAULT)
        v = raw.get("volume")
        speed = raw.get("playback_speed")
        d = raw.get("discord_reply_output")
        m = raw.get("jev_model")
        return {
            "volume": v if type(v) is int and 0 <= v <= 100 else 100,
            "playback_speed": float(speed) if type(speed) in (int, float) and 0.8 <= speed <= 1.25 else 1.0,
            "discord_reply_output": d if d in ("auto", "discord", "window") else "auto",
            "jev_model": m if isinstance(m, str) and len(m) <= 256 else "",
        }

def update_preferences(patch, *, allowed_models=()):
    with _LOCK:
        if not isinstance(patch, dict) or not patch or set(patch) - set(_DEFAULT):
            raise ValueError("unknown preference")
        if "volume" in patch and (type(patch["volume"]) is not int or not 0 <= patch["volume"] <= 100):
            raise ValueError("volume must be integer 0..100")
        if "playback_speed" in patch and (type(patch["playback_speed"]) not in (int, float) or not 0.8 <= patch["playback_speed"] <= 1.25):
            raise ValueError("playback speed must be 0.8..1.25")
        if "discord_reply_output" in patch and patch["discord_reply_output"] not in ("auto", "discord", "window"):
            raise ValueError("invalid output")
        if "jev_model" in patch and (not isinstance(patch["jev_model"], str) or (patch["jev_model"] and patch["jev_model"] not in allowed_models)):
            raise ValueError("model not in loaded Jev chain")
        updated = get_preferences()
        updated.update(patch)
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".settings-v3-", dir=STATE_PATH.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(updated, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, STATE_PATH)
        return updated

def discord_reply_destination(interaction_kind):
    # Only Discord call replies use this transport; do not pretend browser
    # messages can be forwarded into an arbitrary voice channel.
    if interaction_kind not in ("discord_call", "discord_call_greeting"):
        return "window"
    choice = get_preferences()["discord_reply_output"]
    return "discord" if choice in ("auto", "discord") else "window"
