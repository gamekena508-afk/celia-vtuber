"""Bounded, content-safe execution tracing for the local Célia observatory."""

from __future__ import annotations

import asyncio
import contextvars
import functools
import re
import threading
import time
import uuid
from collections import deque
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, Optional, TypeVar


_MAX_EVENTS = 2_000
_MAX_METADATA_VALUE_LENGTH = 96
_SAFE_METADATA_KEYS = {
    "bank_resultado", "bank_ativo_id", "audio_reused", "tts_avoided",
    "latency_saved_ms", "bank_playback",
    "entrypoint",
    "source",
    "input_kind",
    "input_length",
    "image_count",
    "output_length",
    "ttft_ms",
    "chars",
    "output_kind",
    "tool_name",
    "tool_status",
    "history_role",
    "audio_requested",
    "audio_generated",
    "tts_engine",
    "error_type",
    "reason",
    "guardian_verdict",
    "guardian_category",
    "source_declared",
    "policy_mode",
    "request_id",
    "route",
    "final_effect",
    "policy_mode",
    "scene_profile",
    "intent",
    "response_goal",
    "current_session_occurrences",
    "repetition_evidence",
    "turn_id",
    "utterance_id",
    "speech_id",
    # Tool-routing latency: structural metadata only, never prompt/tool payload content.
    "primary_intent",
    "secondary_intents",
    "has_tool",
    "public_external",
    "input_tool_count",
    "filtered_tool_count",
    "filtered_tool_names",
    "visual",
    "model",
    "message_count",
    "message_chars",
    "system_chars",
    "tool_count",
    "tool_names",
    "tool_schema_chars",
    "payload_shape_sha256_16",
    "key_slot",
    "key_slots_total",
    "first_event_ms",
    "event_type",
    # Eventos ambientes (visão, pensamento, roteador): só escalares estruturais.
    "width", "height", "relevance", "provider", "role", "count",
    "should_speak", "calls_window", "calls_max", "active_sources",
}
_NODE_RE = re.compile(r"[^a-zA-Z0-9_.:-]+")


def _safe_node_id(value: str) -> str:
    normalized = _NODE_RE.sub("-", str(value or "unknown").strip()).strip("-.")
    return normalized[:80] or "unknown"


