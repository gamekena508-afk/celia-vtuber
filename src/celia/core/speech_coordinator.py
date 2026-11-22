"""Central arbitration for every Celia speech producer.

The coordinator does not synthesize or play audio.  It owns only speech intent:
who may speak, whether an intent is still valid, deduplication, and cancellation.
That lets direct conversation, game commentary and ambient producers share one
authority without coupling their generation stacks.
"""

from __future__ import annotations

from dataclasses import dataclass
import threading
import time
import uuid
from typing import Any


_SOURCE_PRIORITY = {
    "direct_user": 400,
    "discord_direct": 400,
    "critical_game_event": 300,
    "system": 250,
    "bridge": 220,
    "game_commentary": 200,
    "game": 200,
    "live": 150,
    "live_mode": 150,
    "ambient": 100,
}


@dataclass
class SpeechTicket:
    id: str
    source: str
    priority: int
    created_at: float
    expires_at: float
    context_revision: int | None = None
    event_key: str = ""
    take_id: str = ""
    match_id: str = ""
    board_revision: int | None = None
    interruptible: bool = True
    # Audio of an owner reply (Criador's submitted turn). Its obsolescence is
    # decided by continuous_behavior.turn_revision, not by any human speech:
    # ROUTING_CONTEXT_ONLY speech must not cancel it (measured 2026-10-02 12:33:47).
    owner_reply: bool = False
    status: str = "pending"
    reason: str = ""
    speech_id: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "priority": self.priority,
            "status": self.status,
            "reason": self.reason,
            "event_key": self.event_key,
            "take_id": self.take_id,
            "match_id": self.match_id,
            "board_revision": self.board_revision,
            "speech_id": self.speech_id,
        }


