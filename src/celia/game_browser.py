"""Shared normal-browser launcher for Célia games."""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import urlencode

from loguru import logger

GAME_BROWSER_PATHS = {
    "tic-tac-toe": "tic-tac-toe",
    "checkers": "checkers",
    "chess": "chess",
}
GAME_MODES = frozenset({"criador-vs-celia", "chat-vs-celia", "chat-vs-criador"})


def game_url(game_id: str, mode: str = "criador-vs-celia", *, base_url: str = "http://127.0.0.1:12393") -> str:
    game_id = str(game_id or "").strip().lower()
    mode = str(mode or "criador-vs-celia").strip().lower()
    if game_id not in GAME_BROWSER_PATHS:
        raise ValueError("invalid game")
    if mode not in GAME_MODES:
        raise ValueError("invalid game mode")
    path = GAME_BROWSER_PATHS[game_id]
    return f"{base_url.rstrip('/')}/games/{path}/?{urlencode({'mode': mode})}"

def _default_browser_desktop() -> str:
    xdg_settings = shutil.which("xdg-settings")
    if not xdg_settings:
        return ""
    try:
        result = subprocess.run(
            [xdg_settings, "get", "default-web-browser"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return result.stdout.strip().lower() if result.returncode == 0 else ""


def _detect_browser_command() -> list[str] | None:
    override = os.environ.get("CELIA_GAME_BROWSER", "").strip()
    if override:
        parts = shlex.split(override)
        if parts and (shutil.which(parts[0]) or os.path.isabs(parts[0])):
            return parts

    desktop = _default_browser_desktop()
    families = [
        (("firefox",), ("firefox",)),
        (("brave",), ("brave-browser", "brave")),
        (("google-chrome", "chrome"), ("google-chrome-stable", "google-chrome")),
        (("chromium",), ("chromium", "chromium-browser")),
        (("vivaldi",), ("vivaldi-stable", "vivaldi")),
        (("microsoft-edge", "edge"), ("microsoft-edge-stable", "microsoft-edge")),
    ]
    ordered = []
    for markers, executables in families:
        if desktop and any(marker in desktop for marker in markers):
            ordered.extend(executables)
    for _markers, executables in families:
        ordered.extend(executables)

    seen = set()
    for executable in ordered:
        if executable in seen:
            continue
        seen.add(executable)
        resolved = shutil.which(executable)
        if resolved:
            return [resolved]
    return None


def _session_profile_dir() -> str:
    """Return a Chrome profile bound to the current graphical login session."""
    session_token = (
        os.environ.get("XAUTHORITY")
        or os.environ.get("WAYLAND_DISPLAY")
        or os.environ.get("DISPLAY")
        or "session"
    )
    token = os.path.basename(str(session_token)).strip() or "session"
    safe = "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in token)
    profile = (
        Path(__file__).resolve().parents[2]
        / "var"
        / "game-browser-profiles"
        / f"chrome-{safe}"
    )
    profile.mkdir(parents=True, exist_ok=True)
    return str(profile)


def _launch_args(command: list[str], url: str) -> list[str]:
    executable = os.path.basename(command[0]).lower()
    args = list(command)
    chromium_family = any(
        name in executable
        for name in ("chrome", "chromium", "brave", "vivaldi", "edge")
    )
    if chromium_family:
        # Force X11 under Plasma/Wayland so the game window is attached to the
        # same visible desktop Célia uses and can be verified by the WM.
        # A session-specific profile avoids Chrome forwarding the URL to an old
        # browser process left alive on another graphical login.
        args.extend(
            [
                "--ozone-platform=x11",
                f"--user-data-dir={_session_profile_dir()}",
                "--no-first-run",
                "--no-default-browser-check",
                "--new-window",
                url,
            ]
        )
    elif "firefox" in executable:
        args.extend(["--new-window", url])
    else:
        args.append(url)
    return args


def open_game_browser(
    game_id: str,
    mode: str = "criador-vs-celia",
    *,
    base_url: str = "http://127.0.0.1:12393",
    dry_run: bool = False,
) -> dict[str, object]:
    """Open a game in a normal browser window; never request app/kiosk/fullscreen mode."""
    url = game_url(game_id, mode, base_url=base_url)
    if dry_run:
        return {"ok": True, "game": game_id, "mode": mode, "url": url, "launcher": "dry-run"}

    primary = _detect_browser_command()
    candidates: list[list[str]] = []
    seen: set[str] = set()

    def add_candidate(command: list[str] | None) -> None:
        if not command:
            return
        key = "\0".join(command)
        if key in seen:
            return
        seen.add(key)
        candidates.append(command)

    add_candidate(primary)

    # Fall back only to concrete browser binaries. Generic desktop openers
    # (xdg-open/gio) can report success after forwarding the URL to a stale
    # browser process on another graphical login, which made Célia say "abri"
    # even though no visible window existed.
    for executable in (
        "firefox",
        "google-chrome-stable",
        "google-chrome",
        "chromium",
        "chromium-browser",
        "brave-browser",
        "brave",
        "vivaldi-stable",
        "vivaldi",
        "microsoft-edge-stable",
        "microsoft-edge",
    ):
        resolved = shutil.which(executable)
        if resolved:
            add_candidate([resolved])

    if not candidates:
        return {
            "ok": False,
            "game": game_id,
            "mode": mode,
            "url": url,
            "error": "browser_launcher_unavailable",
        }

    failed_attempts: list[dict[str, object]] = []
    for command in candidates:
        launcher = os.path.basename(command[0])
        if launcher == "gio" and len(command) > 1 and command[1] == "open":
            args = [*command, url]
        else:
            args = _launch_args(command, url)

        try:
            # Keep quick-failure stderr long enough to diagnose the rejected
            # launcher, without attaching the long-lived browser to our logs.
            with tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as stderr_log:
                process = subprocess.Popen(
                    args,
                    stdout=subprocess.DEVNULL,
                    stderr=stderr_log,
                    start_new_session=True,
                )
                time.sleep(0.15)
                returncode = process.poll()
                if returncode in (None, 0):
                    if failed_attempts:
                        logger.warning(
                            "[Games] browser fallback accepted by {} after failures: {}",
                            launcher,
                            failed_attempts,
                        )
                    return {
                        "ok": True,
                        "game": game_id,
                        "mode": mode,
                        "url": url,
                        "launcher": launcher,
                        "launch_state": "accepted",
                        "failed_launchers": failed_attempts,
                    }

                stderr_log.seek(0)
                detail = stderr_log.read()[-2000:].strip()
                failure = {
                    "launcher": launcher,
                    "status": returncode,
                    "stderr": detail,
                }
                failed_attempts.append(failure)
                logger.warning(
                    "[Games] browser launcher {} exited with status {}: {}",
                    launcher,
                    returncode,
                    detail or "(sem stderr)",
                )
        except (OSError, subprocess.SubprocessError) as exc:
            failure = {"launcher": launcher, "error": repr(exc)}
            failed_attempts.append(failure)
            logger.warning("[Games] browser launcher {} failed: {}", launcher, repr(exc))

    return {
        "ok": False,
        "game": game_id,
        "mode": mode,
        "url": url,
        "launcher": failed_attempts[-1].get("launcher", "") if failed_attempts else "",
        "error": "all browser launchers failed",
        "failed_launchers": failed_attempts,
    }
