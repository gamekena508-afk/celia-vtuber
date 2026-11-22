"""Single cooperative GPU lane for Celia.

No polling and no preemption: once a job starts, it finishes. Queued priority is
brain > tts > explicit vision > background vision. Background keeps only the newest pending job.
"""
from __future__ import annotations

import asyncio
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from loguru import logger

from .latency_trace import latency_trace

JobFn = Callable[[], Awaitable[Any]]


def _truthy(name: str, default: str = "0") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class GpuJob:
    kind: str
    fn: JobFn
    generation: int = 0
    turn_id: str = ""
    submitted_at: float = field(default_factory=time.perf_counter)
    future: asyncio.Future[Any] | None = None
    wait_ms: float = 0.0


class GpuScheduler:
    """One-lane async scheduler for callers that explicitly opt in."""

    def __init__(self) -> None:
        self._brain: deque[GpuJob] = deque()
        self._tts: deque[GpuJob] = deque()
        self._vision_requests: deque[GpuJob] = deque()
        self._vision: GpuJob | None = None
        self._min_generation = {"brain": 0, "tts": 0, "vision": 0, "vision_request": 0}
        self._wake = asyncio.Event()
        self._runner: asyncio.Task[None] | None = None
        self.running: str | None = None
        self.metrics = {
            "submitted_brain": 0,
            "submitted_tts": 0,
            "submitted_vision": 0,
            "submitted_vision_request": 0,
            "vision_replaced": 0,
            "stale_dropped": 0,
            "shadow_skipped_busy": 0,
        }

    @property
    def enabled(self) -> bool:
        return _truthy("CELIA_GPU_SCHEDULER", "0")

    def pending_count(self) -> int:
        return len(self._brain) + len(self._tts) + len(self._vision_requests) + (1 if self._vision else 0)

    def is_idle(self) -> bool:
        return self.running is None and self.pending_count() == 0

    def ensure_started(self) -> None:
        if self._runner is not None and not self._runner.done():
            return
        loop = asyncio.get_running_loop()
        self._runner = loop.create_task(self.run(), name="celia-gpu-scheduler")

    def submit(
        self,
        kind: str,
        fn: JobFn,
        *,
        generation: int = 0,
        turn_id: str = "",
    ) -> asyncio.Future[Any]:
        if kind not in {"brain", "tts", "vision", "vision_request"}:
            raise ValueError(f"gpu job kind invalido: {kind}")
        self.ensure_started()
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        job = GpuJob(
            kind=kind,
            fn=fn,
            generation=max(0, int(generation)),
            turn_id=str(turn_id or ""),
            future=future,
        )
        self.metrics[f"submitted_{kind}"] += 1
        if kind == "vision":
            previous = self._vision
            if previous is not None and previous.future is not None and not previous.future.done():
                previous.future.cancel()
                self.metrics["vision_replaced"] += 1
            self._vision = job
        elif kind == "brain":
            self._brain.append(job)
        elif kind == "vision_request":
            # A user-requested reference comparison is not a disposable frame.
            self._vision_requests.append(job)
        else:
            self._tts.append(job)
        self._wake.set()
        return future

    def submit_shadow_if_idle(
        self,
        fn: JobFn,
        *,
        turn_id: str = "",
    ) -> asyncio.Future[Any] | None:
        """Shadow is disposable and never queues behind production work."""
        if not self.is_idle():
            self.metrics["shadow_skipped_busy"] += 1
            return None
        return self.submit("brain", fn, turn_id=turn_id)

    async def run_job(
        self,
        kind: str,
        fn: JobFn,
        *,
        generation: int = 0,
        turn_id: str = "",
    ) -> Any:
        if not self.enabled:
            return await fn()
        return await self.submit(
            kind, fn, generation=generation, turn_id=turn_id
        )

    def invalidate(self, kind: str, new_min_generation: int) -> None:
        if kind not in self._min_generation:
            raise ValueError(f"gpu job kind invalido: {kind}")
        self._min_generation[kind] = max(
            self._min_generation[kind], int(new_min_generation)
        )

    def _next(self) -> GpuJob | None:
        if self._brain:
            return self._brain.popleft()
        if self._tts:
            return self._tts.popleft()
        if self._vision_requests:
            return self._vision_requests.popleft()
        if self._vision is not None:
            job, self._vision = self._vision, None
            return job
        return None

    async def run(self) -> None:
        while True:
            job = self._next()
            if job is None:
                self._wake.clear()
                # Avoid a lost wake if a submit raced with clear().
                if self.pending_count():
                    self._wake.set()
                    continue
                await self._wake.wait()
                continue
            future = job.future
            if future is None or future.cancelled():
                continue
            if job.generation < self._min_generation[job.kind]:
                self.metrics["stale_dropped"] += 1
                future.cancel()
                continue
            job.wait_ms = (time.perf_counter() - job.submitted_at) * 1000.0
            self.running = job.kind
            if job.turn_id:
                latency_trace.mark(
                    job.turn_id,
                    "gpu_wait",
                    gpu_kind=job.kind,
                    wait_ms=round(job.wait_ms, 3),
                )
            try:
                result = await job.fn()
                if not future.done():
                    future.set_result(result)
            except asyncio.CancelledError:
                if not future.done():
                    future.cancel()
                raise
            except Exception as exc:
                logger.warning(
                    "GPU scheduler job falhou kind={} error={}",
                    job.kind,
                    type(exc).__name__,
                )
                if not future.done():
                    future.set_exception(exc)
            finally:
                self.running = None

    def debug_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "running": self.running,
            "pending": {
                "brain": len(self._brain),
                "tts": len(self._tts),
                "vision_request": len(self._vision_requests),
                "vision": bool(self._vision),
            },
            "min_generation": dict(self._min_generation),
            "metrics": dict(self.metrics),
        }


gpu_scheduler = GpuScheduler()
