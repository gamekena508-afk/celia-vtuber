"""Local Célia Audio Lab routes backed by the active OmniVoice engine."""

from __future__ import annotations

import asyncio
import io
import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from .core.raizes import VAR, garantir
from .service_context import ServiceContext
from .tts.omnivoice_tts import OMNIVOICE_PRESETS, TTSEngine
from .tts.tts_factory import TTSFactory


AUDIO_LAB_ROOT = VAR / "audio_lab"
PROFILES_DIR = AUDIO_LAB_ROOT / "profiles"
MAX_REFERENCE_BYTES = 24 * 1024 * 1024
MIN_REFERENCE_SECONDS = 3.0
MAX_REFERENCE_SECONDS = 20.0
PROFILE_ID = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")


class GenerateRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    profile_id: str | None = None
    preset: str = "neutra"
    speed: float | None = None
    duration: float | None = None
    normalize_text: bool = True
    instruct: str | None = Field(default=None, max_length=500)


class CompareRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1200)
    profile_id: str | None = None
    normalize_text: bool = True


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _profile_slug(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", normalized.lower()).strip("-")
    if not PROFILE_ID.fullmatch(slug):
        raise ValueError("Profile name must contain 1-64 letters, digits, spaces, or hyphens")
    return slug


def _profile_paths(profile_id: str) -> tuple[Path, Path, Path]:
    if not PROFILE_ID.fullmatch(profile_id):
        raise ValueError("Invalid profile id")
    directory = PROFILES_DIR / profile_id
    return directory, directory / "profile.json", directory / "voice_prompt.pt"


def _read_profile(profile_id: str) -> dict[str, Any]:
    directory, metadata_path, prompt_path = _profile_paths(profile_id)
    if not metadata_path.is_file() or not prompt_path.is_file():
        raise FileNotFoundError("Profile does not exist or has no validated OmniVoice cache")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    metadata["id"] = profile_id
    metadata["cached"] = True
    metadata["prompt_path"] = str(prompt_path)
    metadata["reference_path"] = str(directory / "reference.wav")
    return metadata


def _public_profile(metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": metadata["id"],
        "name": metadata["name"],
        "created_at": metadata["created_at"],
        "sample_rate": metadata["sample_rate"],
        "duration_seconds": metadata["duration_seconds"],
        "cached": bool(metadata.get("cached")),
    }


def _list_profiles() -> list[dict[str, Any]]:
    if not PROFILES_DIR.is_dir():
        return []
    profiles: list[dict[str, Any]] = []
    for directory in sorted(PROFILES_DIR.iterdir()):
        if not directory.is_dir():
            continue
        try:
            profiles.append(_public_profile(_read_profile(directory.name)))
        except (FileNotFoundError, ValueError, json.JSONDecodeError):
            continue
    return profiles


async def _read_reference_upload(upload: UploadFile) -> tuple[np.ndarray, int]:
    filename = upload.filename or "reference.wav"
    if Path(filename).suffix.lower() not in {".wav", ".flac", ".ogg"}:
        raise HTTPException(415, "Use WAV, FLAC, or OGG reference audio")
    payload = await upload.read(MAX_REFERENCE_BYTES + 1)
    if len(payload) > MAX_REFERENCE_BYTES:
        raise HTTPException(413, "Reference audio exceeds the 24 MB limit")
    try:
        audio, sample_rate = sf.read(io.BytesIO(payload), dtype="float32", always_2d=True)
    except RuntimeError as error:
        raise HTTPException(422, "Reference audio could not be decoded") from error
    if sample_rate <= 0 or audio.size == 0 or not np.isfinite(audio).all():
        raise HTTPException(422, "Reference audio is empty or invalid")
    mono = np.mean(audio, axis=1, dtype=np.float32)
    duration = len(mono) / sample_rate
    if not MIN_REFERENCE_SECONDS <= duration <= MAX_REFERENCE_SECONDS:
        raise HTTPException(
            422,
            f"Reference audio must be between {MIN_REFERENCE_SECONDS:g} and {MAX_REFERENCE_SECONDS:g} seconds",
        )
    return mono, sample_rate


def _engine(context: ServiceContext) -> TTSEngine:
    # Reuse the already-warmed conversation engine whenever possible. Creating
    # another OmniVoice instance here reloads the checkpoint, duplicates VRAM,
    # and makes Audio Lab latency measurements meaningless.
    active = getattr(context, "tts_engine", None)
    if isinstance(active, TTSEngine):
        return active
    config = getattr(getattr(context, "character_config", None), "tts_config", None)
    omnivoice = getattr(config, "omnivoice", None)
    if omnivoice is None:
        raise HTTPException(503, "Celia Audio Lab requires the omnivoice config block")
    return TTSFactory.get_tts_engine("omnivoice", **omnivoice.model_dump())


def _audio_response(path: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    output = Path(path)
    if not output.is_file():
        raise RuntimeError("OmniVoice did not create its WAV output")
    details = {
        "audio_url": "/cache/" + output.name,
        "file_name": output.name,
        "bytes": output.stat().st_size,
        "sample_rate": 24000,
        "channels": 1,
    }
    if metadata:
        details.update(metadata)
    return details


def init_audio_lab_routes(default_context_cache: ServiceContext) -> APIRouter:
    router = APIRouter(prefix="/audio-lab/api", tags=["audio-lab"])
    garantir(AUDIO_LAB_ROOT, PROFILES_DIR)

    @router.get("/health")
    async def health():
        engine = _engine(default_context_cache)
        return {
            "status": "ok",
            "engine": "omnivoice",
            "gpu_only": True,
            "profiles": len(_list_profiles()),
            "presets": engine.list_presets(),
        }

    @router.get("/profiles")
    async def profiles():
        return {"profiles": _list_profiles()}

    @router.post("/profiles")
    async def create_profile(
        name: str = Form(..., min_length=1, max_length=64),
        ref_text: str = Form(..., min_length=1, max_length=3000),
        reference: UploadFile = File(...),
    ):
        engine = _engine(default_context_cache)
        try:
            profile_id = _profile_slug(name)
            directory, metadata_path, prompt_path = _profile_paths(profile_id)
        except ValueError as error:
            raise HTTPException(422, str(error)) from error
        if directory.exists():
            raise HTTPException(409, "A profile with this name already exists")
        transcript = ref_text.strip()
        if not transcript:
            raise HTTPException(422, "The measured reference transcript is required")
        mono, sample_rate = await _read_reference_upload(reference)
        directory.mkdir(parents=True, exist_ok=False)
        reference_path = directory / "reference.wav"
        sf.write(reference_path, mono, sample_rate, subtype="PCM_16")
        metadata = {
            "name": name.strip(),
            "created_at": _utc_now(),
            "sample_rate": sample_rate,
            "duration_seconds": round(len(mono) / sample_rate, 3),
            "reference_transcript": transcript,
            "engine": "omnivoice",
        }
        metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            await asyncio.to_thread(
                engine.create_voice_clone_prompt, reference_path, transcript, prompt_path
            )
        except Exception as error:
            # Keep the uploaded source and metadata for inspection; only a complete
            # profile gets listed, so a failed cache cannot be selected for speech.
            raise HTTPException(500, f"OmniVoice could not cache this profile: {error}") from error
        return {"profile": _public_profile(_read_profile(profile_id))}

    @router.post("/generate")
    async def generate(request: GenerateRequest):
        engine = _engine(default_context_cache)
        if request.instruct and request.profile_id:
            raise HTTPException(422, "Choose either a cloned profile or Voice Design, not both")
        try:
            if request.instruct:
                output = await asyncio.to_thread(
                    engine.generate_voice_design_audio,
                    request.text,
                    request.instruct,
                    None,
                    request.preset,
                    request.speed,
                    request.duration,
                    request.normalize_text,
                )
                return _audio_response(output, {"mode": "voice-design", "preset": request.preset})
            if request.profile_id:
                profile = _read_profile(request.profile_id)
                output = await asyncio.to_thread(
                    engine.generate_profile_audio,
                    request.text,
                    profile["prompt_path"],
                    None,
                    request.preset,
                    request.speed,
                    request.duration,
                    request.normalize_text,
                )
                return _audio_response(output, {"mode": "clone", "profile_id": request.profile_id, "preset": request.preset})
            output = await asyncio.to_thread(
                engine.generate_default_profile_audio,
                request.text,
                None,
                request.preset,
                request.speed,
                request.duration,
                request.normalize_text,
            )
            return _audio_response(output, {"mode": "celia-default", "preset": request.preset})
        except (FileNotFoundError, ValueError) as error:
            raise HTTPException(422, str(error)) from error

    @router.post("/compare")
    async def compare(request: CompareRequest):
        engine = _engine(default_context_cache)
        profile = _read_profile(request.profile_id) if request.profile_id else None
        results: list[dict[str, Any]] = []
        for preset in OMNIVOICE_PRESETS:
            started = asyncio.get_running_loop().time()
            if profile:
                output = await asyncio.to_thread(
                    engine.generate_profile_audio,
                    request.text,
                    profile["prompt_path"],
                    None,
                    preset,
                    None,
                    None,
                    request.normalize_text,
                )
            else:
                output = await asyncio.to_thread(
                    engine.generate_default_profile_audio,
                    request.text,
                    None,
                    preset,
                    None,
                    None,
                    request.normalize_text,
                )
            results.append(_audio_response(output, {
                "preset": preset,
                "elapsed_seconds": round(asyncio.get_running_loop().time() - started, 3),
            }))
        return {"results": results, "profile_id": request.profile_id}

    return router
