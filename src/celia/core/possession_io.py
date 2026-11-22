"""Side-effect adapters of the possession (loopback only): the Jev daemon, the owner's screen, the persona text.

Kept apart from `possession.py` so the decision logic stays pure and these can be replaced by fakes in tests. Nothing here accepts a free
shell string: the body only receives typed actions the daemon re-validates, speech goes through the existing speech coordinator and TTS.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .raizes import RAIZ, VAR

CENTRAL_URL = os.getenv("CELIA_CENTRAL_URL", "http://127.0.0.1:12393")
MAX_SHOTS = 3
SHOT_WIDTH = 1280  # the model reads the frame, it does not need 4K: smaller is faster and cheaper


def _loopback(url: str) -> bool:
    return url.startswith(("http://127.0.0.1:", "http://localhost:", "http://[::1]:"))


def _request(url: str, method: str, payload: dict[str, Any] | None, headers: dict[str, str], timeout: float) -> dict[str, Any]:
    if not _loopback(url):
        return {"ok": False, "error": "url_not_loopback"}
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # loopback only, checked above
            body = json.loads(response.read().decode("utf-8") or "{}")
            return body if isinstance(body, dict) else {"ok": False, "error": "bad_answer"}
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode("utf-8")).get("detail", "")
        except Exception:
            detail = ""
        return {"ok": False, "error": f"http_{exc.code}", "detail": str(detail)[:160]}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"unavailable:{type(exc).__name__}"}


def _daemon_token() -> str:
    from .minecraft_hud import _token  # the central already holds the daemon credential; the agent never sees it

    return _token()


def daemon_call(method: str, path: str, payload: dict[str, Any] | None = None, timeout: float = 8.0) -> dict[str, Any]:
    """Blocking; run through ``asyncio.to_thread``."""
    from .minecraft_hud import DAEMON_URL

    try:
        token = _daemon_token()
    except (OSError, ValueError, PermissionError):
        return {"ok": False, "error": "credential_unavailable"}
    return _request(f"{DAEMON_URL}{path}", method, payload, {"X-Jev-Token": token}, timeout)


def central_post(path: str, payload: dict[str, Any], timeout: float = 70.0) -> dict[str, Any]:
    return _request(f"{CENTRAL_URL}{path}", "POST", payload, {}, timeout)


def cancel_playback(speech_id: str) -> dict[str, Any]:
    return central_post(f"/bridge/playback/{speech_id}/cancel", {}, timeout=5.0)


def current_speech_id() -> str:
    try:
        from .continuous_behavior import continuous_behavior

        return str(continuous_behavior.active_speech_id or "")
    except Exception:
        return ""


def capture_screen(root: Path | None = None, *, active_window: bool = False) -> dict[str, Any]:
    """The OWNER's screen (what the audience sees). KDE Spectacle, no prompt, no cursor; downscaled; only the last few frames are kept."""
    folder = Path(root or Path(VAR) / "possession" / "shots")
    folder.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%H%M%S")
    raw = folder / f"raw-{stamp}.png"
    out = folder / f"screen-{stamp}-{int(time.time() * 1000) % 100000}.jpg"
    try:
        done = subprocess.run(["spectacle", "-b", "-n", "-a" if active_window else "-f", "-o", str(raw)], capture_output=True, timeout=12, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "error": f"capture_unavailable:{type(exc).__name__}"}
    if done.returncode != 0 or not raw.is_file() or raw.stat().st_size < 2000:
        return {"ok": False, "error": "capture_failed"}
    try:
        from PIL import Image

        with Image.open(raw) as image:
            width, height = image.size
            scale = min(1.0, SHOT_WIDTH / float(width))
            small = image.convert("RGB").resize((max(1, int(width * scale)), max(1, int(height * scale))))
            small.save(out, "JPEG", quality=72)
    except Exception as exc:
        return {"ok": False, "error": f"image_failed:{type(exc).__name__}"}
    finally:
        raw.unlink(missing_ok=True)
    shots = sorted(folder.glob("screen-*.jpg"), key=lambda p: p.stat().st_mtime)
    for old in shots[:-MAX_SHOTS]:  # do not pile up old observations: only the freshest frames exist
        old.unlink(missing_ok=True)
    data = out.read_bytes()
    return {"ok": True, "path": str(out), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "size": [small.size[0], small.size[1]],
            "captured_at": time.time(), "perspective": "owner_screen"}


def persona() -> dict[str, Any]:
    """Célia's persona text from conf.yaml (read-only): the controller must sound like her, not like an assistant."""
    import yaml

    try:
        conf = yaml.safe_load((Path(RAIZ) / "conf.yaml").read_text(encoding="utf-8"))
        text = str(((conf or {}).get("character_config") or {}).get("persona_prompt") or "")
    except (OSError, ValueError, yaml.YAMLError):
        return {"ok": False, "error": "persona_unavailable"}
    return {"ok": bool(text), "persona_prompt": text, "chars": len(text)}
