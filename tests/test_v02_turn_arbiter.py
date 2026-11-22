import asyncio
import time

import pytest

from celia.core.conversation_event_queue import ConversationEventQueue
from celia.core.turn_arbiter import ScheduledTurn, TurnArbiter, VerifiedActor


@pytest.fixture
def arbiter():
    queue = ConversationEventQueue()
    value = TurnArbiter(queue, max_pending=1, max_receipts=8)
    yield value
    value.close()


def turn(event_id: str, *, actor_id: str = "bot-session", expires: float | None = None):
    actor = VerifiedActor(actor_id, "twitch", False, frozenset({"events:live_chat"}))
    now = time.monotonic()
    received = min(now, (expires - 0.01) if expires is not None else now)
    return ScheduledTurn(
        event_id=event_id,
        source="twitch",
        session_id="stream-1",
        priority=150,
        received_mono=received,
        expires_mono=expires or now + 5,
        payload={"text": "hello"},
        actor=actor,
    )


@pytest.mark.asyncio
async def test_duplicate_is_deduplicated_by_actor_and_event(arbiter):
    first = await arbiter.submit(turn("event-0001"))
    duplicate = await arbiter.submit(turn("event-0001"))
    assert first.receipt.status == "queued"
    assert duplicate.receipt.status == "duplicate"
    assert arbiter.snapshot()["queue"]["pending"] == 1


@pytest.mark.asyncio
async def test_overload_returns_busy_without_adding_queue_item(arbiter):
    await arbiter.submit(turn("event-0001"))
    overloaded = await arbiter.submit(turn("event-0002"))
    assert overloaded.receipt.status == "busy"
    assert arbiter.snapshot()["queue"]["pending"] == 1


@pytest.mark.asyncio
async def test_expired_turn_and_cancel_resolve_receipts(arbiter):
    expired = await arbiter.submit(turn("event-0001", expires=time.monotonic() - 1))
    assert expired.receipt.status == "expired"
    queued = await arbiter.submit(turn("event-0002"))
    assert await arbiter.cancel_for_actor("bot-session", "event-0002", "test")
    result = await arbiter.wait_result(queued, 0.2)
    assert result.status == "rejected"
    assert result.reason == "cancelled_before_execution"


@pytest.mark.asyncio
async def test_timeout_does_not_cancel_future_and_claim_completes_it(arbiter):
    handle = await arbiter.submit(turn("event-0001"))
    with pytest.raises(asyncio.TimeoutError):
        await arbiter.wait_result(handle, 0.001)
    assert not handle.future.cancelled()
    claim = arbiter.claim_consumer()
    batch = await arbiter.dequeue_group(claim)
    assert [item.turn.event_id for item in batch] == ["event-0001"]
    assert arbiter.complete("bot-session", "event-0001", {"ok": True})
    assert await arbiter.wait_result(handle, 0.2) == {"ok": True}


@pytest.mark.asyncio
async def test_queue_claim_blocks_a_second_consumer(arbiter):
    await arbiter.submit(turn("event-0001"))
    claim = arbiter.claim_consumer()
    with pytest.raises(RuntimeError):
        await arbiter.dequeue_group(object())
    batch = await arbiter.dequeue_group(claim)
    assert len(batch) == 1


@pytest.mark.asyncio
async def test_executor_stays_in_sidecar_and_completes_through_same_queue(arbiter):
    actor = VerifiedActor("bridge:process", None, False, frozenset({"bridge:process"}))
    now = time.monotonic()
    event = ScheduledTurn(
        event_id="process-0001",
        source="bridge",
        session_id="bridge:process:1",
        priority=150,
        received_mono=now,
        expires_mono=now + 5,
        payload={"kind": "bridge_process_generation"},
        actor=actor,
    )

    async def generation():
        return {"response": "fixture"}

    handle = await arbiter.submit(event, executor=generation)
    claim = arbiter.claim_consumer()
    batch = await arbiter.dequeue_group(claim)
    assert len(batch) == 1
    assert "response" not in batch[0].turn.payload
    assert await arbiter.execute_claimed(batch[0].queue_event_id) == {"response": "fixture"}
    assert arbiter.complete_queue_events([batch[0].queue_event_id], {"response": "fixture"}) == 1
    assert await arbiter.wait_result(handle, 0.2) == {"response": "fixture"}


@pytest.mark.asyncio
async def test_cancel_active_executor_stops_its_task():
    queue = ConversationEventQueue()
    arb = TurnArbiter(queue, max_pending=2, max_receipts=8)
    actor = VerifiedActor("bridge:process", None, False, frozenset({"bridge:process"}))
    started = asyncio.Event()
    now = time.monotonic()

    async def slow_generation():
        started.set()
        await asyncio.Event().wait()

    event = ScheduledTurn(
        "bridge-process-0002", "bridge", "fixture", 150, now, now + 5,
        {"kind": "bridge_process_generation"}, actor,
    )
    try:
        handle = await arb.submit(event, executor=slow_generation)
        claim = arb.claim_consumer()
        claimed = await arb.dequeue_group(claim)
        running = asyncio.create_task(arb.execute_claimed(claimed[0].queue_event_id))
        await started.wait()
        assert await arb.cancel_for_actor(actor.actor_id, event.event_id, "test_interrupt")
        with pytest.raises(asyncio.CancelledError):
            await running
        arb.complete_queue_events([claimed[0].queue_event_id], {"status": "cancelled"})
        assert await arb.wait_result(handle, 0.2) == {"status": "cancelled"}
    finally:
        arb.close()