class SpeechCoordinator:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tickets: dict[str, SpeechTicket] = {}
        self._speech_to_ticket: dict[str, str] = {}
        self._event_keys: dict[str, float] = {}
        self._human_floor_until = 0.0

    @staticmethod
    def source_priority(source: str, explicit: int | None = None) -> int:
        if explicit is not None:
            return int(explicit)
        return _SOURCE_PRIORITY.get(str(source or "bridge").strip().lower(), 220)

    def _expire(self, now: float) -> None:
        for ticket in self._tickets.values():
            if ticket.status in {"pending", "generated", "dispatched"} and now >= ticket.expires_at:
                ticket.status = "expired"
                ticket.reason = "ttl_expired"
        for key, expiry in list(self._event_keys.items()):
            if now >= expiry:
                self._event_keys.pop(key, None)

    def request(
        self,
        *,
        source: str,
        ttl_sec: float = 8.0,
        event_key: str = "",
        take_id: str = "",
        match_id: str = "",
        board_revision: int | None = None,
        context_revision: int | None = None,
        priority: int | None = None,
        interruptible: bool = True,
        owner_reply: bool = False,
    ) -> SpeechTicket | None:
        now = time.monotonic()
        normalized_source = str(source or "bridge").strip().lower() or "bridge"
        normalized_event = str(event_key or "").strip()
        with self._lock:
            self._expire(now)
            if normalized_event and normalized_event in self._event_keys:
                return None
            if (
                normalized_source in {
                    "game_commentary",
                    "ambient",
                    "critical_game_event",
                    "game",
                    "live",
                    "live_mode",
                }
                and now < self._human_floor_until
            ):
                return None

            rank = self.source_priority(normalized_source, priority)
            # A direct reply supersedes queued low-priority chatter before it can
            # finish generation and reappear later.
            if normalized_source in {"direct_user", "discord_direct"}:
                self._cancel_pending_locked(
                    lambda item: item.priority < rank,
                    "superseded_by_direct_user",
                )

            ticket = SpeechTicket(
                id=uuid.uuid4().hex,
                source=normalized_source,
                priority=rank,
                created_at=now,
                expires_at=now + max(0.25, float(ttl_sec or 0.25)),
                context_revision=context_revision,
                event_key=normalized_event,
                take_id=str(take_id or ""),
                match_id=str(match_id or ""),
                board_revision=board_revision,
                interruptible=bool(interruptible),
                owner_reply=bool(owner_reply),
            )
            self._tickets[ticket.id] = ticket
            if normalized_event:
                # Event dedupe outlives playback a little so one event cannot
                # produce two audible lines through racing producers.
                self._event_keys[normalized_event] = ticket.expires_at + 20.0
            self._trim_locked()
            return ticket

    def _cancel_pending_locked(self, predicate, reason: str) -> int:
        cancelled = 0
        for item in self._tickets.values():
            if item.status in {"pending", "generated", "dispatched"} and predicate(item):
                item.status = "cancelled"
                item.reason = reason
                cancelled += 1
        return cancelled

    def cancel_pending(self, *, source: str | None = None, reason: str = "cancelled") -> int:
        with self._lock:
            normalized = str(source or "").strip().lower()
            return self._cancel_pending_locked(
                lambda item: not normalized or item.source == normalized,
                reason,
            )

    def owner_stop(self) -> list[str]:
        """Apply owner STOP without entering an input queue.

        The websocket/command handler must authenticate the owner before calling
        this internal arbiter method. Active speech IDs are returned for player
        cancellation; authentication data is never taken from the speech source.
        """
        active_speech_ids: list[str] = []
        with self._lock:
            self._expire(time.monotonic())
            for item in self._tickets.values():
                if item.status in {"pending", "generated"}:
                    item.status = "cancelled"
                    item.reason = "owner_stop"
                elif item.status in {"dispatched", "playing", "cancel_requested"}:
                    item.status = "cancel_requested"
                    item.reason = "owner_stop"
                    if item.speech_id:
                        active_speech_ids.append(item.speech_id)
        return active_speech_ids

    def human_started(self) -> str | None:
        """Invalidate stale speech when Criador starts a new utterance.

        Pending replies/comments are cancelled immediately.  Active game/ambient
        playback is safe to cut before transcript classification because those
        sources are explicitly disposable.  Active direct conversation remains
        under the existing interruption classifier, so a backchannel does not
        cut a normal answer.
        """
        with self._lock:
            now = time.monotonic()
            self._expire(now)
            self._human_floor_until = max(self._human_floor_until, now + 20.0)
            self._cancel_pending_locked(
                lambda item: (
                    item.status != "playing"
                    and item.source in {
                        "game_commentary",
                        "ambient",
                        "critical_game_event",
                    }
                ),
                "human_started_new_utterance",
            )
            active: SpeechTicket | None = None
            for item in self._tickets.values():
                if item.status == "playing" and item.interruptible:
                    if active is None or item.created_at > active.created_at:
                        active = item
            if active and active.source in {"game_commentary", "ambient"}:
                active.status = "cancel_requested"
                active.reason = "human_started_over_spontaneous_speech"
                return active.speech_id or None
            return None

    def human_committed(self) -> int:
        """Cancel stale queued speech once a real human turn is confirmed."""
        with self._lock:
            now = time.monotonic()
            self._expire(now)
            self._human_floor_until = max(self._human_floor_until, now + 20.0)
            # Owner replies are excluded: a new submitted turn invalidates them
            # through turn_revision; context-only speech must leave them alone.
            return self._cancel_pending_locked(
                lambda item: item.interruptible and not item.owner_reply,
                "human_turn_committed",
            )

    def human_committed_playback(self) -> str | None:
        """Request cancellation of lower-priority playback after a real human turn."""
        with self._lock:
            active: SpeechTicket | None = None
            direct_rank = self.source_priority("direct_user")
            for item in self._tickets.values():
                if (
                    item.status == "playing"
                    and item.interruptible
                    and item.priority < direct_rank
                ):
                    if active is None or item.created_at > active.created_at:
                        active = item
            if active is None:
                return None
            active.status = "cancel_requested"
            active.reason = "human_turn_committed"
            return active.speech_id or None

    def valid(
        self,
        ticket_id: str,
        *,
        context_revision: int | None = None,
        match_id: str | None = None,
        board_revision: int | None = None,
        take_id: str | None = None,
    ) -> bool:
        now = time.monotonic()
        with self._lock:
            self._expire(now)
            item = self._tickets.get(str(ticket_id or ""))
            if item is None or item.status in {"cancelled", "cancel_requested", "expired", "finished"}:
                return False
            if context_revision is not None and item.context_revision is not None:
                if int(context_revision) != int(item.context_revision):
                    item.status, item.reason = "cancelled", "context_revision_changed"
                    return False
            if match_id is not None and item.match_id and str(match_id) != item.match_id:
                item.status, item.reason = "cancelled", "match_changed"
                return False
            if board_revision is not None and item.board_revision is not None:
                if int(board_revision) != int(item.board_revision):
                    item.status, item.reason = "cancelled", "board_revision_changed"
                    return False
            if take_id is not None and item.take_id and str(take_id) != item.take_id:
                item.status, item.reason = "cancelled", "take_changed"
                return False
            return True

    def mark_generated(self, ticket_id: str) -> None:
        self._mark(ticket_id, "generated")

    def bind_speech(self, ticket_id: str, speech_id: str) -> None:
        with self._lock:
            item = self._tickets.get(str(ticket_id or ""))
            if item is None:
                return
            item.speech_id = str(speech_id or "")
            item.status = "dispatched"
            if item.speech_id:
                self._speech_to_ticket[item.speech_id] = item.id

    def mark_playing(self, speech_id: str) -> None:
        with self._lock:
            item = self._ticket_for_speech_locked(speech_id)
            if item is not None and item.status not in {"cancelled", "expired", "finished"}:
                item.status = "playing"

    def mark_finished(self, speech_id: str, status: str = "finished") -> None:
        with self._lock:
            item = self._ticket_for_speech_locked(speech_id)
            if item is None:
                return
            item.status = (
                "cancelled"
                if str(status or "").strip().lower()
                in {"cancelled", "canceled", "disconnected", "error", "failed"}
                else "finished"
            )
            item.reason = str(status or "")
            if item.source == "direct_user":
                self._human_floor_until = 0.0

    def cancel_ticket(self, ticket_id: str, reason: str = "cancelled") -> None:
        with self._lock:
            item = self._tickets.get(str(ticket_id or ""))
            if item is None or item.status == "finished":
                return
            item.status = "cancelled"
            item.reason = reason

    def _mark(self, ticket_id: str, status: str) -> None:
        with self._lock:
            item = self._tickets.get(str(ticket_id or ""))
            if item is not None and item.status not in {"cancelled", "expired", "finished"}:
                item.status = status

    def _ticket_for_speech_locked(self, speech_id: str) -> SpeechTicket | None:
        ticket_id = self._speech_to_ticket.get(str(speech_id or ""))
        return self._tickets.get(ticket_id) if ticket_id else None

    def ticket_for_speech(self, speech_id: str) -> dict[str, Any] | None:
        with self._lock:
            item = self._ticket_for_speech_locked(speech_id)
            return item.public() if item else None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            self._expire(time.monotonic())
            active = [
                item.public()
                for item in self._tickets.values()
                if item.status in {"pending", "generated", "dispatched", "playing", "cancel_requested"}
            ]
            active.sort(key=lambda item: item["priority"], reverse=True)
            return {"active": active[-32:]}

    def reset_for_tests(self) -> None:
        with self._lock:
            self._tickets.clear()
            self._speech_to_ticket.clear()
            self._event_keys.clear()
            self._human_floor_until = 0.0

    def _trim_locked(self) -> None:
        if len(self._tickets) <= 512:
            return
        ordered = sorted(self._tickets.values(), key=lambda item: item.created_at)
        for item in ordered[:-256]:
            if item.status not in {"playing", "cancel_requested"}:
                self._tickets.pop(item.id, None)
                if item.speech_id:
                    self._speech_to_ticket.pop(item.speech_id, None)


speech_coordinator = SpeechCoordinator()
