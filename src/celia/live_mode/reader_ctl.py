"""Controle do processo leitor do TikTok. NUNCA sobe sozinho: só por start() com autorização explícita.

O leitor (integrations/tiktok_live_reader/reader.py) roda num venv próprio e fala com a Célia só por
POST /bridge/live/event com um token descartável gerado a cada start.
Regras: sem sala informada pelo dono não conecta; sem reconexão em laço (o leitor tenta 1 vez);
sala repetida de uma live antiga exige confirmação.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Any

from celia.core.raizes import RAIZ, SEGREDOS

from .settings import LIVE_DIR

READER_DIR = RAIZ / "integrations" / "tiktok_live_reader"
READER_SCRIPT = READER_DIR / "reader.py"
READER_PYTHON = READER_DIR / ".venv" / "Scripts" / "python.exe"
EULER_KEY_FILE = SEGREDOS / "euler_api_key"
SAME_ROOM_COOLDOWN_S = 6 * 3600


class ReaderError(RuntimeError):
    pass


class ReaderController:
    def __init__(self, manager, *, python: Path | None = None, script: Path | None = None,
                 state_dir: Path | None = None, spawn=None):
        self.manager = manager
        self.python = Path(python) if python else READER_PYTHON
        self.script = Path(script) if script else READER_SCRIPT
        self.state_dir = Path(state_dir) if state_dir else LIVE_DIR
        self._spawn = spawn or self._default_spawn
        self.proc: Any = None
        self.token: str | None = None
        self.state = "stopped"          # stopped|starting|connected|error|exited
        self.room_id: str | None = None
        self.started_at: float | None = None
        self.last_event_at: float | None = None
        self.message = ""
        self.exit_code: int | None = None
        self._watch: asyncio.Task | None = None
        self._start_lock = asyncio.Lock()

    # ---- status ----
    def _reconcile(self) -> None:
        """O estado nunca contradiz o processo: se ele morreu e ninguém avisou, vira error/exited aqui."""
        if self.proc is not None and self.proc.poll() is not None and self.state in ("starting", "connected"):
            self.exit_code = self.proc.returncode
            self.state = "exited" if self.proc.returncode == 0 else "error"
            self.message = self.message if self.state == "exited" else f"leitor encerrou (codigo {self.proc.returncode})"

    def status(self) -> dict[str, Any]:
        self._reconcile()
        running = self.proc is not None and self.proc.poll() is None
        return {"state": self.state, "running": running, "pid": getattr(self.proc, "pid", None) if running else None,
                "room_id": self.room_id, "started_at": self.started_at, "last_event_at": self.last_event_at,
                "message": self.message, "exit_code": self.exit_code, "env_ready": self.python.exists() and self.script.exists()}

    def check_token(self, token: str | None) -> bool:
        return bool(self.token) and bool(token) and secrets.compare_digest(self.token, token)

    def report(self, state: str, message: str = "") -> None:
        if state in ("connected", "error", "starting"):
            self.state = state
        self.message = message[:200]
        self.last_event_at = time.time()

    def touch(self) -> None:
        self.last_event_at = time.time()

    # ---- ciclo ----
    def _last_session(self) -> dict[str, Any]:
        try:
            return json.loads((self.state_dir / "last_session.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember_session(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        (self.state_dir / "last_session.json").write_text(
            json.dumps({"room_id": self.room_id, "ended_at": time.time()}), encoding="utf-8")

    def _default_spawn(self, env: dict[str, str]):
        log = (self.state_dir / "reader.log").open("ab")
        return subprocess.Popen([str(self.python), str(self.script)], cwd=str(self.script.parent), env=env,
                                stdout=log, stderr=log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))

    async def start(self, **kwargs) -> dict[str, Any]:
        async with self._start_lock:
            return await self._start_locked(**kwargs)

    async def _start_locked(self, *, room_id: str = "", authorized: bool, event_url: str, confirm_same_room: bool = False,
                    handle: str = "", live_confirmed: bool = False) -> dict[str, Any]:
        if authorized is not True:
            raise ReaderError("autorizacao_da_sessao_ausente: o dono precisa autorizar a live (authorized=true)")
        if self.proc is not None and self.proc.poll() is None:
            return self.status()  # idempotente, inclusive antes da descoberta pelo @
        if handle and not room_id:
            if not live_confirmed:
                raise ReaderError("confirme_live_no_ar_antes_de_buscar_pelo_arroba")
            try:
                got = await asyncio.to_thread(subprocess.run,
                    [str(self.python), str(self.script.with_name("resolve_room.py")), "--handle", handle,
                     "--authorized", "--live-confirmed"],
                    cwd=str(self.script.parent), capture_output=True, text=True, encoding="utf-8", timeout=35,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                data = json.loads(got.stdout)
            except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
                raise ReaderError("descoberta_falhou:" + type(exc).__name__) from exc
            if not data.get("ok"):
                raise ReaderError("descoberta_falhou:" + str(data.get("error") or "sala_ausente"))
            room_id = str(data.get("room_id") or "")
        room = re.sub(r"\D", "", str(room_id or ""))
        if not 8 <= len(room) <= 25:
            raise ReaderError("room_id_invalido: informe o numero da sala da live atual (tirado do link)")
        last = self._last_session()
        if last.get("room_id") == room and time.time() - float(last.get("ended_at", 0)) > SAME_ROOM_COOLDOWN_S and not confirm_same_room:
            raise ReaderError("sala_repetida: essa e a sala de uma live antiga; confirme (confirm_same_room=true) se for mesmo a atual")
        if not (self.python.exists() and self.script.exists()):
            raise ReaderError("leitor_nao_instalado: ambiente em integrations/tiktok_live_reader/.venv ausente (veja o README)")
        await self.manager.enable("live", room_id=room)
        self.token = secrets.token_urlsafe(24)
        # No Windows o os.environ guarda as chaves em MAIÚSCULAS: comparar "SystemRoot" deixava o leitor sem SYSTEMROOT
        # e o asyncio morria no import (_overlapped, WinError 10106). Compara sem caixa.
        keep = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATH", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA", "APPDATA"}
        env = {k: v for k, v in os.environ.items() if k.upper() in keep}
        env.update({"LIVE_EVENT_URL": event_url, "LIVE_READER_TOKEN": self.token, "LIVE_ROOM_ID": room,
                    "EULER_KEY_FILE": str(EULER_KEY_FILE), "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1"})
        self.proc = self._spawn(env)
        self.room_id, self.started_at, self.exit_code = room, time.time(), None
        self.state, self.message = "starting", "processo iniciado; aguardando ConnectEvent real"
        self._watch = asyncio.get_running_loop().create_task(self._monitor(self.proc))
        return self.status()

    async def _monitor(self, proc) -> None:
        while proc.poll() is None:
            await asyncio.sleep(1.0)
        if proc is self.proc:
            self.exit_code = proc.returncode
            if self.state != "stopped":
                self.state = "exited" if proc.returncode == 0 else "error"
                self.message = self.message or f"leitor encerrou (codigo {proc.returncode})"

    async def stop(self) -> dict[str, Any]:
        proc = self.proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            for _ in range(50):
                if proc.poll() is not None:
                    break
                await asyncio.sleep(0.1)
            else:
                proc.kill()
        if proc is not None:
            self.exit_code = proc.poll()
            self._remember_session()
        self.proc, self.token = None, None
        self.state, self.message = "stopped", "leitor parado"   # estado e token limpos ANTES de mexer no monitor
        watch, self._watch = self._watch, None
        if watch is not None:
            try:
                watch.cancel()
                if watch.get_loop() is asyncio.get_running_loop():
                    await asyncio.gather(watch, return_exceptions=True)
            except Exception:  # monitor de outro loop/fechado: o processo já foi encerrado acima
                pass
        return self.status()