def _safe_metadata(metadata: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Keep only small, allowlisted scalar metadata; never expose payload content."""
    safe: Dict[str, Any] = {}
    for key, value in (metadata or {}).items():
        if key not in _SAFE_METADATA_KEYS:
            continue
        if isinstance(value, bool):
            safe[key] = value
        elif isinstance(value, int):
            safe[key] = max(-1_000_000_000, min(value, 1_000_000_000))
        elif isinstance(value, float):
            safe[key] = round(value, 3)
        elif isinstance(value, str):
            safe[key] = value[:_MAX_METADATA_VALUE_LENGTH]
    return safe


@dataclass
class _TraceState:
    trace_id: str
    root_span_id: str
    root_started_at: float
    root_node_id: str
    root_finished: bool = False


_trace_state: contextvars.ContextVar[Optional[_TraceState]] = contextvars.ContextVar(
    "celia_observability_trace", default=None
)
_active_span_id: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "celia_observability_span", default=None
)


class Span(AbstractContextManager["Span"]):
    def __init__(
        self,
        recorder: "ObservabilityRecorder",
        node_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._recorder = recorder
        self.node_id = _safe_node_id(node_id)
        self.metadata = _safe_metadata(metadata)
        self.span_id = uuid.uuid4().hex
        self._started_at = time.perf_counter()
        self._token = None
        self._finished = False

    def __enter__(self) -> "Span":
        self._recorder.emit("span_start", self.node_id, "running", self.metadata, self.span_id)
        self._token = _active_span_id.set(self.span_id)
        return self

    def finish(self, status: str = "ok", metadata: Optional[Dict[str, Any]] = None) -> None:
        if self._finished:
            return
        self._finished = True
        duration_ms = int((time.perf_counter() - self._started_at) * 1000)
        merged = dict(self.metadata)
        merged.update(_safe_metadata(metadata))
        if self._token is not None:
            _active_span_id.reset(self._token)
            self._token = None
        self._recorder.emit(
            "span_end",
            self.node_id,
            status,
            merged,
            self.span_id,
            duration_ms=duration_ms,
        )

    def __exit__(self, exc_type, exc, _traceback) -> None:
        if exc_type is asyncio.CancelledError:
            self.finish("cancelled", {"reason": "cancelled"})
        elif exc_type is not None:
            self.finish("error", {"error_type": exc_type.__name__})
        else:
            self.finish()


class Trace(AbstractContextManager["Trace"]):
    def __init__(
        self, recorder: "ObservabilityRecorder", entrypoint: str, metadata: Optional[Dict[str, Any]]) -> None:
        self._recorder = recorder
        self.entrypoint = _safe_node_id(entrypoint)
        self.metadata = _safe_metadata(metadata)
        self.state = _TraceState(
            trace_id=uuid.uuid4().hex,
            root_span_id=uuid.uuid4().hex,
            root_started_at=time.perf_counter(),
            root_node_id="celia.core",
        )
        self._trace_token = None
        self._span_token = None

    def __enter__(self) -> "Trace":
        self._trace_token = _trace_state.set(self.state)
        self._span_token = _active_span_id.set(self.state.root_span_id)
        root_metadata = dict(self.metadata)
        root_metadata["entrypoint"] = self.entrypoint
        self._recorder.emit("trace_start", self.state.root_node_id, "running", root_metadata, self.state.root_span_id)
        return self

    def span(self, node_id: str, metadata: Optional[Dict[str, Any]] = None) -> Span:
        return Span(self._recorder, node_id, metadata)

    def finish(self, status: str = "ok", metadata: Optional[Dict[str, Any]] = None) -> None:
        if self.state.root_finished:
            return
        self.state.root_finished = True
        duration_ms = int((time.perf_counter() - self.state.root_started_at) * 1000)
        self._recorder.emit("trace_end", self.state.root_node_id, status, metadata, self.state.root_span_id, duration_ms)
        if self._span_token is not None:
            _active_span_id.reset(self._span_token)
            self._span_token = None
        if self._trace_token is not None:
            _trace_state.reset(self._trace_token)
            self._trace_token = None

    def __exit__(self, exc_type, exc, _traceback) -> None:
        if exc_type is asyncio.CancelledError:
            self.finish("cancelled", {"reason": "cancelled"})
        elif exc_type is not None:
            self.finish("error", {"error_type": exc_type.__name__})
        else:
            self.finish()


class ObservabilityRecorder:
    """A process-local event ring. Tracing must never interrupt Célia's work."""

    def __init__(self, max_events: int = _MAX_EVENTS) -> None:
        self._events: Deque[Dict[str, Any]] = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._sequence = 0

    def trace(self, entrypoint: str, metadata: Optional[Dict[str, Any]] = None) -> Trace:
        return Trace(self, entrypoint, metadata)

    def span(self, node_id: str, metadata: Optional[Dict[str, Any]] = None) -> Span:
        return Span(self, node_id, metadata)

    def emit(
        self,
        kind: str,
        node_id: str,
        status: str = "ok",
        metadata: Optional[Dict[str, Any]] = None,
        span_id: Optional[str] = None,
        duration_ms: Optional[int] = None,
    ) -> Optional[Dict[str, Any]]:
        state = _trace_state.get()
        if state is None:
            return None
        try:
            with self._lock:
                self._sequence += 1
                event = {
                    "sequence": self._sequence,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": state.trace_id,
                    "span_id": span_id or uuid.uuid4().hex,
                    "parent_span_id": _active_span_id.get(),
                    "kind": _safe_node_id(kind),
                    "node_id": _safe_node_id(node_id),
                    "status": _safe_node_id(status),
                    "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
                    "metadata": _safe_metadata(metadata),
                }
                if event["span_id"] == event["parent_span_id"]:
                    event["parent_span_id"] = None
                self._events.append(event)
                return dict(event)
        except Exception:
            return None

    def emit_ambient(
        self,
        node_id: str,
        status: str = "ok",
        metadata: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
        kind: str = "pulse",
    ) -> Optional[Dict[str, Any]]:
        """Evento que não pertence a um turno (visão, pensamento, roteador).

        `emit()` descarta tudo fora de um trace. Estes processos rodam em
        segundo plano, então usam o trace fixo "ambient". Mesma allowlist de
        metadados: nunca conteúdo de prompt, imagem ou texto.
        """
        try:
            with self._lock:
                self._sequence += 1
                event = {
                    "sequence": self._sequence,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": "ambient",
                    "span_id": uuid.uuid4().hex,
                    "parent_span_id": None,
                    "kind": _safe_node_id(kind),
                    "node_id": _safe_node_id(node_id),
                    "status": _safe_node_id(status),
                    "duration_ms": max(0, int(duration_ms)) if duration_ms is not None else None,
                    "metadata": _safe_metadata(metadata),
                }
                self._events.append(event)
                return dict(event)
        except Exception:
            return None

    def record_tool_status(self, tool_name: Any, status: Any) -> None:
        normalized_status = str(status or "unknown").lower()
        safe_status = normalized_status if normalized_status in {"running", "complete", "completed", "error", "calling"} else "unknown"
        self.emit(
            "tool_status",
            f"tool.{_safe_node_id(str(tool_name or 'unknown'))}",
            "error" if safe_status == "error" else safe_status,
            {"tool_name": str(tool_name or "unknown"), "tool_status": safe_status},
        )

    def record_error(self, node_id: str, exc: BaseException) -> None:
        self.emit("error", node_id, "error", {"error_type": type(exc).__name__})

    def snapshot(self, after_sequence: int = 0) -> list[Dict[str, Any]]:
        with self._lock:
            return [dict(event) for event in self._events if event["sequence"] > after_sequence]

    def latest_sequence(self) -> int:
        with self._lock:
            return self._sequence

    def clear(self) -> None:
        with self._lock:
            self._events.clear()
            self._sequence = 0


observability = ObservabilityRecorder()
F = TypeVar("F", bound=Callable[..., Any])


def observed_async_turn(entrypoint: str) -> Callable[[F], F]:
    """Wrap an async endpoint/turn so each actual invocation owns a trace."""
    def decorate(function: F) -> F:
        @functools.wraps(function)
        async def wrapped(*args, **kwargs):
            with observability.trace(entrypoint):
                return await function(*args, **kwargs)
        return wrapped  # type: ignore[return-value]
    return decorate
