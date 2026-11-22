"""Small, content-safe LLM call telemetry for the local Observatory."""

from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from typing import Any


class LLMCallTelemetry:
    def __init__(self, limit: int = 100) -> None:
        self._items: deque[dict[str, Any]] = deque(maxlen=limit)
        self._lock = threading.Lock()

    def record(self, *, model: str, mode: str, latency_ms: int, success: bool,
               fallback_used: bool, tool_used: bool, output_chars: int = 0,
               error_type: str | None = None) -> None:
        item = {
            "request_id": uuid.uuid4().hex,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "tool" if mode == "tool" else "conversation",
            "model": str(model)[:120],
            "latency_ms": max(0, int(latency_ms)),
            "tokens_in": "NOT AVAILABLE",
            "tokens_out_estimate": max(0, int(output_chars) // 4),
            "tool_used": bool(tool_used),
            "success": bool(success),
            "error": (str(error_type)[:80] if error_type else None),
            "fallback_used": bool(fallback_used),
        }
        with self._lock:
            self._items.append(item)

    def safe_snapshot(self) -> dict[str, Any]:
        with self._lock:
            entries = list(self._items)[-12:]
        return {"available": True, "recent_calls": entries, "count": len(entries)}


llm_telemetry = LLMCallTelemetry()
