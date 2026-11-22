from __future__ import annotations

import asyncio
import json
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol

from ..bridge.schemas import BOT_ID_VALUES, BotId, EventReceiptV2
from .conversation_event_queue import ConversationEvent, ConversationEventQueue

MAX_SCHEDULED_PAYLOAD_BYTES = 1_000_000


@dataclass(frozen=True)
class VerifiedActor:
    """Identity produced only by a successful infrastructure authentication step.

    Never construct this from request payload fields. A bot process cannot be an
    owner; owner identity must come from a separate verified local/user channel.
    """

    actor_id: str
    bot_id: BotId | None
    is_owner: bool
    scopes: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.actor_id, str) or not 1 <= len(self.actor_id.strip()) <= 128:
            raise ValueError("actor_id must contain 1 to 128 characters")
        if self.bot_id is not None and self.bot_id not in BOT_ID_VALUES:
            raise ValueError("bot_id is not a registered Bridge identity")
        if not isinstance(self.is_owner, bool):
            raise TypeError("is_owner must come from verified authentication")
        if self.bot_id is not None and self.is_owner:
            raise ValueError("a bot identity cannot be marked as owner")
        if (
            not isinstance(self.scopes, frozenset)
            or len(self.scopes) > 64
            or any(not isinstance(scope, str) or len(scope) > 128 for scope in self.scopes)
        ):
            raise TypeError("scopes must be a bounded frozenset of verified scope names")


@dataclass(frozen=True)
class ScheduledTurn:
    """Internal immutable scheduling record; payload must remain JSON-only.

    Keep VerifiedActor, Future/callbacks, and credentials outside `payload`:
    ConversationEventQueue training traces capture payload contents.
    """

    event_id: str
    source: str
    session_id: str
    priority: int
    received_mono: float
    expires_mono: float
    payload: dict[str, Any]
    actor: VerifiedActor

    def __post_init__(self) -> None:
        if not 8 <= len(self.event_id) <= 128:
            raise ValueError("event_id length must be between 8 and 128")
        if not self.source.strip() or not self.session_id.strip():
            raise ValueError("source and session_id are required")
        if not isinstance(self.actor, VerifiedActor):
            raise TypeError("actor must be produced by the authentication layer")
        if isinstance(self.priority, bool) or not 0 <= self.priority <= 1000:
            raise ValueError("priority is outside the V02 range")
        if (
            not math.isfinite(self.received_mono)
            or not math.isfinite(self.expires_mono)
            or self.received_mono < 0
            or self.expires_mono <= self.received_mono
        ):
            raise ValueError("turn deadlines must be finite monotonic timestamps")
        forbidden_authority_fields = {
            "priority", "actor", "verified_actor", "trusted_actor", "scopes",
            "authorization_scope", "future", "callback",
        }
        if forbidden_authority_fields.intersection(self.payload):
            raise ValueError("authority, priority, and async handles belong outside payload")
        try:
            payload_size = len(json.dumps(self.payload, allow_nan=False).encode("utf-8"))
        except (TypeError, ValueError) as exc:
            raise TypeError("payload must contain JSON-compatible values only") from exc
        if payload_size > MAX_SCHEDULED_PAYLOAD_BYTES:
            raise ValueError("payload exceeds the bounded turn size")


@dataclass(frozen=True)
class ClaimedTurn:
    queue_event_id: str
    turn: ScheduledTurn


@dataclass
class TurnHandle:
    event_id: str
    future: asyncio.Future[Any]
    receipt: EventReceiptV2 | None = None


class TurnArbiterProtocol(Protocol):
    async def submit(self, event: ScheduledTurn) -> TurnHandle: ...

    async def wait_result(self, handle: TurnHandle, timeout_s: float) -> Any: ...

    async def cancel(self, event_id: str, reason: str) -> bool: ...

    def snapshot(self) -> dict[str, Any]: ...


@dataclass
class _ReceiptState:
    key: tuple[str, str]
    event_id: str
    actor_id: str
    future: asyncio.Future[Any] | None
    receipt: EventReceiptV2
    state: str
    queue_event_id: str | None
    dedupe_until_mono: float
    turn: ScheduledTurn | None
    executor: Callable[[], Awaitable[Any]] | None = None
    running_task: asyncio.Future[Any] | None = None


