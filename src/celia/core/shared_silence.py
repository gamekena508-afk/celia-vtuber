"""One global silence clock for the conversation (plan V2 §5, bug #1: "the timer started speech when Criador was talking again").

Silence is counted from the END of the last voice of EITHER party and only while nobody is talking, synthesising or playing audio. The START
of any voice resets it. A "started" event that never gets its "ended" (a lost websocket frame) cannot freeze the clock forever: every
activity flag expires after ``max_active_seconds``.
"""
from __future__ import annotations

import time
from typing import Callable

PARTIES = ("human", "celia_playback", "celia_generating")


class SharedSilenceClock:
    def __init__(self, clock: Callable[[], float] = time.monotonic, *, max_active_seconds: float = 120.0) -> None:
        self._clock = clock
        self._max_active = max_active_seconds
        self._active_since: dict[str, float | None] = {name: None for name in PARTIES}
        self._last_end: float | None = None
        self._last_voice_start: float | None = None

    def reset(self) -> None:
        """New session / profile change: nobody has spoken yet, and the counter starts now."""
        for name in PARTIES:
            self._active_since[name] = None
        self._last_end = self._clock()
        self._last_voice_start = None

    def _begin(self, name: str) -> None:
        now = self._clock()
        self._active_since[name] = now
        self._last_voice_start = now

    def _end(self, name: str) -> None:
        was_active = self._active_since[name] is not None
        self._active_since[name] = None
        now = self._clock()
        # an "end" always moves the last-end forward (a final transcript can arrive without a matching start), never backward
        self._last_end = now if self._last_end is None else max(self._last_end, now)
        if not was_active and self._last_voice_start is None:
            self._last_voice_start = now

    def human_started(self) -> None:
        self._begin("human")

    def human_ended(self) -> None:
        self._end("human")

    def playback_started(self) -> None:
        self._begin("celia_playback")

    def playback_ended(self) -> None:
        self._end("celia_playback")

    def generating_started(self) -> None:
        self._begin("celia_generating")

    def generating_ended(self) -> None:
        self._end("celia_generating")

    def _expire(self) -> None:
        now = self._clock()
        for name, since in self._active_since.items():
            if since is not None and now - since > self._max_active:
                self._active_since[name] = None
                self._last_end = max(self._last_end or 0.0, since + self._max_active)

    def active(self) -> list[str]:
        self._expire()
        return [name for name, since in self._active_since.items() if since is not None]

    def silent_for(self, ignore: tuple[str, ...] = ()) -> float:
        """Seconds of real shared silence: 0.0 while anyone is active (or nothing was ever heard and the clock was never started).

        ``ignore`` names parties that must not count as activity. The re-check done right before a line is played ignores
        ``celia_generating`` (that generation is the very line being checked); it never ignores the human.
        """
        self._expire()
        if any(since is not None for name, since in self._active_since.items() if name not in ignore) or self._last_end is None:
            return 0.0
        return max(0.0, self._clock() - self._last_end)

    def seconds_until(self, needed: float) -> float:
        """How long until ``needed`` seconds of silence would be reached if nobody speaks from now on (inf while someone is active)."""
        self._expire()
        if any(since is not None for since in self._active_since.values()) or self._last_end is None:
            return float("inf")
        return max(0.0, needed - (self._clock() - self._last_end))

    def snapshot(self) -> dict:
        return {"active": self.active(), "silent_for": round(self.silent_for(), 2)}
