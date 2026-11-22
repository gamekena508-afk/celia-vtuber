"""Síntese local das vozes dos personagens auxiliares pelo OmniVoice da Célia.

A voz da Célia NÃO passa por aqui: continua no pipeline existente (OmniVoice).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
import wave
import httpx
from pathlib import Path
from typing import Any

from celia.core.raizes import RAIZ

from .characters import voice_spec
from .settings import LIVE_DIR

SCRIPT = RAIZ / "tools" / "live_voice_synth.ps1"
AUDIO_DIR = LIVE_DIR / "audio"
_lock = asyncio.Lock()  # SAPI/OneCore: uma síntese por vez


class SynthError(RuntimeError):
    pass


def wav_info(path: Path) -> dict[str, Any]:
    """Duração e energia reais do WAV gerado (usado para validar antes de tocar)."""
    with wave.open(str(path), "rb") as w:
        rate, frames, width, channels = w.getframerate(), w.getnframes(), w.getsampwidth(), w.getnchannels()
        raw = w.readframes(frames)
    if width != 2 or not frames:
        return {"duration_s": frames / rate if rate else 0.0, "rms": 0.0, "sample_rate": rate}
    import array
    samples = array.array("h")
    samples.frombytes(raw)
    if channels > 1:
        samples = samples[::channels]
    rms = (sum(s * s for s in samples) / max(1, len(samples))) ** 0.5 / 32768.0
    return {"duration_s": frames / rate, "rms": rms, "sample_rate": rate}


async def synthesize(character: str, text: str, *, out_dir: Path | None = None, timeout: float = 120.0) -> dict[str, Any]:
    spec = voice_spec(character)
    out_dir = Path(out_dir) if out_dir else AUDIO_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    ident = f"{character}-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}"
    wav = out_dir / f"{ident}.wav"
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(base_url="http://127.0.0.1:12393", timeout=timeout) as client:
            response = await client.post('/audio-lab/api/generate', json={"text": text, "profile_id": spec["voice"]})
            response.raise_for_status()
            url = response.json().get("audio_url", "")
            if not url.startswith('/cache/') or '/' in url[len('/cache/'):]:
                raise SynthError('tts_invalid_audio_url')
            # O OmniVoice grava em RAIZ/cache; o mount /cache da aplicação
            # aponta para VAR/cache. Ler o arquivo produzido evita o 404.
            source = RAIZ / 'cache' / url[len('/cache/'):]
            wav.write_bytes(source.read_bytes())
        info = wav_info(wav)
        if info["duration_s"] < 0.2 or info["rms"] < 0.002:
            raise SynthError("tts_silent_or_empty")
        return {"id": ident, "path": wav, "voice": spec["voice"], "engine": spec["engine"], "latency_ms": int((time.monotonic() - started) * 1000), **info}
    except (httpx.HTTPError, ValueError, OSError, wave.Error) as exc:
        raise SynthError("omnivoice_failed:" + str(exc)[:120]) from exc


def purge_old(max_age_s: float = 3600.0, out_dir: Path | None = None) -> int:
    out_dir = Path(out_dir) if out_dir else AUDIO_DIR
    removed = 0
    if out_dir.exists():
        for f in out_dir.glob("*.wav"):
            try:
                if time.time() - f.stat().st_mtime > max_age_s:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
    return removed
