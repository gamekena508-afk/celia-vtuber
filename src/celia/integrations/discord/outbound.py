from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import random
import subprocess
import sys
from pathlib import Path
from typing import Any

import aiohttp

ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from integrations.discord import bot_choice

SECRETS = ROOT / "secrets" / ".env"
PID_FILE = ROOT / "var" / "run" / "celia-discord.pid"
LOG_FILE = ROOT / "var" / "logs" / "celia-discord.log"
BOT_SCRIPT = ROOT / "bots" / "discord" / "run.py"
BOT_PYTHON = (
    ROOT / "bots" / "discord" / ".venv" / "Scripts" / "python.exe"
    if os.name == "nt"
    else ROOT / "bots" / "discord" / ".venv" / "bin" / "python"
)
GENERATED_DIR = ROOT / "assets" / "generated-images"

CAPTIONS = (
    "Saiu mais uma arte daqui. Eu aprovei, então tá valendo.",
    "Olha o que acabou de sair daqui.",
    "Pronto. Mais uma arte feita por aqui.",
)

def _env() -> dict[str, str]:
    data: dict[str, str] = {}
    if SECRETS.is_file():
        for raw in SECRETS.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            data[key.strip()] = value.strip().strip('"').strip("'")
    return data


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def _bot_pid() -> int | None:
    if PID_FILE.is_file():
        try:
            pid = int(PID_FILE.read_text().strip())
            if _pid_alive(pid):
                return pid
        except Exception:
            pass
    needle = str(BOT_SCRIPT)
    try:
        processes = list(Path("/proc").iterdir())
    except OSError:
        return None
    for proc in processes:
        if not proc.name.isdigit():
            continue
        try:
            cmdline = (proc / "cmdline").read_bytes().replace(b"\0", b" ").decode(errors="ignore")
        except Exception:
            continue
        if needle in cmdline:
            try:
                return int(proc.name)
            except ValueError:
                continue
    return None


def _selection_env() -> dict[str, str]:
    """Match the API token to the bot currently selected by the Discord adapter."""
    return {**_env(), **os.environ}


def _settings(
    *, env: dict[str, str] | None = None, var_dir: str | Path | None = None,
) -> tuple[str, int]:
    selected_env = _selection_env() if env is None else dict(env)
    resolution = bot_choice.resolve(
        env=selected_env,
        var_dir=Path(var_dir) if var_dir is not None else ROOT / "var",
    )
    token = resolution.token.strip()
    raw_channel = selected_env.get("CELIA_DISCORD_DEFAULT_CHANNEL_ID", "").strip()
    channel_id = int(raw_channel) if raw_channel.isdigit() else 0
    return token, channel_id


def latest_generated_image() -> Path | None:
    allowed = {".png", ".jpg", ".jpeg", ".webp"}
    files = [p for p in GENERATED_DIR.glob("*/*") if p.is_file() and p.suffix.lower() in allowed and p.parent.name != "temp"]
    return max(files, key=lambda p: p.stat().st_mtime) if files else None


def _mentions(mark_everyone: bool) -> dict[str, list[str]]:
    return {"parse": ["everyone"] if mark_everyone else []}

