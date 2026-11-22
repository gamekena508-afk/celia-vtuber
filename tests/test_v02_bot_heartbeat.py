import asyncio
import time

import pytest

from v02_bot_test_support import manager_for, wait_for_pid


@pytest.mark.asyncio
async def test_process_alive_and_bridge_connected_are_separate_states(tmp_path):
    manager = manager_for(tmp_path, "import time; time.sleep(30)", restart_policy="never")
    task = asyncio.create_task(manager.start("probe"))
    starting = await wait_for_pid(manager)
    assert starting.process_alive
    assert not starting.connected
    assert starting.state == "starting"

    assert not manager.heartbeat("probe", instance_id="stale-instance", queue_depth=1)
    assert not manager.heartbeat("probe", instance_id=starting.instance_id, queue_depth=-1)
    assert manager.heartbeat("probe", instance_id=starting.instance_id, reported_state="starting")
    assert not manager.status("probe").connected
    assert manager.status("probe").state == "starting"
    assert manager.heartbeat("probe", instance_id=starting.instance_id, queue_depth=3, busy=True)
    connected = await task
    assert connected.connected and connected.process_alive
    assert connected.state == "busy"
    assert connected.queue_depth == 3

    assert manager.heartbeat("probe", instance_id=starting.instance_id, reported_state="disconnected")
    assert not manager.status("probe").connected
    assert manager.status("probe").state == "disconnected"

    runtime = manager._runtime["probe"]
    runtime.last_heartbeat_mono = time.monotonic() - 20
    degraded = manager.status("probe")
    assert degraded.process_alive
    assert not degraded.connected
    assert degraded.state == "degraded"

    runtime.last_heartbeat_mono = time.monotonic() - 31
    disconnected = manager.status("probe")
    assert disconnected.process_alive
    assert not disconnected.connected
    assert disconnected.state == "disconnected"
    assert disconnected.restart_count == 0  # A stale heartbeat alone is not a death signal.

    stopped = await manager.stop("probe")
    assert not stopped.process_alive
    assert stopped.state == "stopped"
    await manager.shutdown()


def test_heartbeat_requires_a_known_bot_and_bounded_queue_depth(tmp_path):
    manager = manager_for(tmp_path, "pass", restart_policy="never")
    assert not manager.heartbeat("missing", instance_id="anything")
    assert manager.status("probe").state == "stopped"
