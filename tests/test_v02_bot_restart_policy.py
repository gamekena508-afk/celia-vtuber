import asyncio
from pathlib import Path

import pytest

from v02_bot_test_support import manager_for, satisfy_startup, wait_for_pid


HELPER = r"""
from pathlib import Path
import sys, time
root = Path.cwd()
counter = root / 'starts.txt'
try:
    number = int(counter.read_text(encoding='ascii')) + 1
except Exception:
    number = 1
counter.write_text(str(number), encoding='ascii')
print('helper-start-' + str(number), flush=True)
while not (root / ('exit-' + str(number) + '.flag')).exists():
    time.sleep(0.01)
raise SystemExit(7)
"""


async def wait_for_terminal(manager, *, error_code: str, timeout: float = 3.0):
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        status = manager.status("probe")
        if status.state == "error" and status.error_code == error_code:
            return status
        await asyncio.sleep(0.01)
    raise AssertionError(f"manager did not reach {error_code}")


@pytest.mark.asyncio
async def test_crashes_restart_with_backoff_and_stop_at_finite_limit(tmp_path):
    manager = manager_for(
        tmp_path,
        HELPER,
        startup_timeout=0.8,
        max_restarts=1,
        restart_backoff=(0.01,),
        restart_policy="on-failure",
    )
    start_task = asyncio.create_task(manager.start("probe"))
    first = await satisfy_startup(manager, start_task)
    bot_root = manager.registry.get("probe").root
    (bot_root / "exit-1.flag").touch()

    second_process = await wait_for_pid(manager, previous_pid=first.pid)
    assert second_process.instance_id != first.instance_id
    assert manager.status("probe").restart_count == 1
    assert manager.heartbeat("probe", instance_id=second_process.instance_id, queue_depth=0)
    assert manager.status("probe").connected

    (bot_root / "exit-2.flag").touch()
    terminal = await wait_for_terminal(manager, error_code="restart_limit")
    assert terminal.restart_count == 1
    assert terminal.pid is None
    assert not terminal.process_alive
    await manager.shutdown()


@pytest.mark.asyncio
async def test_manual_stop_and_never_policy_do_not_restart(tmp_path):
    manager = manager_for(tmp_path, "import time; time.sleep(30)", restart_policy="never")
    start_task = asyncio.create_task(manager.start("probe"))
    started = await satisfy_startup(manager, start_task)
    stopped = await manager.stop("probe")
    assert stopped.state == "stopped"
    assert stopped.restart_count == 0
    assert not stopped.process_alive
    await manager.shutdown()
