import time
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from celia.core.conversation_event_queue import ConversationEventQueue
from celia.core.turn_arbiter import ScheduledTurn, TurnArbiter, VerifiedActor


@pytest.mark.asyncio
async def test_bridge_generation_callback_uses_arbiter_sidecar_and_existing_consumer():
    queue = ConversationEventQueue()
    arbiter = TurnArbiter(queue, max_pending=4, max_receipts=8)
    actor = VerifiedActor("bridge:process", None, False, frozenset({"bridge:process"}))
    now = time.monotonic()
    event = ScheduledTurn(
        "bridge-process-0001", "bridge", "fixture-session", 150, now, now + 5,
        {"kind": "bridge_process_generation"}, actor,
    )
    callback_data = {"text": "private fixture text"}

    async def execute_generation():
        return {"response": callback_data["text"]}

    try:
        handle = await arbiter.submit(event, executor=execute_generation)
        claim = arbiter.claim_consumer()
        claimed = await arbiter.dequeue_group(claim)
        assert len(claimed) == 1
        assert "text" not in claimed[0].turn.payload
        result = await arbiter.execute_claimed(claimed[0].queue_event_id)
        assert arbiter.complete_queue_events([claimed[0].queue_event_id], result) == 1
        assert await arbiter.wait_result(handle, 0.2) == {"response": "private fixture text"}
    finally:
        arbiter.close()


def test_bridge_process_route_returns_agent_result_from_singleton_queue(monkeypatch):
    import celia.bridge_routes as bridge
    from celia.agent.output_types import Actions, DisplayText, SentenceOutput

    queue = ConversationEventQueue()
    arbiter = TurnArbiter(queue, max_pending=8, max_receipts=16)

    class Singleton:
        def __init__(self):
            self._turn_arbiter = arbiter
            self._event_queue = queue
            self._processing = False
            self._turn_consumer_claim = None
            self.context = None

        async def _trigger_processing(self):
            if self._processing:
                return
            self._processing = True

            async def consume():
                try:
                    claim = self._turn_arbiter.claim_consumer()
                    while self._event_queue.qsize():
                        events = await self._turn_arbiter.dequeue_group(claim)
                        for event in events:
                            result = await self._turn_arbiter.execute_claimed(event.queue_event_id)
                            self._turn_arbiter.complete_queue_events([event.queue_event_id], result)
                finally:
                    self._processing = False

            asyncio.create_task(consume())

        async def broadcast(self, _payload):
            return None

    singleton = Singleton()

    class Agent:
        async def chat(self, _batch):
            yield SentenceOutput(DisplayText("Fixture reply"), "Fixture reply", Actions())

    async def accepted_guard(_request):
        return None, "accepted", "fixture"

    async def no_live_comment(*_args, **_kwargs):
        return None

    context = SimpleNamespace(agent_engine=Agent())
    monkeypatch.setattr(bridge.CeliaSingleton, "get_instance", lambda: singleton)
    monkeypatch.setattr(bridge, "_guardar_entrada", accepted_guard)
    monkeypatch.setattr(bridge, "_bridge_emotion_context", lambda _text: ("", "neutral"))
    monkeypatch.setattr(bridge, "read_live_comment", no_live_comment)
    app = FastAPI()
    app.include_router(bridge.init_bridge_routes(context))
    try:
        response = TestClient(app).post(
            "/bridge/process",
            json={"text": "fixture input", "fonte": "chat", "synthesize_audio": False},
        )
        assert response.status_code == 200
        assert response.json()["response"] == "Fixture reply"
        assert response.json()["error"] is None
    finally:
        arbiter.close()
