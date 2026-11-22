"""Bounded, text-free audio transport telemetry. Never grants playback authority."""
from .observability import observability
from loguru import logger
import json

EVENTS = frozenset({"AUDIO_GENERATED", "AUDIO_SENT", "AUDIO_RECEIVED", "AUDIO_QUEUED",
    "AUDIO_DEFERRED", "AUDIO_STARTED", "AUDIO_FINISHED", "AUDIO_CANCELLED", "AUDIO_DROPPED"})

def record_audio_lifecycle(data, *, source="frontend"):
    event = data.get("event")
    speech_id = data.get("speech_id")
    if not isinstance(event, str) or event not in EVENTS or not isinstance(speech_id, str) or not speech_id or len(speech_id) > 128:
        return False
    reason = str(data.get("reason") or "")[:120]
    logger.info("audio-lifecycle " + json.dumps({"event": event, "speech_id": speech_id, "reason": reason, "source": source}, ensure_ascii=True))
    observability.emit(event, "audio.lifecycle", "dropped" if event == "AUDIO_DROPPED" else "ok",
        {"speech_id": speech_id, "reason": reason, "source": source})
    return True
