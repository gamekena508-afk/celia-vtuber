"""Publish Célia emotions to the web bus; legacy VTS transport is opt-in."""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from websockets.sync.client import connect

# 2026-08-11 (claude, Fatia 5): era conta por profundidade relativa
# (parents[N]). Agora vem da raiz unica em core/raizes.py — mover a
# arvore passa a ser mexer num lugar so, nao cacar 6 contas quebradas.
from .raizes import TRACK as TRACK_ROOT

_lipsync_lock = threading.Lock()
_pending_lipsync: dict[str, dict[str, Any]] = {}
_LEGACY_TRACKER_ENV = "CELIA_ENABLE_LEGACY_VTS_TRACKER"


def legacy_tracker_enabled() -> bool:
    """Return whether an operator explicitly enabled the retired VTS transport."""
    return os.getenv(_LEGACY_TRACKER_ENV, "").strip().lower() in {"1", "true", "yes"}


def control_uri() -> str:
    return os.getenv(
        "CELIA_VTS_CONTROL_URI",
        f"ws://{os.getenv('CELIA_TRACK_CONTROL_HOST', '127.0.0.1')}:{os.getenv('CELIA_TRACK_CONTROL_PORT', '8770')}",
    )


def send_command(payload: dict[str, Any], timeout: float = 0.8) -> dict[str, Any]:
    with connect(control_uri(), open_timeout=timeout, close_timeout=timeout) as ws:
        ws.send(json.dumps(payload, ensure_ascii=False))
        return json.loads(ws.recv(timeout=timeout))


def register_lipsync_envelope(
    speech_id: str,
    *,
    volumes: list[float],
    slice_length_ms: int,
    duration_sec: float,
) -> None:
    # The web client owns its own audio playback. This envelope only served the
    # retired external tracker, so do not retain data or contact it by default.
    if not legacy_tracker_enabled():
        return
    if not speech_id or not volumes:
        return
    now = time.time()
    with _lipsync_lock:
        expired = [
            key
            for key, item in _pending_lipsync.items()
            if float(item.get("expires_at", 0)) < now
        ]
        for key in expired:
            _pending_lipsync.pop(key, None)
        _pending_lipsync[speech_id] = {
            "speech_id": speech_id,
            "volumes": [max(0.0, min(1.0, float(v))) for v in volumes],
            "slice_length_ms": int(slice_length_ms or 20),
            "duration_sec": float(duration_sec),
            "expires_at": now + max(30.0, float(duration_sec) + 10.0),
        }


def start_lipsync_envelope(speech_id: str, *, timeout: float = 0.8) -> dict[str, Any]:
    if not speech_id:
        return {"ok": False, "error": "missing speech_id"}
    if not legacy_tracker_enabled():
        return {"ok": False, "status": "legacy_tracker_disabled"}
    with _lipsync_lock:
        envelope = _pending_lipsync.pop(speech_id, None)
    if not envelope:
        return {"ok": False, "error": f"no lipsync envelope for {speech_id}"}
    return send_command(
        {
            "cmd": "start_lipsync_envelope",
            "speech_id": speech_id,
            "volumes": envelope["volumes"],
            "slice_length_ms": envelope["slice_length_ms"],
            "duration_sec": envelope["duration_sec"],
            "mouth_gain": 0.92,
        },
        timeout=timeout,
    )


def publish_emotion(
    emotion: str,
    *,
    source: str,
    priority: int,
    ttl_sec: float | None,
    lock_sec: float = 0,
    persistent: bool = False,
    expected_emotion: str | None = None,
    expected_source: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = time.time()
    # The retired VTube tracker only understands numeric expiry values. Its
    # optional transport remains finite while the in-process web bus preserves
    # the actual character expression indefinitely.
    legacy_ttl_sec = float(ttl_sec if ttl_sec is not None else 30.0)
    event = {
        "cmd": "set_emotion",
        "event_id": str(uuid.uuid4()),
        "timestamp": now,
        "expires_at": now + legacy_ttl_sec,
        "emotion": emotion,
        "source": source,
        "priority": int(priority),
        "ttl_sec": legacy_ttl_sec,
        "persistent": bool(persistent),
        "lock_sec": float(lock_sec),
        "metadata": metadata or {},
    }
    # 2026-08-11 (claude, Fatia 6): o barramento em processo passa a ser o
    # consumidor de verdade. Antes daqui, este `publish_emotion` só sabia
    # falar com `vts_fake_tracker_v11.py` no ws://127.0.0.1:8770 — um tracker
    # que morreu e que, mesmo vivo, só rodava em Windows com VTube Studio.
    # Toda emoção que os 8 chamadores publicavam caía no spool e ficava lá:
    # 93 arquivos parados, medidos hoje. Agora o rosto no navegador recebe.
    from .emotion_bus import barramento

    resultado_bus = barramento.publicar(
        emotion,
        origem=source,
        prioridade=priority,
        ttl_s=ttl_sec,
        trava_s=lock_sec,
        persistente=persistent,
        esperado_emocao=expected_emotion,
        esperada_origem=expected_source,
        metadados=metadata or {},
    )

    # The web face is the active consumer. A separate VTube tracker is retained
    # solely for an operator who explicitly enables it on a compatible setup.
    if not legacy_tracker_enabled():
        return {
            "ok": bool(resultado_bus.get("ok")),
            "bus": resultado_bus,
            "legacy_tracker": "disabled",
        }

    try:
        externo = send_command(event)
        return {"ok": bool(resultado_bus.get("ok")), "bus": resultado_bus, "tracker": externo}
    except Exception as exc:
        # O spool existia pra dar durabilidade ao tracker externo. Sem tracker,
        # ele só acumula lixo — um arquivo por emoção, para sempre. Virou
        # opt-in: `CELIA_TRACK_SPOOL=1` liga de novo, para quem for depurar o
        # tracker externo.
        if os.getenv("CELIA_TRACK_SPOOL", "").strip() in ("1", "true", "yes"):
            spool = TRACK_ROOT / "spool"
            spool.mkdir(parents=True, exist_ok=True)
            tmp = spool / f".{event['event_id']}.tmp"
            target = spool / f"{event['event_id']}.json"
            tmp.write_text(json.dumps(event, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, target)
            return {"ok": bool(resultado_bus.get("ok")), "bus": resultado_bus,
                    "spooled": True, "path": str(target), "error": str(exc)}
        return {"ok": bool(resultado_bus.get("ok")), "bus": resultado_bus,
                "tracker_error": type(exc).__name__}