async def discord_status() -> dict[str, Any]:
    token, channel_id = _settings()
    pid = _bot_pid()
    result: dict[str, Any] = {
        "ok": bool(token),
        "token_configured": bool(token),
        "bot_running": bool(pid),
        "pid": pid,
        "default_channel_id": channel_id or None,
    }
    if not token:
        result.update(code="token_missing", message="CELIA_DISCORD_TOKEN não configurado")
        return result
    try:
        headers = {"Authorization": f"Bot {token}"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get("https://discord.com/api/v10/users/@me", timeout=aiohttp.ClientTimeout(total=10)) as response:
                result["api_http"] = response.status
                if response.status == 200:
                    user = await response.json()
                    result.update(api_ok=True, bot_name=user.get("username"), bot_id=user.get("id"))
                else:
                    result.update(api_ok=False, code="discord_api_error")
    except Exception as exc:
        result.update(api_ok=False, code="network_error", message=str(exc))
    return result

async def start_discord_bot() -> dict[str, Any]:
    current = _bot_pid()
    if current:
        return {"ok": True, "already_running": True, "pid": current}
    token, _ = _settings()
    if not token:
        return {"ok": False, "code": "token_missing", "message": "CELIA_DISCORD_TOKEN não configurado"}
    if os.environ.get("CELIA_DISCORD_ENABLED", "").strip().lower() not in {"1", "true", "yes", "on"}:
        return {"ok": False, "code": "bot_disabled", "message": "Bot Discord desabilitado no runtime V02"}
    if not BOT_PYTHON.is_file() or not BOT_SCRIPT.is_file():
        return {"ok": False, "code": "bot_runtime_missing", "message": "Runtime do bot Discord não encontrado"}
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOG_FILE.open("ab") as log:
        proc = subprocess.Popen(
            [str(BOT_PYTHON), str(BOT_SCRIPT)],
            cwd=str(ROOT),
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            env=os.environ.copy(),
        )
    PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    await asyncio.sleep(3.0)
    if not _pid_alive(proc.pid):
        return {"ok": False, "code": "bot_start_failed", "message": "Bot encerrou durante a inicialização"}
    return {"ok": True, "already_running": False, "pid": proc.pid}


def _resolve_channel(requested: int | str | None) -> int:
    _, default = _settings()
    raw = str(requested or "").strip()
    return int(raw) if raw.isdigit() else default

def _prepare_content(text: str, mark_everyone: bool) -> str:
    content = (text or "").strip()
    if not content:
        content = random.choice(CAPTIONS)
    if mark_everyone and not content.startswith("@everyone"):
        content = "@everyone " + content
    return content[:1900]


async def post_message(text: str, channel_id: int | str | None = None, *, mark_everyone: bool = False) -> dict[str, Any]:
    token, _ = _settings()
    target = _resolve_channel(channel_id)
    if not token:
        return {"ok": False, "code": "token_missing", "message": "Token Discord não configurado"}
    if not target:
        return {"ok": False, "code": "channel_missing", "message": "Canal Discord não definido"}
    payload = {
        "content": _prepare_content(text, mark_everyone),
        "allowed_mentions": _mentions(mark_everyone),
    }
    try:
        headers = {"Authorization": f"Bot {token}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.post(
                f"https://discord.com/api/v10/channels/{target}/messages",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=20),
            ) as response:
                data = await response.json(content_type=None)
                if response.status not in (200, 201):
                    return {"ok": False, "code": "discord_http_error", "http": response.status, "message": str(data)[:500]}
                return {"ok": True, "channel_id": target, "message_id": data.get("id"), "marked_everyone": mark_everyone}
    except Exception as exc:
        return {"ok": False, "code": "network_error", "message": str(exc)}

async def post_image(
    file_path: str | Path | None = None,
    text: str = "",
    channel_id: int | str | None = None,
    *,
    use_latest: bool = True,
    mark_everyone: bool = False,
) -> dict[str, Any]:
    token, _ = _settings()
    target = _resolve_channel(channel_id)
    if not token:
        return {"ok": False, "code": "token_missing", "message": "Token Discord não configurado"}
    if not target:
        return {"ok": False, "code": "channel_missing", "message": "Canal Discord não definido"}
    path = Path(file_path).expanduser().resolve() if file_path else (latest_generated_image() if use_latest else None)
    if path is None or not path.is_file():
        return {"ok": False, "code": "image_missing", "message": "Imagem para postagem não encontrada"}
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        return {"ok": False, "code": "unsupported_file", "message": "Arquivo não é uma imagem suportada"}
    payload = {
        "content": _prepare_content(text, mark_everyone),
        "allowed_mentions": _mentions(mark_everyone),
        "attachments": [{"id": 0, "filename": path.name}],
    }
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    try:
        headers = {"Authorization": f"Bot {token}"}
        form = aiohttp.FormData()
        form.add_field("payload_json", json.dumps(payload, ensure_ascii=False), content_type="application/json")
        with path.open("rb") as handle:
            form.add_field("files[0]", handle, filename=path.name, content_type=mime)
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.post(
                    f"https://discord.com/api/v10/channels/{target}/messages",
                    data=form,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status not in (200, 201):
                        return {"ok": False, "code": "discord_http_error", "http": response.status, "message": str(data)[:500]}
                    return {
                        "ok": True,
                        "channel_id": target,
                        "message_id": data.get("id"),
                        "file_path": str(path),
                        "marked_everyone": mark_everyone,
                    }
    except Exception as exc:
        return {"ok": False, "code": "network_error", "message": str(exc)}
