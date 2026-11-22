"""Local, opt-in-ish training traces for turning real Célia sessions into datasets.

Unlike observability.py, this recorder may keep bounded conversational content.
It therefore redacts obvious secrets and never stores image/base64 payloads.
The recorder must never interrupt the runtime if writing fails.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import threading
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA_VERSION = 1
_MAX_TEXT = 6000
_SECRET_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+|bearer\s+|api[_-]?key\s*[=:]\s*|"
    r"password\s*[=:]\s*|token\s*[=:]\s*|cookie\s*[=:]\s*)([^\s,;]+)"
)
_LONG_SECRET_RE = re.compile(r"\b[A-Za-z0-9_\-]{40,}\b")
_DROP_KEYS = {"data", "base64", "image", "image_data", "audio_data", "bytes"}

def _enabled() -> bool:
    value = os.getenv("CELIA_TRAINING_TRACE_ENABLED", "1").strip().lower()
    return value in {"1", "true", "yes", "on", "sim"}


def _redact_text(value: Any) -> str:
    text = str(value or "")[:_MAX_TEXT]
    text = _SECRET_RE.sub(lambda m: m.group(1) + "[REDACTED]", text)
    # Long opaque strings are usually credentials/hashes. Keep known hashes only
    # when explicitly labelled elsewhere; free text does not need them verbatim.
    text = _LONG_SECRET_RE.sub("[OPAQUE_REDACTED]", text)
    return text


def _sanitize(value: Any, *, key: str = "", depth: int = 0) -> Any:
    if depth > 5:
        return "[DEPTH_LIMIT]"
    if key.lower() in _DROP_KEYS:
        return "[BINARY_OMITTED]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        lowered = key.lower()
        if "sha256" in lowered or lowered.endswith("_hash") or lowered == "record_hash":
            return str(value)[:160]
        return _redact_text(value)
    if isinstance(value, dict):
        return {
            str(k)[:80]: _sanitize(v, key=str(k), depth=depth + 1)
            for k, v in list(value.items())[:80]
        }
    if isinstance(value, (list, tuple)):
        return [_sanitize(v, depth=depth + 1) for v in list(value)[:80]]
    return _redact_text(repr(value))

def _current_trace_id() -> str:
    try:
        from .observability import _trace_state  # local internal bridge, no payload access
        state = _trace_state.get()
        return str(state.trace_id) if state is not None else ""
    except Exception:
        return ""


class TrainingTraceRecorder:
    """Append-only JSONL recorder designed for later offline curation/export."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        project_root = Path(__file__).resolve().parents[3]
        configured = os.getenv("CELIA_TRAINING_TRACE_DIR", "").strip()
        self.base_dir = Path(base_dir or configured or project_root / "var" / "training-traces")
        self._lock = threading.Lock()
        self._sequence = 0

    def _path(self) -> Path:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return self.base_dir / f"celia-training-{day}.jsonl"

    def capture(
        self,
        event_type: str,
        payload: dict[str, Any] | None = None,
        *,
        layer: str,
        turn_id: str = "",
        source: str = "runtime",
        trainable: bool = True,
        speech_id: str = "",
        tool_id: str = "",
    ) -> None:
        if not _enabled():
            return
        try:
            safe_payload = _sanitize(payload or {})
            with self._lock:
                self._sequence += 1
                record = {
                    "schema_version": _SCHEMA_VERSION,
                    "sequence": self._sequence,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "trace_id": _current_trace_id(),
                    "turn_id": _redact_text(turn_id)[:120],
                    "speech_id": _redact_text(speech_id)[:120],
                    "tool_id": _redact_text(tool_id)[:160],
                    "layer": _redact_text(layer)[:48],
                    "event_type": _redact_text(event_type)[:64],
                    "source": _redact_text(source)[:64],
                    "trainable": bool(trainable),
                    "payload": safe_payload,
                }
                canonical = json.dumps(record, ensure_ascii=False, sort_keys=True)
                record["record_hash"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:20]
                path = self._path()
                path.parent.mkdir(parents=True, exist_ok=True)
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            # Training capture is secondary. Never break conversation/tool execution.
            return

    def capture_turn_input(self, texts: list[Any], *, turn_id: str = "", metadata: Any = None) -> None:
        compact = []
        for item in texts or []:
            compact.append({
                "source": getattr(getattr(item, "source", None), "value", str(getattr(item, "source", ""))),
                "from_name": getattr(item, "from_name", None),
                "content": getattr(item, "content", ""),
            })
        self.capture("turn_input", {"texts": compact, "metadata": metadata or {}}, layer="input", turn_id=turn_id)

    def capture_final_response(
        self,
        text: str,
        *,
        turn_id: str = "",
        status_events: list[dict[str, Any]] | None = None,
    ) -> None:
        self.capture(
            "final_response",
            {"text": text, "status_events": status_events or []},
            layer="persona",
            turn_id=turn_id,
            # Raw observed answers need human curation before supervised learning.
            trainable=False,
        )


    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _asset_root(self) -> Path:
        project_root = Path(__file__).resolve().parents[3]
        configured = os.getenv("CELIA_TRAINING_ASSET_DIR", "").strip()
        return Path(configured or project_root / "var" / "training-assets")

    @staticmethod
    def _asset_capture_enabled() -> bool:
        value = os.getenv("CELIA_TRAINING_ASSET_CAPTURE_ENABLED", "1").strip().lower()
        return value in {"1", "true", "yes", "on", "sim"}

    def archive_tts_output(
        self,
        audio_path: str | Path,
        *,
        text: str,
        speech_id: str,
        turn_id: str = "",
        emotion: str = "",
        engine: Any = None,
    ) -> dict[str, Any] | None:
        """Copy the actually synthesized WAV into the immutable training asset bank."""
        if not self._asset_capture_enabled():
            return None
        try:
            source = Path(audio_path).expanduser().resolve()
            if not source.is_file():
                return None
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            asset_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", speech_id or uuid.uuid4().hex)[:120]
            target_dir = self._asset_root() / "tts" / day
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{asset_id}.wav"
            if target.exists():
                target = target_dir / f"{asset_id}-{uuid.uuid4().hex[:8]}.wav"
            shutil.copy2(source, target)
            run_meta: dict[str, Any] = {}
            run_sidecar = source.with_suffix(".run.json")
            if run_sidecar.is_file():
                try:
                    run_meta = json.loads(run_sidecar.read_text(encoding="utf-8"))
                except Exception:
                    run_meta = {"sidecar_parse_error": True}
                shutil.copy2(run_sidecar, target.with_suffix(".run.json"))
            with wave.open(str(target), "rb") as wav_file:
                sample_rate = wav_file.getframerate()
                channels = wav_file.getnchannels()
                frames = wav_file.getnframes()
            payload = {
                "asset_path": str(target),
                "asset_sha256": self._sha256_file(target),
                "text": text,
                "emotion": emotion,
                "tts_engine": type(engine).__name__ if engine is not None else "",
                "tts_engine_id": getattr(engine, "engine_id", "") if engine is not None else "",
                "sample_rate": sample_rate,
                "channels": channels,
                "frames": frames,
                "duration_seconds": round(frames / sample_rate, 6) if sample_rate else 0.0,
                "run_metadata": run_meta,
            }
            self.capture(
                "tts_asset_archived", payload, layer="tts", turn_id=turn_id,
                speech_id=speech_id, source="runtime_tts", trainable=True,
            )
            target.with_suffix(".dataset.json").write_text(
                json.dumps(_sanitize({
                    "schema": "celia.tts.asset.v1",
                    "trace_id": _current_trace_id(),
                    "turn_id": turn_id,
                    "speech_id": speech_id,
                    **payload,
                }), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return payload
        except Exception:
            return None

    def archive_asr_input(
        self,
        samples: Any,
        *,
        transcript: str,
        engine: Any = None,
        turn_id: str = "",
        sample_rate: int = 16000,
        channels: int = 1,
    ) -> dict[str, Any] | None:
        """Persist microphone WAV + transcript as a separate ASR training asset."""
        if not self._asset_capture_enabled():
            return None
        try:
            import numpy as np
            data = np.asarray(samples, dtype=np.float32).reshape(-1)
            data = np.clip(data, -1.0, 1.0)
            pcm = (data * 32767.0).astype(np.int16)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            base = re.sub(r"[^A-Za-z0-9_.-]+", "_", turn_id or uuid.uuid4().hex)[:120]
            target_dir = self._asset_root() / "asr_input" / day
            target_dir.mkdir(parents=True, exist_ok=True)
            target = target_dir / f"{base}.wav"
            if target.exists():
                target = target_dir / f"{base}-{uuid.uuid4().hex[:8]}.wav"
            with wave.open(str(target), "wb") as wav_file:
                wav_file.setnchannels(int(channels or 1))
                wav_file.setsampwidth(2)
                wav_file.setframerate(int(sample_rate or 16000))
                wav_file.writeframes(pcm.tobytes())
            engine_name = type(engine).__name__ if engine is not None else ""
            payload = {
                "asset_path": str(target),
                "asset_sha256": self._sha256_file(target),
                "transcript": transcript,
                "asr_engine": engine_name,
                "asr_model": getattr(engine, "model", "") if engine is not None else "",
                "asr_state": getattr(engine, "state", "") if engine is not None else "",
                "sample_rate": int(sample_rate or 16000),
                "channels": int(channels or 1),
                "frames": int(data.size),
                "duration_seconds": round(data.size / float(sample_rate or 16000), 6),
            }
            self.capture(
                "asr_input_archived", payload, layer="asr", turn_id=turn_id,
                source="microphone", trainable=True,
            )
            target.with_suffix(".dataset.json").write_text(
                json.dumps(_sanitize({
                    "schema": "celia.asr.asset.v1",
                    "trace_id": _current_trace_id(),
                    "turn_id": turn_id,
                    **payload,
                }), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            return payload
        except Exception:
            return None


training_trace = TrainingTraceRecorder()
