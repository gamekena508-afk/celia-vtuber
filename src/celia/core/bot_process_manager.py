"""Finite, owned-process supervisor for explicitly allowlisted V02 bots.

No processes start during construction. Commands can only come from a
code-owned ``BotLaunchSpec`` matched to the manifest's launcher identifier;
request data never supplies an executable, cwd, arguments, or environment.
"""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from celia.bridge.registry import BotEntry, BotRegistry


_SAFE_ENV_NAMES = frozenset({
    "PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR",
    "LOCALAPPDATA", "APPDATA", "USERPROFILE", "HOMEDRIVE", "HOMEPATH",
    "SYSTEMDRIVE", "COMSPEC", "PATHEXT", "HOME",
})
_SENSITIVE_ENV_PARTS = ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "APIKEY", "CREDENTIAL")
_ENV_NAME_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
_LAUNCH_ENV_ALLOWLIST = {
    "twitch": frozenset({
        "CELIA_TWITCH_ENABLED", "CELIA_BRIDGE_BASE_URL", "TWITCH_CLIENT_ID",
        "TWITCH_USER_ACCESS_TOKEN", "TWITCH_BROADCASTER_ID", "TWITCH_USER_ID",
        "CELIA_BRIDGE_V2_TOKEN_TWITCH_LIVE_CHAT",
    }),
    "youtube": frozenset({
        "CELIA_YOUTUBE_ENABLED", "CELIA_BRIDGE_BASE_URL", "YOUTUBE_API_KEY",
        "YOUTUBE_LIVE_CHAT_ID", "CELIA_BRIDGE_V2_TOKEN_YOUTUBE_LIVE_CHAT",
    }),
}


@dataclass(frozen=True)
class BotLaunchSpec:
    bot_id: str
    cwd: Path
    executable: Path
    args: tuple[str, ...]
    env_file: Path | None = None
    startup_timeout_s: float = 30.0
    stop_timeout_s: float = 5.0
    restart_policy: str = "on-failure"
    env_allowlist: tuple[str, ...] = ()
    heartbeat_token: str | None = field(default=None, repr=False, compare=False)
    heartbeat_url: str = "http://127.0.0.1:12393/bridge/v2/bots/{bot_id}/heartbeat"
    launch_env: Mapping[str, str] = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.bot_id or not self.bot_id.replace("-", "").replace("_", "").isalnum():
            raise ValueError("invalid bot id in launch spec")
        if not isinstance(self.cwd, Path) or not self.cwd.is_absolute():
            raise ValueError("launch cwd must be an absolute Path")
        if not isinstance(self.executable, Path) or not self.executable.is_absolute():
            raise ValueError("launch executable must be an absolute Path")
        if any(not isinstance(arg, str) or "\x00" in arg for arg in self.args):
            raise ValueError("launch arguments must be fixed strings")
        if isinstance(self.startup_timeout_s, bool) or not isinstance(self.startup_timeout_s, (int, float)) or not 0.1 <= self.startup_timeout_s <= 120:
            raise ValueError("startup timeout must be between 0.1 and 120 seconds")
        if isinstance(self.stop_timeout_s, bool) or not isinstance(self.stop_timeout_s, (int, float)) or not 0.1 <= self.stop_timeout_s <= 30:
            raise ValueError("stop timeout must be between 0.1 and 30 seconds")
        if self.restart_policy not in {"on-failure", "never"}:
            raise ValueError("unsupported restart policy")
        if self.heartbeat_token is not None and (not isinstance(self.heartbeat_token, str) or len(self.heartbeat_token) < 32):
            raise ValueError("heartbeat token must contain at least 32 characters")
        if (not isinstance(self.heartbeat_url, str) or not self.heartbeat_url.startswith("http://127.0.0.1:")
                or "{bot_id}" not in self.heartbeat_url or "\x00" in self.heartbeat_url):
            raise ValueError("heartbeat URL must be a fixed loopback endpoint template")
        for name in self.env_allowlist:
            upper = name.upper()
            if not _ENV_NAME_RE.fullmatch(upper) or any(part in upper for part in _SENSITIVE_ENV_PARTS):
                raise ValueError("environment allowlist contains an unsafe name")
        object.__setattr__(self, "env_allowlist", tuple(name.upper() for name in self.env_allowlist))
        if not isinstance(self.launch_env, Mapping):
            raise ValueError("launch environment must be a mapping")
        launch_env: dict[str, str] = {}
        for name, value in self.launch_env.items():
            upper = name.upper() if isinstance(name, str) else ""
            if not _ENV_NAME_RE.fullmatch(upper) or not isinstance(value, str) or "\x00" in value:
                raise ValueError("launch environment contains an invalid entry")
            launch_env[upper] = value
        if set(launch_env) - _LAUNCH_ENV_ALLOWLIST.get(self.bot_id, frozenset()):
            raise ValueError("launch environment exceeds the bot's fixed credential allowlist")
        object.__setattr__(self, "launch_env", MappingProxyType(launch_env))


