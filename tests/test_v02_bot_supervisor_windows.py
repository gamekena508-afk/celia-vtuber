import asyncio
import dataclasses
import sys
from pathlib import Path

import pytest

from celia.bridge.registry import BotRegistry
from celia.core.bot_process_manager import BotProcessManager
from v02_bot_test_support import manager_for, satisfy_startup


@pytest.mark.asyncio
async def test_managed_python_helper_is_owned_logged_and_stopped(tmp_path):
    manager = manager_for(
        tmp_path,
        "import sys,time; print('fixture stdout', flush=True); print('fixture stderr', file=sys.stderr, flush=True); time.sleep(30)",
        restart_policy="never",
    )
    task = asyncio.create_task(manager.start("probe"))
    started = await satisfy_startup(manager, task)

    assert started.state == "connected"
    assert started.process_alive and started.connected and started.pid
    log_path = tmp_path / "logs" / "probe.stdout.log"
    for _ in range(100):
        if log_path.exists() and "fixture stdout" in log_path.read_text(encoding="utf-8"):
            break
        await asyncio.sleep(0.01)
    stopped = await manager.stop("probe")
    assert stopped.state == "stopped"
    assert not stopped.process_alive and not stopped.connected
    assert "fixture stdout" in log_path.read_text(encoding="utf-8")
    assert "fixture stderr" in (tmp_path / "logs" / "probe.stderr.log").read_text(encoding="utf-8")
    await manager.shutdown()


@pytest.mark.asyncio
async def test_launch_failures_are_explicit_and_startup_timeout_is_finite(tmp_path):
    missing = manager_for(tmp_path / "missing", "pass", restart_policy="never")
    runtime = missing._runtime["probe"]
    runtime.spec = dataclasses.replace(runtime.spec, executable=tmp_path / "missing-python.exe")
    status = await missing.start("probe")
    assert status.state == "error"
    assert status.error_code == "executable_missing"
    assert status.pid is None
    with pytest.raises(KeyError):
        await missing.start("not-in-registry")

    exited = manager_for(
        tmp_path / "exited",
        "import celia_p5_dependency_that_is_not_installed",
        restart_policy="never",
    )
    status = await exited.start("probe")
    assert status.state == "error"
    assert status.error_code == "exited_before_heartbeat"
    assert not status.process_alive

    timeout = manager_for(
        tmp_path / "timeout",
        "import time; time.sleep(30)",
        startup_timeout=0.2,
        stop_timeout=0.2,
        restart_policy="never",
    )
    status = await timeout.start("probe")
    assert status.state == "error"
    assert status.error_code == "heartbeat_timeout"
    assert not status.process_alive
    await asyncio.gather(missing.shutdown(), exited.shutdown(), timeout.shutdown())


@pytest.mark.asyncio
async def test_windows_launch_uses_hidden_window_flag_when_available(monkeypatch, tmp_path):
    import subprocess
    import celia.core.bot_process_manager as module

    original = asyncio.create_subprocess_exec
    seen = {}

    async def capture(*args, **kwargs):
        seen["creationflags"] = kwargs.get("creationflags")
        return await original(*args, **kwargs)

    monkeypatch.setattr(module.asyncio, "create_subprocess_exec", capture)
    manager = manager_for(tmp_path, "import time; time.sleep(30)", restart_policy="never")
    task = asyncio.create_task(manager.start("probe"))
    await satisfy_startup(manager, task)
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    assert seen["creationflags"] == expected
    await manager.shutdown()


@pytest.mark.asyncio
async def test_current_inventory_never_starts_real_bots(tmp_path):
    root = Path(__file__).resolve().parents[1]
    registry = BotRegistry.load(root)
    manager = BotProcessManager(registry, log_dir=tmp_path / "logs")

    assert registry.autostart_ids() == ()
    assert len(registry.list_entries()) == 7
    for entry in registry.list_entries():
        assert not entry.enabled
        assert not entry.launchable
        assert entry.launcher is None
        status = await manager.start(entry.bot_id)
        assert status.state == "disabled"
        assert status.error_code == "bot_not_launchable"
        assert status.pid is None
    assert list((tmp_path / "logs").glob("*")) == [] if (tmp_path / "logs").exists() else True
    await manager.shutdown()
