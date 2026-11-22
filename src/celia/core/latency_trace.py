"""Persistent, low-overhead latency tracing for Célia.

This module is observability-only. It must never raise into the runtime path.
Set CELIA_LATENCY_TRACE_ENABLED=0 to disable all new persistent latency tracing.
"""

from __future__ import annotations

import contextvars
import inspect
import json
import os
import queue
import threading
import time
import uuid
from collections import OrderedDict, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_DEFAULT_MAX_TURNS = 512
_LLM_ROLE_OVERRIDE: contextvars.ContextVar[str] = contextvars.ContextVar("celia_latency_llm_role", default="")


def _truthy(name: str, default: str = "1") -> bool:
    return os.getenv(name, default).strip().lower() not in {"0", "false", "no", "off"}


def _clean(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "[DEPTH_LIMIT]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:400]
    if isinstance(value, dict):
        return {str(k)[:80]: _clean(v, depth=depth + 1) for k, v in list(value.items())[:80]}
    if isinstance(value, (list, tuple)):
        return [_clean(v, depth=depth + 1) for v in list(value)[:80]]
    return str(value)[:400]


class LatencyTraceRecorder:
    def __init__(self, max_turns: int = _DEFAULT_MAX_TURNS) -> None:
        self._lock = threading.RLock()
        self._max_turns = max(32, int(max_turns))
        self._turns: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._active_llm: dict[str, dict[str, Any]] = {}
        self._llm_history = deque(maxlen=2000)
        self._speech_correlation: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._slow_dumped: set[str] = set()
        self._file_day = ""
        self._file_handle = None
        self._io_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=8192)
        self._worker_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._stats_lock = threading.Lock()
        self._enqueue_count = 0
        self._enqueue_total_ns = 0
        self._enqueue_max_ns = 0
        self._write_count = 0
        self._write_total_ns = 0
        self._write_max_ns = 0
        self._gpu_count = 0
        self._gpu_total_ns = 0
        self._gpu_max_ns = 0
        self._dropped_records = 0
        self._gpu_initialized = False
        self._gpu_available = False
        self._pynvml = None

    @property
    def enabled(self) -> bool:
        return _truthy("CELIA_LATENCY_TRACE_ENABLED", "1")

    @property
    def slow_threshold_ms(self) -> int:
        try:
            return max(1000, int(os.getenv("CELIA_LATENCY_SLOW_TURN_MS", "10000")))
        except ValueError:
            return 10000

    @staticmethod
    def _base_dir() -> Path:
        project_root = Path(__file__).resolve().parents[3]
        configured = os.getenv("CELIA_LATENCY_TRACE_DIR", "").strip()
        return Path(configured or project_root / "var" / "latency-traces")

    def _ensure_file(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._file_handle is not None and self._file_day == day:
            return self._file_handle
        try:
            if self._file_handle is not None:
                self._file_handle.close()
        except Exception:
            pass
        target_dir = self._base_dir()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / f"celia-latency-{day}.jsonl"
        self._file_handle = target.open("a", encoding="utf-8", buffering=1)
        self._file_day = day
        return self._file_handle

    def _ensure_worker(self) -> None:
        if self._worker is not None and self._worker.is_alive():
            return
        with self._worker_lock:
            if self._worker is not None and self._worker.is_alive():
                return
            self._worker = threading.Thread(
                target=self._io_worker,
                name="celia-latency-trace",
                daemon=True,
            )
            self._worker.start()

    def _append_record(self, record: dict[str, Any]) -> None:
        """Enqueue persistence; never perform disk I/O on Célia's critical path."""
        if not self.enabled:
            return
        started_ns = time.perf_counter_ns()
        try:
            self._ensure_worker()
            self._io_queue.put_nowait({"op": "record", "record": dict(record)})
        except queue.Full:
            with self._stats_lock:
                self._dropped_records += 1
        except Exception:
            return
        finally:
            elapsed_ns = time.perf_counter_ns() - started_ns
            with self._stats_lock:
                self._enqueue_count += 1
                self._enqueue_total_ns += elapsed_ns
                self._enqueue_max_ns = max(self._enqueue_max_ns, elapsed_ns)

    def request_gpu_snapshot(self, turn_id: str, stage: str, **metadata: Any) -> None:
        """Queue an NVML sample so GPU telemetry never blocks the conversation/TTS path."""
        if not self.enabled:
            return
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            return
        requested_ns = time.monotonic_ns()
        self.mark(
            turn_id,
            "gpu_snapshot_requested",
            stage=str(stage),
            requested_monotonic_ns=requested_ns,
            gpu_measurement="async_pending",
            **metadata,
        )
        try:
            self._ensure_worker()
            self._io_queue.put_nowait({
                "op": "gpu",
                "turn_id": turn_id,
                "stage": str(stage),
                "requested_monotonic_ns": requested_ns,
                "metadata": dict(metadata),
            })
        except queue.Full:
            with self._stats_lock:
                self._dropped_records += 1
        except Exception:
            return

    def flush(self, timeout_sec: float = 2.0) -> bool:
        """Best-effort test/debug flush; runtime callers do not need this."""
        if not self.enabled:
            return True
        self._ensure_worker()
        deadline = time.monotonic() + max(0.05, float(timeout_sec))
        while self._io_queue.unfinished_tasks and time.monotonic() < deadline:
            time.sleep(0.005)
        return self._io_queue.unfinished_tasks == 0

    def overhead_snapshot(self) -> dict[str, Any]:
        with self._stats_lock:
            return {
                "enqueue_count": self._enqueue_count,
                "enqueue_avg_us": round(
                    (self._enqueue_total_ns / self._enqueue_count) / 1000, 3
                ) if self._enqueue_count else 0.0,
                "enqueue_max_us": round(self._enqueue_max_ns / 1000, 3),
                "write_count": self._write_count,
                "write_avg_us": round(
                    (self._write_total_ns / self._write_count) / 1000, 3
                ) if self._write_count else 0.0,
                "write_max_us": round(self._write_max_ns / 1000, 3),
                "gpu_count": self._gpu_count,
                "gpu_avg_us": round(
                    (self._gpu_total_ns / self._gpu_count) / 1000, 3
                ) if self._gpu_count else 0.0,
                "gpu_max_us": round(self._gpu_max_ns / 1000, 3),
                "dropped_records": self._dropped_records,
                "queue_depth": self._io_queue.qsize(),
            }

    def _write_record_sync(self, record: dict[str, Any]) -> None:
        started_ns = time.perf_counter_ns()
        handle = self._ensure_file()
        handle.write(json.dumps(_clean(record), ensure_ascii=False) + "\n")
        elapsed_ns = time.perf_counter_ns() - started_ns
        with self._stats_lock:
            self._write_count += 1
            self._write_total_ns += elapsed_ns
            self._write_max_ns = max(self._write_max_ns, elapsed_ns)
            count = self._write_count
        if count % 32 == 0:
            stats = {
                "type": "trace_overhead",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **self.overhead_snapshot(),
            }
            handle.write(json.dumps(_clean(stats), ensure_ascii=False) + "\n")

    def _io_worker(self) -> None:
        while True:
            item = self._io_queue.get()
            try:
                op = item.get("op")
                if op == "gpu":
                    started_ns = time.perf_counter_ns()
                    gpu = self._gpu_snapshot_sync()
                    gpu_elapsed_ns = time.perf_counter_ns() - started_ns
                    with self._stats_lock:
                        self._gpu_count += 1
                        self._gpu_total_ns += gpu_elapsed_ns
                        self._gpu_max_ns = max(self._gpu_max_ns, gpu_elapsed_ns)
                    sampled_ns = time.monotonic_ns()
                    event = {
                        "type": "latency_event",
                        "turn_id": item.get("turn_id", ""),
                        "event": "gpu_snapshot",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "monotonic_ns": sampled_ns,
                        "stage": item.get("stage", ""),
                        "requested_monotonic_ns": item.get("requested_monotonic_ns"),
                        "sample_delay_ms": round(
                            (sampled_ns - int(item.get("requested_monotonic_ns") or sampled_ns))
                            / 1_000_000,
                            3,
                        ),
                        **dict(item.get("metadata") or {}),
                        **gpu,
                    }
                    turn_id = str(item.get("turn_id") or "")
                    with self._lock:
                        turn = self._turns.get(turn_id)
                        if turn is not None:
                            turn["events"].append(_clean(event))
                    self._write_record_sync(event)
                    continue
                record = dict(item.get("record") or {})
                if record.get("type") == "slow_turn_dump":
                    turn_id = str(record.get("turn_id") or "")
                    with self._lock:
                        turn = self._turns.get(turn_id)
                        if turn is not None:
                            record["timeline"] = sorted(
                                list(turn.get("events", [])),
                                key=lambda row: int(row.get("monotonic_ns") or 0),
                            )
                self._write_record_sync(record)
            except Exception:
                pass
            finally:
                self._io_queue.task_done()

    def _turn(self, turn_id: str) -> dict[str, Any]:
        turn_id = str(turn_id or "").strip()
        state = self._turns.get(turn_id)
        if state is None:
            state = {
                "turn_id": turn_id,
                "created_monotonic_ns": time.monotonic_ns(),
                "events": [],
                "concurrency_samples": [],
            }
            self._turns[turn_id] = state
        self._turns.move_to_end(turn_id)
        while len(self._turns) > self._max_turns:
            old_id, _ = self._turns.popitem(last=False)
            self._slow_dumped.discard(old_id)
        return state

    def mark(self, turn_id: str, event: str, **metadata: Any) -> None:
        if not self.enabled:
            return
        turn_id = str(turn_id or "").strip()
        if not turn_id:
            return
        try:
            with self._lock:
                turn = self._turn(turn_id)
                mono_ns = time.monotonic_ns()
                wall = datetime.now(timezone.utc).isoformat()
                item = {
                    "type": "latency_event",
                    "turn_id": turn_id,
                    "event": str(event),
                    "timestamp": wall,
                    "monotonic_ns": mono_ns,
                    "elapsed_from_turn_record_ms": round(
                        (mono_ns - int(turn["created_monotonic_ns"])) / 1_000_000, 3
                    ),
                    **metadata,
                }
                turn["events"].append(_clean(item))
                self._append_record(item)
                if event == "audio_sent":
                    self._maybe_dump_slow_locked(turn_id)
        except Exception:
            return

    def snapshot_turn(self, turn_id: str) -> dict[str, Any]:
        with self._lock:
            turn = self._turns.get(str(turn_id or "").strip())
            if not turn:
                return {}
            return json.loads(json.dumps(_clean(turn), ensure_ascii=False))

    def _event_ns(self, turn: dict[str, Any], event_names: set[str]) -> int | None:
        for item in turn.get("events", []):
            if item.get("event") in event_names:
                try:
                    return int(item.get("monotonic_ns"))
                except Exception:
                    return None
        return None

    def _llm_overlap_snapshot(self, start_ns: int, end_ns: int) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for item in list(self._llm_history):
            try:
                item_start = int(item.get("started_monotonic_ns") or 0)
                item_end = int(item.get("ended_monotonic_ns") or item_start)
            except Exception:
                continue
            if item_start <= end_ns and item_end >= start_ns:
                rows.append(_clean(item))
        for item in self._active_llm.values():
            try:
                item_start = int(item.get("started_monotonic_ns") or 0)
            except Exception:
                continue
            if item_start <= end_ns:
                row = dict(item)
                row["ended_monotonic_ns"] = None
                row["still_active"] = True
                rows.append(_clean(row))
        return rows

    def _maybe_dump_slow_locked(self, turn_id: str) -> None:
        if turn_id in self._slow_dumped:
            return
        turn = self._turns.get(turn_id)
        if not turn:
            return
        start_ns = self._event_ns(turn, {"fim_fala", "mic_audio_end", "vad_end"})
        sent_ns = None
        for item in reversed(turn.get("events", [])):
            if item.get("event") == "audio_sent":
                try:
                    sent_ns = int(item.get("monotonic_ns"))
                except Exception:
                    pass
                break
        if start_ns is None or sent_ns is None:
            return
        elapsed_ms = (sent_ns - start_ns) / 1_000_000
        if elapsed_ms < self.slow_threshold_ms:
            return
        self._slow_dumped.add(turn_id)
        dump = {
            "type": "slow_turn_dump",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "turn_id": turn_id,
            "elapsed_fim_fala_to_audio_sent_ms": round(elapsed_ms, 3),
            "threshold_ms": self.slow_threshold_ms,
            "timeline": list(turn.get("events", [])),
            "concurrency_samples": list(turn.get("concurrency_samples", [])),
            "llm_calls_overlapping_turn": self._llm_overlap_snapshot(start_ns, sent_ns),
            "active_llm_now": self.active_llm_snapshot(),
            "trace_overhead": self.overhead_snapshot(),
        }
        self._append_record(dump)

    def current_trace_turn_id(self) -> str:
        try:
            from .observability import observability, _trace_state
            state = _trace_state.get()
            if state is None:
                return ""
            for item in reversed(observability.snapshot()):
                if item.get("trace_id") != state.trace_id:
                    continue
                turn_id = str((item.get("metadata") or {}).get("turn_id") or "").strip()
                if turn_id:
                    return turn_id
        except Exception:
            return ""
        return ""

    def push_llm_role(self, role: str):
        return _LLM_ROLE_OVERRIDE.set(str(role or ""))

    def reset_llm_role(self, token) -> None:
        try:
            _LLM_ROLE_OVERRIDE.reset(token)
        except Exception:
            pass

    def current_llm_role(self, default: str) -> str:
        return str(_LLM_ROLE_OVERRIDE.get() or default)

    def llm_begin(
        self,
        *,
        role: str,
        model: str,
        endpoint: str,
        turn_id: str = "",
        measurement: str = "sdk_stream",
    ) -> str:
        if not self.enabled:
            return ""
        call_id = uuid.uuid4().hex
        now_ns = time.monotonic_ns()
        resolved_turn = str(turn_id or self.current_trace_turn_id() or "").strip()
        with self._lock:
            others = self.active_llm_snapshot()
            record = {
                "call_id": call_id,
                "role": str(role or "unknown"),
                "model": str(model or ""),
                "endpoint": str(endpoint or ""),
                "turn_id": resolved_turn,
                "started_monotonic_ns": now_ns,
                "measurement": measurement,
            }
            self._active_llm[call_id] = record
            sample = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "llm_concurrency_at_start",
                "call": _clean(record),
                "other_calls": others,
            }
            if resolved_turn:
                self._turn(resolved_turn)["concurrency_samples"].append(_clean(sample))
                self.mark(
                    resolved_turn,
                    "llm_request_sent",
                    call_id=call_id,
                    role=record["role"],
                    model=record["model"],
                    endpoint=record["endpoint"],
                    concurrent_calls=others,
                    gateway_queue_ms=None,
                    gateway_queue_measurement="not_exposed_by_client_or_gateway",
                )
                self.request_gpu_snapshot(
                    resolved_turn,
                    "llm_start",
                    call_id=call_id,
                    role=record["role"],
                )
            else:
                self._append_record({"type": "llm_global_event", **sample})
        return call_id

    def llm_event(self, call_id: str, event: str, **metadata: Any) -> None:
        if not self.enabled or not call_id:
            return
        with self._lock:
            call = self._active_llm.get(call_id)
            if not call:
                return
            turn_id = str(call.get("turn_id") or "")
            if turn_id:
                self.mark(turn_id, event, call_id=call_id, role=call.get("role"), **metadata)
            else:
                self._append_record({
                    "type": "llm_global_event",
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "monotonic_ns": time.monotonic_ns(),
                    "event": event,
                    "call_id": call_id,
                    "role": call.get("role"),
                    **metadata,
                })

    def llm_end(self, call_id: str, **metadata: Any) -> None:
        if not self.enabled or not call_id:
            return
        with self._lock:
            call = self._active_llm.get(call_id)
            if not call:
                return
            ended_ns = time.monotonic_ns()
            elapsed_ms = round((ended_ns - int(call["started_monotonic_ns"])) / 1_000_000, 3)
            self.llm_event(call_id, "llm_call_end", duration_ms=elapsed_ms, **metadata)
            self._llm_history.append(_clean({
                **call,
                **metadata,
                "ended_monotonic_ns": ended_ns,
                "duration_ms": elapsed_ms,
            }))
            self._active_llm.pop(call_id, None)

    def register_speech_correlation(
        self, speech_id: str, turn_id: str, phrase_index: int | None
    ) -> None:
        speech_id = str(speech_id or "").strip()
        turn_id = str(turn_id or "").strip()
        if not speech_id or not turn_id:
            return
        with self._lock:
            self._speech_correlation[speech_id] = {
                "turn_id": turn_id,
                "phrase_index": phrase_index,
            }
            self._speech_correlation.move_to_end(speech_id)
            while len(self._speech_correlation) > 4096:
                self._speech_correlation.popitem(last=False)

    def speech_correlation(self, speech_id: str) -> dict[str, Any]:
        with self._lock:
            return dict(self._speech_correlation.get(str(speech_id or "").strip(), {}))

    def active_llm_snapshot(self) -> list[dict[str, Any]]:
        now_ns = time.monotonic_ns()
        with self._lock:
            result = []
            for call in self._active_llm.values():
                row = dict(call)
                row["age_ms"] = round((now_ns - int(call["started_monotonic_ns"])) / 1_000_000, 3)
                result.append(_clean(row))
            return result

    def profile_change(self, from_profile: str, to_profile: str) -> None:
        if not self.enabled or from_profile == to_profile:
            return
        try:
            frames = []
            for frame in inspect.stack(context=0)[2:8]:
                frames.append(f"{Path(frame.filename).name}:{frame.function}:{frame.lineno}")
            self._append_record({
                "type": "profile_change",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "monotonic_ns": time.monotonic_ns(),
                "from": from_profile,
                "to": to_profile,
                "caller": frames[0] if frames else "unknown",
                "stack": frames,
            })
        except Exception:
            return

    def _gpu_snapshot_sync(self) -> dict[str, Any]:
        try:
            if not self._gpu_initialized:
                self._gpu_initialized = True
                try:
                    import pynvml  # type: ignore
                    pynvml.nvmlInit()
                    self._pynvml = pynvml
                    self._gpu_available = True
                except Exception:
                    self._gpu_available = False
            if not self._gpu_available or self._pynvml is None:
                return {"gpu_measurement": "unavailable"}
            pynvml = self._pynvml
            handle = pynvml.nvmlDeviceGetHandleByIndex(0)
            mem = pynvml.nvmlDeviceGetMemoryInfo(handle)
            util = pynvml.nvmlDeviceGetUtilizationRates(handle)
            return {
                "gpu_measurement": "pynvml",
                "vram_used_mb": round(mem.used / 1024 / 1024, 1),
                "vram_free_mb": round(mem.free / 1024 / 1024, 1),
                "vram_total_mb": round(mem.total / 1024 / 1024, 1),
                "gpu_util_percent": int(util.gpu),
                "memory_util_percent": int(util.memory),
            }
        except Exception:
            return {"gpu_measurement": "unavailable"}


latency_trace = LatencyTraceRecorder()