@dataclass(frozen=True)
class BotStatus:
    bot_id: str
    state: str
    pid: int | None
    last_heartbeat_mono: float | None
    queue_depth: int
    restart_count: int
    error_code: str | None
    process_alive: bool
    connected: bool
    instance_id: str | None


@dataclass
class _Runtime:
    entry: BotEntry
    spec: BotLaunchSpec | None
    process: asyncio.subprocess.Process | None = None
    state: str = "stopped"
    instance_id: str | None = None
    started_mono: float | None = None
    last_heartbeat_mono: float | None = None
    queue_depth: int = 0
    heartbeat_state: str = "starting"
    restart_count: int = 0
    error_code: str | None = None
    manual_stop: bool = False
    ever_connected: bool = False
    watcher: asyncio.Task | None = None
    restarter: asyncio.Task | None = None
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class BotProcessManager:
    """Start and stop only subprocesses created and retained by this instance."""

    DEGRADED_AFTER_S = 15.0
    DISCONNECTED_AFTER_S = 30.0

    def __init__(
        self,
        registry: BotRegistry,
        *,
        log_dir: str | Path,
        max_restarts: int = 5,
        restart_backoff_s: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0, 30.0),
        clock=time.monotonic,
    ):
        if max_restarts < 0 or max_restarts > 10:
            raise ValueError("max_restarts must be between 0 and 10")
        if not restart_backoff_s or any(delay < 0 or delay > 30 for delay in restart_backoff_s):
            raise ValueError("restart backoff must be finite and capped at 30 seconds")
        self.registry = registry
        self.log_dir = Path(log_dir).resolve()
        self.max_restarts = max_restarts
        self.restart_backoff_s = restart_backoff_s
        self._clock = clock
        self._runtime: dict[str, _Runtime] = {}
        self._closing = False
        self._spec_errors: dict[str, str] = {}

        for entry in registry.list_entries():
            candidate = registry.launch_spec(entry.bot_id)
            error = None
            if candidate is not None:
                if not isinstance(candidate, BotLaunchSpec) or candidate.bot_id != entry.bot_id:
                    candidate = None
                    error = "invalid_allowlist_spec"
                elif not self._inside(entry.root, candidate.cwd):
                    candidate = None
                    error = "cwd_outside_bot_root"
                elif candidate.restart_policy != entry.restart_policy:
                    candidate = None
                    error = "restart_policy_mismatch"
            if entry.manually_launchable and candidate is None:
                error = error or "launcher_spec_missing"
            self._runtime[entry.bot_id] = _Runtime(
                entry=entry,
                spec=candidate,
                state="stopped" if candidate is not None else "disabled",
                error_code=error if candidate is None and entry.manually_launchable else None,
            )
            if error:
                self._spec_errors[entry.bot_id] = error

    @staticmethod
    def _inside(root: Path, child: Path) -> bool:
        try:
            child.resolve(strict=True).relative_to(root.resolve(strict=True))
            return True
        except (ValueError, OSError):
            return False

    def status(self, bot_id: str) -> BotStatus:
        runtime = self._runtime.get(bot_id)
        if runtime is None:
            raise KeyError(bot_id)
        process = runtime.process
        alive = process is not None and process.returncode is None
        now = self._clock()
        heartbeat = runtime.last_heartbeat_mono
        connected = (
            alive and heartbeat is not None and now - heartbeat <= self.DEGRADED_AFTER_S
            and runtime.heartbeat_state in {"connected", "busy"} and not runtime.manual_stop
        )
        state = runtime.state
        if not runtime.entry.manually_launchable or runtime.spec is None:
            state = "disabled"
        elif alive and state != "stopping":
            if heartbeat is None:
                state = "starting" if state not in {"restarting", "stopping"} else state
            else:
                age = max(0.0, now - heartbeat)
                if age <= self.DEGRADED_AFTER_S:
                    state = runtime.heartbeat_state
                elif age <= self.DISCONNECTED_AFTER_S:
                    state = "degraded"
                else:
                    state = "disconnected"
        elif runtime.state not in {"error", "disabled", "restarting", "stopping"}:
            state = "error" if runtime.error_code else "stopped"
        return BotStatus(
            bot_id=bot_id,
            state=state,
            pid=process.pid if process is not None else None,
            last_heartbeat_mono=heartbeat,
            queue_depth=runtime.queue_depth,
            restart_count=runtime.restart_count,
            error_code=runtime.error_code,
            process_alive=alive,
            connected=connected,
            instance_id=runtime.instance_id,
        )

    def list_status(self) -> list[BotStatus]:
        return [self.status(bot_id) for bot_id in sorted(self._runtime)]

    def heartbeat(
        self,
        bot_id: str,
        *,
        instance_id: str,
        queue_depth: int = 0,
        busy: bool = False,
        reported_state: str | None = None,
    ) -> bool:
        runtime = self._runtime.get(bot_id)
        if runtime is None or runtime.process is None or runtime.process.returncode is not None:
            return False
        if not instance_id or runtime.instance_id != instance_id:
            return False
        if isinstance(queue_depth, bool) or not isinstance(queue_depth, int) or queue_depth < 0 or queue_depth > 1_000_000:
            return False
        state = reported_state if reported_state is not None else ("busy" if busy else "connected")
        if state not in {"starting", "connected", "busy", "degraded", "disconnected", "stopping"}:
            return False
        runtime.last_heartbeat_mono = self._clock()
        runtime.queue_depth = queue_depth
        runtime.heartbeat_state = state
        runtime.ever_connected |= state in {"connected", "busy"}
        return True

    async def start(self, bot_id: str) -> BotStatus:
        runtime = self._runtime.get(bot_id)
        if runtime is None:
            raise KeyError(bot_id)
        if runtime.spec is None or not runtime.entry.manually_launchable:
            runtime.state = "disabled"
            runtime.error_code = "bot_not_launchable"
            return self.status(bot_id)
        async with runtime.lock:
            if runtime.process is not None and runtime.process.returncode is None:
                return self.status(bot_id)
            if runtime.restarter is not None and not runtime.restarter.done():
                return self.status(bot_id)
            if self._closing:
                runtime.state = "error"
                runtime.error_code = "manager_shutting_down"
                return self.status(bot_id)
            runtime.manual_stop = False
            runtime.restart_count = 0
            runtime.error_code = None
            try:
                await self._spawn_locked(runtime)
            except FileNotFoundError:
                runtime.state = "error"
                runtime.error_code = "executable_missing"
                return self.status(bot_id)
            except OSError:
                runtime.state = "error"
                runtime.error_code = "launch_failed"
                return self.status(bot_id)
        return await self._wait_for_heartbeat(runtime, runtime.spec.startup_timeout_s)

    async def _spawn_locked(self, runtime: _Runtime) -> None:
        spec = runtime.spec
        if spec is None:
            raise FileNotFoundError("launcher unavailable")
        cwd = spec.cwd.resolve(strict=True)
        executable = spec.executable.resolve(strict=True)
        if not cwd.is_dir():
            raise FileNotFoundError("working directory unavailable")
        if not executable.is_file():
            raise FileNotFoundError("executable unavailable")
        if spec.env_file is not None and not self._inside(runtime.entry.root, spec.env_file):
            raise OSError("environment file escapes bot root")
        self.log_dir.mkdir(parents=True, exist_ok=True)
        stdout_path = self.log_dir / f"{runtime.entry.bot_id}.stdout.log"
        stderr_path = self.log_dir / f"{runtime.entry.bot_id}.stderr.log"
        instance_id = uuid.uuid4().hex
        env = self._filtered_environment(spec)
        env["CELIA_BOT_ID"] = runtime.entry.bot_id
        env["CELIA_BOT_INSTANCE_ID"] = instance_id
        if spec.heartbeat_token:
            # Explicitly pass only this bot's credential; sensitive values from
            # the parent environment remain filtered out.
            env["CELIA_BOT_HEARTBEAT_TOKEN"] = spec.heartbeat_token
        env["CELIA_BOT_HEARTBEAT_URL"] = spec.heartbeat_url.format(bot_id=runtime.entry.bot_id)
        stdout_file = stdout_path.open("ab")
        stderr_file = stderr_path.open("ab")
        try:
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            process = await asyncio.create_subprocess_exec(
                str(executable), *spec.args,
                cwd=str(cwd),
                env=env,
                stdout=stdout_file,
                stderr=stderr_file,
                creationflags=creationflags,
            )
        finally:
            stdout_file.close()
            stderr_file.close()
        runtime.process = process
        runtime.instance_id = instance_id
        runtime.started_mono = self._clock()
        runtime.last_heartbeat_mono = None
        runtime.queue_depth = 0
        runtime.heartbeat_state = "starting"
        runtime.ever_connected = False
        runtime.state = "starting"
        runtime.manual_stop = False
        runtime.watcher = asyncio.create_task(
            self._watch_exit(runtime, process, runtime.instance_id),
            name=f"bot-watch-{runtime.entry.bot_id}",
        )

    def _filtered_environment(self, spec: BotLaunchSpec) -> dict[str, str]:
        env = {key: value for key, value in os.environ.items() if key.upper() in _SAFE_ENV_NAMES}
        if spec.env_file is not None:
            allowed = set(spec.env_allowlist)
            if allowed:
                try:
                    lines = spec.env_file.read_text(encoding="utf-8").splitlines()
                except OSError as exc:
                    raise OSError("environment file unavailable") from exc
                for line in lines:
                    stripped = line.strip()
                    if not stripped or stripped.startswith("#") or "=" not in stripped:
                        continue
                    name, value = stripped.split("=", 1)
                    name = name.strip().upper()
                    if name in allowed and _ENV_NAME_RE.fullmatch(name):
                        env[name] = value.strip().strip("\"'")
        # Code-owned specs carry only this bot's explicitly selected values.
        # Apply last so an env-file entry cannot replace the fixed launch map.
        allowed_launch_names = _LAUNCH_ENV_ALLOWLIST.get(spec.bot_id, frozenset())
        env.update({name: value for name, value in spec.launch_env.items()
                    if name in allowed_launch_names})
        return env

    async def _wait_for_heartbeat(self, runtime: _Runtime, timeout_s: float) -> BotStatus:
        deadline = self._clock() + timeout_s
        instance_id = runtime.instance_id
        while self._clock() < deadline:
            process = runtime.process
            if runtime.instance_id != instance_id or runtime.manual_stop:
                return self.status(runtime.entry.bot_id)
            if process is None or process.returncode is not None:
                if runtime.error_code is None:
                    runtime.state = "error"
                    runtime.error_code = "exited_before_heartbeat"
                return self.status(runtime.entry.bot_id)
            if self.status(runtime.entry.bot_id).connected:
                return self.status(runtime.entry.bot_id)
            await asyncio.sleep(min(0.05, max(0.001, deadline - self._clock())))
        await self._fail_start(runtime, instance_id, "heartbeat_timeout")
        return self.status(runtime.entry.bot_id)

    async def _fail_start(self, runtime: _Runtime, instance_id: str | None, error_code: str) -> None:
        process = runtime.process
        if runtime.instance_id != instance_id:
            return
        runtime.manual_stop = True
        runtime.error_code = error_code
        runtime.state = "error"
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=runtime.spec.stop_timeout_s if runtime.spec else 1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if runtime.process is process:
            runtime.process = None
        runtime.state = "error"
        runtime.error_code = error_code

    async def _watch_exit(
        self,
        runtime: _Runtime,
        process: asyncio.subprocess.Process,
        instance_id: str,
    ) -> None:
        returncode = await process.wait()
        if runtime.process is not process or runtime.instance_id != instance_id:
            return
        if runtime.manual_stop or self._closing:
            runtime.process = None
            runtime.state = "stopped"
            return
        runtime.process = None
        runtime.state = "error"
        runtime.error_code = "process_exited" if runtime.ever_connected else "exited_before_heartbeat"
        if not runtime.ever_connected or runtime.spec is None or runtime.spec.restart_policy != "on-failure":
            return
        if runtime.restart_count >= self.max_restarts:
            runtime.error_code = "restart_limit"
            return
        attempt = runtime.restart_count
        runtime.restart_count += 1
        runtime.state = "restarting"
        delay = self.restart_backoff_s[min(attempt, len(self.restart_backoff_s) - 1)]
        runtime.restarter = asyncio.create_task(
            self._restart_after(runtime, delay), name=f"bot-restart-{runtime.entry.bot_id}-{attempt + 1}"
        )

    async def _restart_after(self, runtime: _Runtime, delay: float) -> None:
        try:
            await asyncio.sleep(delay)
            if runtime.manual_stop or self._closing or runtime.spec is None:
                return
            async with runtime.lock:
                if runtime.manual_stop or self._closing:
                    return
                try:
                    await self._spawn_locked(runtime)
                except FileNotFoundError:
                    runtime.state = "error"
                    runtime.error_code = "executable_missing"
                    return
                except OSError:
                    runtime.state = "error"
                    runtime.error_code = "launch_failed"
                    return
            await self._wait_for_heartbeat(runtime, runtime.spec.startup_timeout_s)
        except asyncio.CancelledError:
            return

    async def stop(self, bot_id: str) -> BotStatus:
        runtime = self._runtime.get(bot_id)
        if runtime is None:
            raise KeyError(bot_id)
        async with runtime.lock:
            runtime.manual_stop = True
            runtime.state = "stopping"
            restarter = runtime.restarter
            process = runtime.process
            if restarter is not None and not restarter.done():
                restarter.cancel()
        if restarter is not None and not restarter.done():
            await asyncio.gather(restarter, return_exceptions=True)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=runtime.spec.stop_timeout_s if runtime.spec else 1.0)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        async with runtime.lock:
            if runtime.process is process:
                runtime.process = None
            runtime.state = "stopped"
            runtime.error_code = None
        return self.status(bot_id)

    async def restart(self, bot_id: str) -> BotStatus:
        runtime = self._runtime.get(bot_id)
        if runtime is None:
            raise KeyError(bot_id)
        if runtime.spec is None:
            return self.status(bot_id)
        await self.stop(bot_id)
        return await self.start(bot_id)

    async def shutdown(self) -> None:
        self._closing = True
        await asyncio.gather(*(self.stop(bot_id) for bot_id in self._runtime), return_exceptions=True)

