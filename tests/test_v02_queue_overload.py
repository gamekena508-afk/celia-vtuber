import time

import pytest

from celia.core.conversation_event_queue import ConversationEventQueue
from celia.core.turn_arbiter import ScheduledTurn, TurnArbiter, VerifiedActor


def make_turn(event_id):
    now = time.monotonic()
    actor = VerifiedActor("twitch-fixture", "twitch", False, frozenset({"events:live_chat"}))
    return ScheduledTurn(event_id, "twitch", "fixture", 150, now, now + 5, {"text": "fixture"}, actor)


@pytest.mark.asyncio
async def test_queue_limit_rejects_extra_event_and_cancel_resolves_receipt():
    queue = ConversationEventQueue()
    arbiter = TurnArbiter(queue, max_pending=1, max_receipts=8)
    try:
        first = await arbiter.submit(make_turn("overload-0001"))
        second = await arbiter.submit(make_turn("overload-0002"))
        assert first.receipt.status == "queued"
        assert second.receipt.status == "busy"
        assert await arbiter.cancel_for_actor("twitch-fixture", "overload-0001", "test")
        receipt = await arbiter.wait_result(first, 0.2)
        assert receipt.status == "rejected"
        assert receipt.reason == "cancelled_before_execution"
        assert queue.snapshot()["pending"] == 0
    finally:
        arbiter.close()