class TurnArbiter:
    """Bounded sidecar arbiter over the one injected ConversationEventQueue.

    This class does not create a queue or consumer. The eventual singleton worker
    must dequeue through `dequeue_group` and complete each claimed turn. Queue
    lifecycle callbacks only update sidecar receipts; none are sent through the
    training trace or stored in ConversationEvent payloads.
    """

    def __init__(
        self,
        queue: ConversationEventQueue,
        *,
        max_pending: int = 128,
        max_receipts: int = 1024,
        dedupe_retention_sec: float = 300.0,
    ) -> None:
        if not isinstance(queue, ConversationEventQueue):
            raise TypeError("TurnArbiter requires the existing ConversationEventQueue")
        if isinstance(max_pending, bool) or max_pending < 1:
            raise ValueError("max_pending must be positive")
        if isinstance(max_receipts, bool) or max_receipts < max_pending:
            raise ValueError("max_receipts must be >= max_pending")
        if not math.isfinite(dedupe_retention_sec) or dedupe_retention_sec < 1:
            raise ValueError("dedupe_retention_sec must be finite and >= 1 second")
        self._queue = queue
        self._max_pending = int(max_pending)
        self._max_receipts = int(max_receipts)
        self._dedupe_retention_sec = float(dedupe_retention_sec)
        self._records: OrderedDict[tuple[str, str], _ReceiptState] = OrderedDict()
        self._pending: dict[tuple[str, str], _ReceiptState] = {}
        self._queue_to_key: dict[str, tuple[str, str]] = {}
        self._submit_lock = asyncio.Lock()
        self._consumer_claim_taken = False
        self._dequeue_active = False
        self._observer_callback = self._on_queue_lifecycle
        queue.set_lifecycle_observer(self._observer_callback)
        self._consumer_token: object | None = None
        self._counters = {"queued": 0, "duplicate": 0, "expired": 0, "busy": 0, "rejected": 0, "completed": 0, "cancelled": 0}

    @staticmethod
    def _queue_source(actor: VerifiedActor) -> str | None:
        if actor.bot_id is not None and not actor.is_owner:
            return actor.bot_id
        if actor.bot_id is None and actor.is_owner:
            return "owner"
        return None

    @staticmethod
    def _receipt(event_id: str, status: str, reason: str | None = None) -> EventReceiptV2:
        accepted = status == "queued"
        return EventReceiptV2(event_id=event_id, accepted=accepted, status=status, reason=reason)

    @staticmethod
    def _future_with_result(result: Any) -> asyncio.Future[Any]:
        future = asyncio.get_running_loop().create_future()
        future.set_result(result)
        return future

    def _prune_records(self, now: float) -> None:
        # qsize purges expired events and notifies this arbiter's sidecar observer.
        self._queue.qsize()
        for key, record in list(self._records.items()):
            if record.state not in {"queued", "claimed"} and record.dedupe_until_mono <= now:
                self._records.pop(key, None)
        while len(self._records) > self._max_receipts:
            removable = next(
                (key for key, record in self._records.items() if record.state not in {"queued", "claimed"}),
                None,
            )
            if removable is None:
                break
            self._records.pop(removable, None)

    def _immediate_handle(
        self, event_id: str, status: str, reason: str | None = None
    ) -> TurnHandle:
        receipt = self._receipt(event_id, status, reason)
        return TurnHandle(event_id, self._future_with_result(receipt), receipt)

    def _store_terminal(
        self,
        key: tuple[str, str],
        event: ScheduledTurn,
        receipt: EventReceiptV2,
        *,
        state: str,
        now: float,
    ) -> TurnHandle | None:
        if len(self._records) >= self._max_receipts:
            self._prune_records(now)
        if len(self._records) >= self._max_receipts:
            return None
        future = self._future_with_result(receipt)
        record = _ReceiptState(
            key=key,
            event_id=event.event_id,
            actor_id=event.actor.actor_id,
            future=None,
            receipt=receipt,
            state=state,
            queue_event_id=None,
            dedupe_until_mono=now + self._dedupe_retention_sec,
            turn=None,
        )
        self._records[key] = record
        return TurnHandle(event.event_id, future, receipt)

    async def submit(
        self,
        event: ScheduledTurn,
        *,
        executor: Callable[[], Awaitable[Any]] | None = None,
    ) -> TurnHandle:
        from .event_priority import AdmissionError, legacy_queue_priority

        if not isinstance(event, ScheduledTurn):
            raise TypeError("event must be a ScheduledTurn built after authentication")
        now = time.monotonic()
        key = (event.actor.actor_id, event.event_id)
        async with self._submit_lock:
            self._prune_records(now)
            existing = self._records.get(key)
            if existing is not None:
                self._records.move_to_end(key)
                self._counters["duplicate"] += 1
                return self._immediate_handle(event.event_id, "duplicate", "actor_event_already_seen")

            expected_source = self._queue_source(event.actor)
            if (
                expected_source is None
                and event.actor.actor_id == "bridge:process"
                and event.actor.bot_id is None
                and not event.actor.is_owner
                and "bridge:process" in event.actor.scopes
                and event.source == "bridge"
            ):
                expected_source = "bridge"
            elif (
                expected_source is None
                and event.actor.actor_id.startswith("ws:")
                and event.actor.bot_id is None
                and not event.actor.is_owner
                and "ws:conversation" in event.actor.scopes
                and event.source == "websocket"
            ):
                expected_source = "websocket"
            if expected_source is None or event.source != expected_source:
                self._counters["rejected"] += 1
                receipt = self._receipt(event.event_id, "rejected", "source_must_come_from_verified_actor")
                return self._store_terminal(key, event, receipt, state="rejected", now=now) or self._immediate_handle(event.event_id, "busy", "receipt_capacity_full")
            if event.expires_mono <= now:
                self._counters["expired"] += 1
                receipt = self._receipt(event.event_id, "expired", "ttl_expired_before_enqueue")
                return self._store_terminal(key, event, receipt, state="expired", now=now) or self._immediate_handle(event.event_id, "busy", "receipt_capacity_full")

            try:
                queue_priority = legacy_queue_priority(event.priority)
            except AdmissionError as exc:
                self._counters["rejected"] += 1
                receipt = self._receipt(event.event_id, "rejected", getattr(exc, "code", "invalid_priority").value if hasattr(getattr(exc, "code", None), "value") else "invalid_priority")
                return self._store_terminal(key, event, receipt, state="rejected", now=now) or self._immediate_handle(event.event_id, "busy", "receipt_capacity_full")

            claimed_count = sum(record.state == "claimed" for record in self._pending.values())
            if len(self._pending) >= self._max_pending or self._queue.qsize() + claimed_count >= self._max_pending:
                self._counters["busy"] += 1
                return self._immediate_handle(event.event_id, "busy", "queue_capacity_reached")
            if len(self._records) >= self._max_receipts:
                self._prune_records(now)
            if len(self._records) >= self._max_receipts:
                self._counters["busy"] += 1
                return self._immediate_handle(event.event_id, "busy", "receipt_capacity_reached")

            remaining_sec = max(0.001, event.expires_mono - time.monotonic())
            queue_event = self._queue.put(
                event.payload,
                source=expected_source,
                speaker=(f"{event.session_id}:{event.event_id}" if executor else event.session_id),
                priority=queue_priority,
                ttl_sec=remaining_sec,
            )
            future = asyncio.get_running_loop().create_future()
            future.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
            receipt = self._receipt(event.event_id, "queued")
            record = _ReceiptState(
                key=key,
                event_id=event.event_id,
                actor_id=event.actor.actor_id,
                future=future,
                receipt=receipt,
                state="queued",
                queue_event_id=queue_event.event_id,
                dedupe_until_mono=event.expires_mono + self._dedupe_retention_sec,
                turn=event,
                executor=executor,
            )
            self._records[key] = record
            self._pending[key] = record
            self._queue_to_key[queue_event.event_id] = key
            self._counters["queued"] += 1
            return TurnHandle(event.event_id, future, receipt)

    def executor_for(self, queue_event_id: str) -> Callable[[], Awaitable[Any]] | None:
        key = self._queue_to_key.get(queue_event_id)
        record = self._records.get(key) if key is not None else None
        return record.executor if record is not None and record.state == "claimed" else None

    async def execute_claimed(self, queue_event_id: str) -> Any:
        key = self._queue_to_key.get(queue_event_id)
        record = self._records.get(key) if key is not None else None
        if record is None or record.state != "claimed" or record.executor is None:
            raise RuntimeError("claimed event has no in-memory executor")
        task = asyncio.ensure_future(record.executor())
        record.running_task = task
        try:
            return await task
        finally:
            record.running_task = None
            record.executor = None

    def claim_consumer(self) -> object:
        """Return the one opaque token P2's existing singleton worker must use."""
        if self._consumer_claim_taken:
            raise RuntimeError("turn arbiter already has its single consumer")
        self._consumer_token = self._queue.claim_single_consumer()
        self._consumer_claim_taken = True
        return self._consumer_token

    async def dequeue_group(
        self, consumer_claim: object, max_items: int = 5
    ) -> list[ClaimedTurn]:
        if consumer_claim is not self._consumer_token or not self._consumer_claim_taken:
            raise RuntimeError("dequeue requires the arbiter's single-consumer claim")
        if self._dequeue_active:
            raise RuntimeError("turn arbiter already has an active dequeue")
        self._dequeue_active = True
        try:
            queue_events = await self._queue.get_group(
                max_items, consumer_token=self._consumer_token
            )
            claimed: list[ClaimedTurn] = []
            for queue_event in queue_events:
                key = self._queue_to_key.get(queue_event.event_id)
                record = self._records.get(key) if key is not None else None
                if record is None or record.turn is None:
                    # A claimed event without a sidecar means a legacy writer bypassed
                    # the arbiter; do not fabricate actor/result data for it.
                    continue
                claimed.append(ClaimedTurn(queue_event.event_id, record.turn))
            return claimed
        finally:
            self._dequeue_active = False

    def _on_queue_lifecycle(self, event: ConversationEvent, state: str) -> None:
        key = self._queue_to_key.get(event.event_id)
        if key is None:
            return
        record = self._records.get(key)
        if record is None:
            self._queue_to_key.pop(event.event_id, None)
            return
        if state == "claimed":
            if record.state == "queued":
                record.state = "claimed"
            return

        self._queue_to_key.pop(event.event_id, None)
        self._pending.pop(key, None)
        record.state = "expired" if state == "expired" else "rejected"
        status = "expired" if state == "expired" else "rejected"
        reason = {
            "expired": "ttl_expired_in_queue",
            "superseded": "superseded_before_execution",
            "cleared": "queue_cleared_before_execution",
            "cancelled": "cancelled_before_execution",
        }.get(state, "queue_removed_before_execution")
        receipt = self._receipt(record.event_id, status, reason)
        record.receipt = receipt
        record.dedupe_until_mono = time.monotonic() + self._dedupe_retention_sec
        record.turn = None
        record.executor = None
        self._counters[status] += 1
        if record.future is not None and not record.future.done():
            record.future.set_result(receipt)
        record.future = None

    def complete(
        self,
        actor_id: str,
        event_id: str,
        result: Any = None,
        *,
        error: BaseException | None = None,
    ) -> bool:
        """Complete one claimed future; callers pass the verified actor sidecar key."""
        key = (actor_id, event_id)
        record = self._records.get(key)
        if record is None or record.state != "claimed" or record.future is None:
            return False
        if error is None:
            record.future.set_result(result)
        else:
            record.future.set_exception(error)
        record.future = None
        record.turn = None
        record.executor = None
        record.state = "completed"
        record.dedupe_until_mono = time.monotonic() + self._dedupe_retention_sec
        if record.queue_event_id is not None:
            self._queue_to_key.pop(record.queue_event_id, None)
        record.queue_event_id = None
        self._pending.pop(key, None)
        self._counters["completed"] += 1
        return True

    def complete_queue_events(self, queue_event_ids: list[str], result: Any = None) -> int:
        """Resolve sidecars for claimed queue events after the existing worker finishes."""
        completed = 0
        for queue_event_id in queue_event_ids:
            key = self._queue_to_key.get(queue_event_id)
            record = self._records.get(key) if key is not None else None
            if record is not None and self.complete(record.actor_id, record.event_id, result):
                completed += 1
        return completed

    async def cancel_for_actor(self, actor_id: str, event_id: str, reason: str) -> bool:
        key = (actor_id, event_id)
        async with self._submit_lock:
            record = self._records.get(key)
            if record is None:
                return False
            if record.state == "claimed" and record.running_task is not None:
                record.running_task.cancel()
                return True
            if record.state != "queued" or record.queue_event_id is None:
                return False
            # The queue observer resolves the sidecar future and records the reason.
            removed = self._queue.remove(record.queue_event_id, state="cancelled")
            if removed:
                self._counters["cancelled"] += 1
            return removed

    async def cancel(self, event_id: str, reason: str) -> bool:
        matches = [
            record for record in self._pending.values()
            if record.event_id == event_id and record.state == "queued"
        ]
        if len(matches) != 1:
            return False
        return await self.cancel_for_actor(matches[0].actor_id, event_id, reason)

    async def wait_result(self, handle: TurnHandle, timeout_s: float) -> Any:
        if not math.isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be finite and positive")
        return await asyncio.wait_for(asyncio.shield(handle.future), timeout_s)

    def snapshot(self) -> dict[str, Any]:
        queue_snapshot = self._queue.snapshot()
        return {
            "queue": queue_snapshot,
            "pending": sum(record.state in {"queued", "claimed"} for record in self._pending.values()),
            "claimed": sum(record.state == "claimed" for record in self._pending.values()),
            "receipts": len(self._records),
            "limits": {"pending": self._max_pending, "receipts": self._max_receipts},
            "counters": dict(self._counters),
        }

    def close(self) -> None:
        """Release the observer and consumer claim during isolated test/shutdown."""
        self._queue.clear_lifecycle_observer(self._observer_callback)
        if self._consumer_token is not None:
            self._queue.release_single_consumer(self._consumer_token)
