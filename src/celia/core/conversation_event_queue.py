from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass
from typing import Any, Callable

from .training_trace import training_trace


_OWNER_SOURCES = {"owner", "microphone", "browser"}


@dataclass
class ConversationEvent:
    event_id: str
    payload: dict[str, Any]
    source: str
    speaker: str
    priority: int
    created_at: float
    expires_at: float | None
    conversation_revision: int
    supersede_key: str = ""

    def expired(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return False
        return (now or time.monotonic()) >= self.expires_at


class ConversationEventQueue:
    """Single in-RAM conversational queue with TTL and supersede support."""

    def __init__(self) -> None:
        self._items: list[ConversationEvent] = []
        self._wake = asyncio.Event()
        self.expired_count = 0
        self.superseded_count = 0
        self._lifecycle_observer: Callable[[ConversationEvent, str], None] | None = None
        self._single_consumer_token: object | None = None
        self._single_consumer_active = False

    def set_lifecycle_observer(
        self, observer: Callable[[ConversationEvent, str], None]
    ) -> None:
        """Attach the one sidecar observer used by the injected turn arbiter."""
        if self._lifecycle_observer is not None and self._lifecycle_observer is not observer:
            raise RuntimeError("conversation queue already has a lifecycle observer")
        self._lifecycle_observer = observer

    def clear_lifecycle_observer(
        self, observer: Callable[[ConversationEvent, str], None]
    ) -> None:
        if self._lifecycle_observer is observer:
            self._lifecycle_observer = None

    def claim_single_consumer(self) -> object:
        """Reserve dequeue ownership before P2 wires the singleton worker."""
        if self._single_consumer_token is not None:
            raise RuntimeError("conversation queue already has a single consumer")
        self._single_consumer_token = object()
        return self._single_consumer_token

    def release_single_consumer(self, token: object) -> None:
        if token is not self._single_consumer_token:
            raise RuntimeError("invalid conversation queue consumer claim")
        if self._single_consumer_active:
            raise RuntimeError("cannot release an active conversation queue consumer")
        self._single_consumer_token = None

    def _notify_lifecycle(self, event: ConversationEvent, state: str) -> None:
        observer = self._lifecycle_observer
        if observer is not None:
            observer(event, state)

    def qsize(self) -> int:
        self._purge_expired()
        return len(self._items)

    def clear(self) -> int:
        removed = list(self._items)
        self._items.clear()
        self._wake.clear()
        for event in removed:
            self._notify_lifecycle(event, "cleared")
        return len(removed)

    def _purge_expired(self) -> None:
        now = time.monotonic()
        expired = [item for item in self._items if item.expired(now)]
        expired_ids = {item.event_id for item in expired}
        self._items = [item for item in self._items if item.event_id not in expired_ids]
        self.expired_count += len(expired)
        for event in expired:
            self._notify_lifecycle(event, "expired")
        if not self._items:
            self._wake.clear()

    def remove(self, event_id: str, *, state: str = "cancelled") -> bool:
        """Remove one queued item and report its lifecycle outside trace payloads."""
        self._purge_expired()
        for index, event in enumerate(self._items):
            if event.event_id == event_id:
                self._items.pop(index)
                if not self._items:
                    self._wake.clear()
                self._notify_lifecycle(event, state)
                return True
        return False

    def put(
        self,
        payload: dict[str, Any],
        *,
        source: str,
        speaker: str = "",
        priority: int = 50,
        ttl_sec: float | None = None,
        conversation_revision: int = 0,
        supersede_key: str = "",
    ) -> ConversationEvent:
        self._purge_expired()
        if supersede_key:
            before = len(self._items)
            superseded = [item for item in self._items if item.supersede_key == supersede_key]
            self._items = [
                item for item in self._items
                if item.supersede_key != supersede_key
            ]
            self.superseded_count += before - len(self._items)
            for item in superseded:
                self._notify_lifecycle(item, "superseded")
        now = time.monotonic()
        event = ConversationEvent(
            event_id=uuid.uuid4().hex,
            payload=payload,
            source=(source or "unknown").strip().lower(),
            speaker=(speaker or "").strip(),
            priority=max(0, min(100, int(priority))),
            created_at=now,
            expires_at=(now + float(ttl_sec)) if ttl_sec else None,
            conversation_revision=max(0, int(conversation_revision)),
            supersede_key=(supersede_key or "").strip(),
        )
        # Stable priority insertion: owner/urgent work can pass stale low-value
        # proactive events, while equal-priority dialogue preserves chronology.
        insert_at = len(self._items)
        for idx, queued in enumerate(self._items):
            if event.priority > queued.priority:
                insert_at = idx
                break
        self._items.insert(insert_at, event)
        self._wake.set()
        training_trace.capture(
            "orchestrator_event",
            {
                "event_id": event.event_id,
                "payload": payload,
                "source": event.source,
                "speaker": event.speaker,
                "priority": event.priority,
                "conversation_revision": event.conversation_revision,
                "supersede_key": event.supersede_key,
            },
            layer="orchestrator",
        )
        return event

    @staticmethod
    def _compatible(first: ConversationEvent, other: ConversationEvent) -> bool:
        if first.source in _OWNER_SOURCES and other.source in _OWNER_SOURCES:
            if first.speaker and other.speaker and first.speaker != other.speaker:
                return False
            # Distinct owner turns must stay distinct. Without this guard,
            # several quick utterances can be merged into one LLM prompt
            # (up to BATCH_SIZE), making follow-ups look like one malformed
            # request. A single ASR/text turn already carries its own turn_id.
            first_turn = str(first.payload.get("turn_id") or "").strip()
            other_turn = str(other.payload.get("turn_id") or "").strip()
            if first_turn and other_turn:
                return first_turn == other_turn
            return True
        return first.source == other.source and first.speaker == other.speaker

    async def get_group(
        self, max_items: int = 5, *, consumer_token: object | None = None
    ) -> list[ConversationEvent]:
        """Return one group; once claimed, only the claimed singleton may dequeue."""
        if self._single_consumer_token is not None:
            if consumer_token is not self._single_consumer_token:
                raise RuntimeError("dequeue requires the registered single-consumer claim")
            if self._single_consumer_active:
                raise RuntimeError("conversation queue already has an active consumer")
            self._single_consumer_active = True
        elif consumer_token is not None:
            raise RuntimeError("consumer token was not registered on this queue")

        try:
            while True:
                self._purge_expired()
                if self._items:
                    break
                await self._wake.wait()
            first = self._items.pop(0)
            group = [first]
            self._notify_lifecycle(first, "claimed")
            while self._items and len(group) < max(1, int(max_items)):
                candidate = self._items[0]
                if not self._compatible(first, candidate):
                    break
                group.append(self._items.pop(0))
                self._notify_lifecycle(group[-1], "claimed")
            if not self._items:
                self._wake.clear()
            return group
        finally:
            if self._single_consumer_token is not None and consumer_token is self._single_consumer_token:
                self._single_consumer_active = False

    def snapshot(self) -> dict[str, Any]:
        self._purge_expired()
        return {
            "pending": len(self._items),
            "expired": self.expired_count,
            "superseded": self.superseded_count,
            "sources": [item.source for item in self._items[:8]],
            "priorities": [item.priority for item in self._items[:8]],
        }
