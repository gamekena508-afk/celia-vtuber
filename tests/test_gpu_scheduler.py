import asyncio

import pytest

from celia.core.gpu_scheduler import GpuScheduler


@pytest.mark.asyncio
async def test_gpu_scheduler_priority_after_current_job(monkeypatch):
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    sched = GpuScheduler()
    order = []
    release = asyncio.Event()

    async def first():
        order.append("vision-start")
        await release.wait()
        order.append("vision-end")
        return "v"

    async def brain():
        order.append("brain")
        return "b"

    async def tts():
        order.append("tts")
        return "t"

    vision_f = sched.submit("vision", first)
    await asyncio.sleep(0)
    brain_f = sched.submit("brain", brain)
    tts_f = sched.submit("tts", tts)
    release.set()

    assert await vision_f == "v"
    assert await brain_f == "b"
    assert await tts_f == "t"
    assert order == ["vision-start", "vision-end", "brain", "tts"]

    sched._runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sched._runner


@pytest.mark.asyncio
async def test_vision_pending_slot_keeps_only_newest(monkeypatch):
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    sched = GpuScheduler()
    hold = asyncio.Event()

    async def blocker():
        await hold.wait()
        return "brain"

    blocker_f = sched.submit("brain", blocker)
    await asyncio.sleep(0)

    async def old():
        return "old"

    async def new():
        return "new"

    old_f = sched.submit("vision", old)
    new_f = sched.submit("vision", new)
    assert old_f.cancelled()

    hold.set()
    assert await blocker_f == "brain"
    assert await new_f == "new"
    assert sched.metrics["vision_replaced"] == 1

    sched._runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sched._runner


@pytest.mark.asyncio
async def test_shadow_is_skipped_when_busy(monkeypatch):
    monkeypatch.setenv("CELIA_GPU_SCHEDULER", "1")
    sched = GpuScheduler()
    hold = asyncio.Event()

    async def blocker():
        await hold.wait()

    fut = sched.submit("brain", blocker)
    await asyncio.sleep(0)
    assert sched.submit_shadow_if_idle(lambda: blocker()) is None
    assert sched.metrics["shadow_skipped_busy"] == 1

    hold.set()
    await fut
    sched._runner.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sched._runner


@pytest.mark.asyncio
async def test_explicit_identity_survives_background_replacement_and_invalidation(monkeypatch):
    monkeypatch.setenv('CELIA_GPU_SCHEDULER', '1')
    sched = GpuScheduler()
    hold = asyncio.Event()
    order = []

    async def blocker():
        await hold.wait()

    async def job(label):
        order.append(label)
        return label

    running = sched.submit('brain', blocker)
    await asyncio.sleep(0)
    explicit = sched.submit('vision_request', lambda: job('identity'))
    old = sched.submit('vision', lambda: job('old'), generation=5)
    sched.invalidate('vision', 6)
    newest = sched.submit('vision', lambda: job('new'), generation=6)
    speech = sched.submit('tts', lambda: job('speech'))
    brain = sched.submit('brain', lambda: job('brain'))
    assert old.cancelled() and not explicit.cancelled()
    hold.set()
    try:
        await asyncio.gather(running, explicit, newest, speech, brain)
        assert order == ['brain', 'speech', 'identity', 'new']
    finally:
        sched._runner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sched._runner
