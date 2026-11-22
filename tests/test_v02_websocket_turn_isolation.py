import asyncio
import time

import pytest

from celia.celia_singleton import CeliaSingleton
from celia.conversations.conversation_handler import _submit_websocket_turn
from celia.core.conversation_event_queue import ConversationEventQueue
from celia.core.turn_arbiter import ScheduledTurn, TurnArbiter, VerifiedActor


@pytest.mark.asyncio
async def test_two_private_clients_and_bridge_generation_share_one_consumer(monkeypatch):
    queue = ConversationEventQueue()
    arbiter = TurnArbiter(queue, max_pending=8, max_receipts=16)

    class Singleton:
        _turn_arbiter = arbiter
        _processing = True

    monkeypatch.setattr(CeliaSingleton, "get_instance", lambda: Singleton())
    sent = {"client-a": [], "client-b": []}

    async def client_callback(client):
        sent[client].append(f"private:{client}")

    client_a = asyncio.create_task(_submit_websocket_turn("client-a", lambda: client_callback("client-a")))
    client_b = asyncio.create_task(_submit_websocket_turn("client-b", lambda: client_callback("client-b")))

    now = time.monotonic()
    actor = VerifiedActor("bridge:process", None, False, frozenset({"bridge:process"}))

    async def bridge_callback():
        return {"response": "bridge-result"}

    bridge_handle = await arbiter.submit(ScheduledTurn(
        "bridge-concurrent-01", "bridge", "bridge-session", 150, now, now + 5,
        {"kind": "bridge_process_generation"}, actor,
    ), executor=bridge_callback)
    claim = arbiter.claim_consumer()

    async def drain_three():
        result = None
        for _ in range(3):
            claimed = await arbiter.dequeue_group(claim)
            assert len(claimed) == 1
            item = claimed[0]
            result = await arbiter.execute_claimed(item.queue_event_id)
            arbiter.complete_queue_events([item.queue_event_id], result)
        return result

    try:
        bridge_result = await drain_three()
        await asyncio.gather(client_a, client_b)
        assert sent == {
            "client-a": ["private:client-a"],
            "client-b": ["private:client-b"],
        }
        assert await arbiter.wait_result(bridge_handle, 0.2) == {"response": "bridge-result"}
        assert bridge_result in (None, {"response": "bridge-result"})
    finally:
        for task in (client_a, client_b):
            if not task.done():
                task.cancel()
        arbiter.close()
