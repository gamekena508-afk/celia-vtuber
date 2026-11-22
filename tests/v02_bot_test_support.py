"""Temporary, no-network fixtures for the P5 bot process supervisor tests."""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

from celia.bridge.registry import BotRegistry
from celia.core.bot_process_manager import BotLaunchSpec, BotProcessManager


def manager_for(
    tmp_path: Path,
    script: str,
    *,
    startup_timeout: float = 1.0,
    stop_timeout: float = 0.3,
    max_restarts: int = 0,
    restart_backoff: tuple[float, ...] = (0.01,),
    restart_policy: str = "on-failure",
    env_file: Path | None = None,
    env_allowlist: tuple[str, ...] = (),
) -> BotProcessManager:
    project = tmp_path / "fixture-project"
    bot_root = project / "bots" / "probe"
    config_dir = project / "config"
    bot_root.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    manifest_rel = "bots/probe/manifest.yaml"
    manifest = {
        "schema_version": 1,
        "bot_id": "probe",
        "enabled": False,
        "launch_policy": "manual; launchable through test code allowlist",
    }
    (bot_root / "manifest.yaml").write_text(yaml.safe_dump(manifest), encoding="utf-8")
    config = {
        "schema_version": 1,
        "default_enabled": False,
        "startup_policy": "manual_only",
        "bots": {
            "probe": {
                "enabled": False,
                "root": "bots/probe",
                "launcher": "probe-python",
                "health": "heartbeat",
                "restart": restart_policy,
                "startup_timeout_sec": startup_timeout,
                "launchable": True,
                "manifest": manifest_rel,
                "readiness": "test-only",
                "phase": "test-only",
            }
        },
    }
    (config_dir / "bots.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    spec = BotLaunchSpec(
        bot_id="probe",
        cwd=bot_root.resolve(),
        executable=Path(sys.executable).resolve(),
        args=("-c", script),
        env_file=env_file,
        startup_timeout_s=startup_timeout,
        stop_timeout_s=stop_timeout,
        restart_policy=restart_policy,
        env_allowlist=env_allowlist,
    )
    registry = BotRegistry.load(project, launch_allowlist={"probe-python": spec})
    return BotProcessManager(
        registry,
        log_dir=tmp_path / "logs",
        max_restarts=max_restarts,
        restart_backoff_s=restart_backoff,
    )


async def wait_for_pid(manager: BotProcessManager, *, previous_pid: int | None = None, timeout: float = 3.0):
    import asyncio

    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status = manager.status("probe")
        if status.pid is not None and status.process_alive and status.pid != previous_pid:
            return status
        await asyncio.sleep(0.01)
    raise AssertionError("managed helper process did not become alive")


async def satisfy_startup(manager: BotProcessManager, task, *, previous_pid: int | None = None):
    status = await wait_for_pid(manager, previous_pid=previous_pid)
    assert status.instance_id
    assert manager.heartbeat("probe", instance_id=status.instance_id, queue_depth=0)
    return await task
