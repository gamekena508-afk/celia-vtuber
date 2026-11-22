"""Local OmniVoice engine for the standard Célia TTS interface."""

import asyncio
import hashlib
import json
import os
import platform
import sys
import threading
import time
import uuid
import wave
from datetime import datetime, timezone
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import numpy as np
import soundfile as sf
from loguru import logger

from ..core.observability import observability
from .tts_interface import TTS_ENGINE_OMNIVOICE, TTSInterface
from .production_voice import (VOICE_PROFILE_ID, GENERATION_OPTIONS, production_descriptor,
                               validate_production_assets, validate_production_config,
                               ANCHOR_PROFILE_ID, VOICE_ROUTING_ID, MODEL_INSTRUCT,
                               MODEL_VARIANT_ID, validate_reference_descriptor)
from ..utils.tts_preprocessor import sanitize_production_tts_text

_INFERENCE_LOCK = threading.Lock()


@dataclass(frozen=True)
class OmniVoicePreset:
    """Only expose controls that the installed OmniVoice API accepts."""

    name: str
    label: str
    description: str
    speed: float
    num_step: int
    guidance_scale: float
    seed: int | None = None
    text_tag: str | None = None


EMOTION_PRESETS: dict[str, dict[str, Any]] = {
    "angry": {
        "seeds": (7319, 7324, 7330, 7314, 7306),
        "production_seeds": (7319,),
        "tag": "[angry]",
        "type": "native",
        "interpretations": {
            7319: "confronto",
            7324: "confronto",
            7330: "irritacao",
            7314: "limite",
            7306: "acusacao",
        },
    },
    "sad": {
        "seeds": (7308, 9000), "production_seeds": (9000,),
        "tag": "[sad]", "type": "native",
    },
    "excited": {
        "seeds": (7316, 7324), "production_seeds": (7316,),
        "tag": "[excited]", "type": "native",
    },
    "calm": {"seeds": (7314, 7318), "tag": "[calm]", "type": "native"},
    "whisper": {"seeds": (7314,), "tag": "[whisper]", "type": "native"},
    "singing": {"seeds": (7319,), "tag": "[singing]", "type": "native"},
    # [happy] was measured unstable on 2026-09-07 (empty/truncated waveform
    # and speaker-similarity collapse). Keep the manual preset available in
    # Audio Lab, but automatic speech uses the stable excited acoustic route.
    "happy": {
        "seeds": (7304,), "tag": "[happy]", "type": "native",
        "production_acoustic": "excited", "production_seeds": (7316,),
    },
    "nervous": {
        "seeds": (),
        "tag": "[nervous]",
        "type": "fallback",
        "fallback": "calm",
        "fallback_seeds": (7314,),
    },
}

EMOTION_ALIASES = {
    "surprised": "excited",
    "surprise": "excited",
    "excitement": "excited",
    "teasing": "happy",
    "in_love": "happy",
}


def _emotion_preset_name(emotion: str, seed: int) -> str:
    spec = EMOTION_PRESETS[emotion]
    interpretation = spec.get("interpretations", {}).get(seed)
    if interpretation:
        return f"{emotion}_{interpretation}_{seed}"
    return f"{emotion}_{seed}"


def _build_emotion_presets() -> dict[str, OmniVoicePreset]:
    presets: dict[str, OmniVoicePreset] = {}
    for emotion, spec in EMOTION_PRESETS.items():
        if spec["type"] != "native":
            continue
        for seed in spec["seeds"]:
            preset_name = _emotion_preset_name(emotion, seed)
            interpretation = spec.get("interpretations", {}).get(seed)
            label = emotion.capitalize()
            if interpretation:
                label += f" - {interpretation}"
            presets[preset_name] = OmniVoicePreset(
                name=preset_name,
                label=f"{label} ({seed})",
                description=f"Célia v3: {emotion} aprovado para a seed {seed}.",
                speed=1.0,
                num_step=32,
                guidance_scale=3.0,
                seed=seed,
                text_tag=spec["tag"],
            )
    return presets


def resolve_emotion_preset(
    emotion: str | None,
    chooser=None,
) -> dict[str, Any] | None:
    """Resolve semantic emotion to an authorized acoustic preset.

    ``nervous`` intentionally keeps its semantic identity while using the
    documented ``calm`` acoustic pool until a native seed is approved.
    ``chooser`` is injectable for regression tests; production deterministically
    uses the first measured-safe seed in the selected pool.
    """
    if not emotion:
        return None
    requested = str(emotion).strip().lower()
    canonical = EMOTION_ALIASES.get(requested, requested)
    source = EMOTION_PRESETS.get(canonical)
    if source is None:
        return None
    requested_source = source
    acoustic = str(requested_source.get("production_acoustic") or canonical)
    fallback = acoustic if acoustic != canonical else None
    if requested_source["type"] == "fallback":
        acoustic = str(requested_source["fallback"])
        fallback = acoustic
        seeds = tuple(
            requested_source.get("fallback_seeds")
            or EMOTION_PRESETS[acoustic].get("production_seeds")
            or EMOTION_PRESETS[acoustic]["seeds"]
        )
    else:
        seeds = tuple(
            requested_source.get("production_seeds")
            or EMOTION_PRESETS[acoustic].get("production_seeds")
            or requested_source["seeds"]
        )
    if not seeds:
        raise ValueError(f"Emotion {requested} has no authorized acoustic seed pool")
    # Production voice is deterministic. Random seed selection caused audible
    # identity drift even while the reference WAV stayed constant. Tests may
    # still inject a chooser to verify the closed authorized pool.
    seed = int(chooser(seeds) if chooser else seeds[0])
    if seed not in seeds:
        raise ValueError(f"Chooser returned unauthorized seed {seed} for {requested}")
    return {
        "requested_emotion": requested,
        "semantic_emotion": requested,
        "acoustic_emotion": acoustic,
        "fallback": fallback,
        "seed": seed,
        "preset": _emotion_preset_name(acoustic, seed),
        "tag": EMOTION_PRESETS[acoustic]["tag"],
    }


OMNIVOICE_PRESETS: dict[str, OmniVoicePreset] = {
    "calma": OmniVoicePreset(
        name="calma",
        label="Calma",
        description="Ritmo mais lento para fala descansada.",
        speed=0.88,
        num_step=32,
        guidance_scale=2.0,
        seed=7314,
    ),
    "neutra": OmniVoicePreset(
        name="neutra",
        label="Neutra",
        description="Ritmo base para conversa da Celia.",
        # Neutral conversation follows the calmer approved acoustic region.
        speed=0.88,
        num_step=32,
        guidance_scale=2.0,
        seed=7314,
    ),
    "expressiva": OmniVoicePreset(
        name="expressiva",
        label="Expressiva",
        description="Ritmo mais vivo para reacoes e momentos de palco.",
        speed=1.08,
        num_step=36,
        guidance_scale=2.2,
    ),
}

OMNIVOICE_PRESETS.update(_build_emotion_presets())


PRESET_POR_EMOCAO = {
    # The legacy ``expressiva`` preset has no seed. Route emotion requests to
    # already-approved acoustic pools instead of silently selecting it.
    "happy": "neutra",
    "in_love": "neutra",
    "surprised": "excited_7316",
    "teasing": "neutra",
    "angry": "angry_confronto_7319",
    "sad": "calma",
    "scared": "calma",
    "neutral": "neutra",
    "confused": "neutra",
}


def load_omnivoice_cuda(model_path: Path, omnivoice_class=None, cuda_available=None):
    """Load both OmniVoice models on the dedicated Célia CUDA device.

    Voice inference has no CPU fallback: falling back turns an interactive
    character into a multi-minute blocking job and hides a broken GPU setup.
    """
    if cuda_available is None:
        import torch

        cuda_available = torch.cuda.is_available
    if not cuda_available():
        raise RuntimeError(
            "OmniVoice requires CUDA for Célia; GPU is unavailable. "
            "CPU fallback is intentionally disabled."
        )
    import torch

    if omnivoice_class is None:
        from omnivoice import OmniVoice

        omnivoice_class = OmniVoice
    dtype_name = os.getenv("CELIA_OMNIVOICE_DTYPE", "float16").strip().lower()
    dtype_map = {
        "float16": torch.float16,
        "fp16": torch.float16,
        "bfloat16": torch.bfloat16,
        "bf16": torch.bfloat16,
        "float32": torch.float32,
        "fp32": torch.float32,
    }
    if dtype_name not in dtype_map:
        raise ValueError(
            "CELIA_OMNIVOICE_DTYPE must be one of: float16, bfloat16, float32"
        )
    torch_dtype = dtype_map[dtype_name]
    logger.info(f"⚡ OmniVoice loading on CUDA with dtype={torch_dtype}")
    model = omnivoice_class.from_pretrained(
        str(model_path), device_map="cuda:0", torch_dtype=torch_dtype
    )
    model_device = str(model.device)
    tokenizer_device = str(model.audio_tokenizer.device)
    if not model_device.startswith("cuda") or not tokenizer_device.startswith("cuda"):
        raise RuntimeError(
            "OmniVoice CUDA placement failed: "
            f"model={model_device}, audio_tokenizer={tokenizer_device}."
        )
    return model


class OmniVoiceSynthesisError(RuntimeError):
    """A production synthesis failed; no acoustic retry is permitted."""

    retry_neutral = False


class TTSEngine(TTSInterface):
    engine_id = TTS_ENGINE_OMNIVOICE
    sample_rate = 24000
    fixed_voice_profile = True
    voice_profile_id = VOICE_PROFILE_ID

    def __init__(
        self,
        model_path: str,
        ref_audio: str,
        ref_text: str,
        language: str = "pt",
        emotion_tags_enabled: bool = False,
        angry_default_preset: str = "angry_confronto_7319",
        **production_options,
    ):
        self.model_path = Path(model_path).expanduser().resolve()
        self.ref_audio = Path(ref_audio).expanduser().resolve()
        self.ref_text = ref_text.strip()
        self.language = language
        self.emotion_tags_enabled = bool(emotion_tags_enabled)
        self.angry_default_preset = str(angry_default_preset).strip().lower()
        if not self.model_path.is_dir():
            raise FileNotFoundError(f"OmniVoice model directory not found: {self.model_path}")
        if not self.ref_audio.is_file():
            raise FileNotFoundError(f"OmniVoice reference audio not found: {self.ref_audio}")
        if not self.ref_text:
            raise ValueError("OmniVoice requires the measured reference transcript")
        self._model = None
        self._model_lock = threading.Lock()
        # OmniVoice model generation is serialized so a second UI click cannot
        # race cache/profile operations on the single Célia GPU model.
        self._generation_lock = _INFERENCE_LOCK
        self._production_options = dict(production_options)
        self._production_validated = False
        self._last_voice_quality = None
        self._active_preset = "neutra"
        # Fast 16-step mode is opportunistic. Two consecutive empty/invalid
        # waveforms suspend it briefly so later turns jump directly to the
        # measured reliable preset instead of paying a doomed first attempt.
        self._fast_failure_streak = 0
        self._fast_disabled_until = 0.0
        if self.angry_default_preset not in OMNIVOICE_PRESETS:
            raise ValueError(f"Unknown angry default preset: {self.angry_default_preset}")

    def _load_model(self):
        if self._model is None:
            with self._model_lock:
                if self._model is None:
                    self._model = load_omnivoice_cuda(self.model_path)
        return self._model

    def warmup(self) -> None:
        """Preload the approved checkpoint so the first spoken turn is not a cold start."""
        started = time.perf_counter()
        self._validate_production_assets()
        if self._uses_central():
            self._central_client().ensure_central()
            return
        self._load_model()
        logger.info(
            f"OmniVoice warmup complete in {(time.perf_counter() - started) * 1000:.0f} ms"
        )

    @staticmethod
    def _describe_audio(value) -> str:
        """Return shape metadata only; never put synthesized samples in logs."""
        try:
            data = np.asarray(value)
            return f"type={type(value).__name__}, shape={data.shape}, size={data.size}, dtype={data.dtype}"
        except Exception as error:
            return f"type={type(value).__name__}, array_error={type(error).__name__}"

    @classmethod
    def _mono(cls, samples) -> np.ndarray:
        if hasattr(samples, "detach"):
            samples = samples.detach().float().cpu().numpy()
        try:
            data = np.asarray(samples, dtype=np.float32)
        except (TypeError, ValueError) as error:
            raise ValueError(
                "OmniVoice returned non-numeric audio " + cls._describe_audio(samples)
            ) from error
        if data.ndim == 2:
            data = data[0] if data.shape[0] == 1 else data.mean(axis=0)
        if data.ndim != 1 or data.size == 0:
            raise ValueError("OmniVoice returned invalid audio " + cls._describe_audio(data))
        if not np.isfinite(data).all():
            raise ValueError("OmniVoice returned non-finite audio " + cls._describe_audio(data))
        return np.clip(data, -1.0, 1.0)

    @classmethod
    def _first_waveform(cls, generated) -> np.ndarray:
        """Accept the documented one-item list and a 1-D direct waveform safely."""
        if isinstance(generated, (list, tuple)):
            if not generated:
                raise RuntimeError("OmniVoice returned no audio")
            candidate = generated[0]
        else:
            candidate = generated
        logger.info(f"OmniVoice generated audio {cls._describe_audio(candidate)}")
        return cls._mono(candidate)

    @classmethod
    def _validate_waveform_quality(cls, samples: np.ndarray, text: str) -> None:
        """Reject grossly truncated or near-silent audio before writing a WAV."""
        duration = float(samples.size) / float(cls.sample_rate)
        words = max(1, len(str(text or "").split()))
        # This is intentionally conservative: it catches pathological 0.4 s
        # outputs for full sentences without punishing short interjections.
        minimum_duration = min(1.2, max(0.20, words * 0.11))
        peak = float(np.max(np.abs(samples))) if samples.size else 0.0
        rms = float(np.sqrt(np.mean(np.square(samples)))) if samples.size else 0.0
        if duration < minimum_duration:
            raise ValueError(
                f"OmniVoice returned truncated audio duration={duration:.3f}s "
                f"minimum={minimum_duration:.3f}s words={words}"
            )
        is_whisper = str(text or "").lstrip().lower().startswith("[whisper]")
        if not is_whisper and (peak < 0.025 or rms < 0.003):
            raise ValueError(
                f"OmniVoice returned near-silent audio peak={peak:.5f} rms={rms:.5f}"
            )

    @staticmethod
    def _validate_range(name: str, value: float, minimum: float, maximum: float) -> float:
        parsed = float(value)
        if not minimum <= parsed <= maximum:
            raise ValueError(f"{name} must be between {minimum} and {maximum}")
        return parsed

    def list_presets(self) -> dict[str, Any]:
        return {"active": self._active_preset, "presets": {
            name: asdict(preset) for name, preset in OMNIVOICE_PRESETS.items()
        }}

    def get_active_preset(self) -> dict[str, Any]:
        preset = OMNIVOICE_PRESETS[self._active_preset]
        return {"active": preset.name, "preset": asdict(preset)}

    def set_preset(self, name: str) -> dict[str, Any]:
        selected = str(name).strip().lower()
        if selected not in OMNIVOICE_PRESETS:
            raise ValueError(
                "Unknown OmniVoice preset: " + selected + ". Available: "
                + ", ".join(OMNIVOICE_PRESETS)
            )
        self._active_preset = selected
        return self.get_active_preset()

    def _generation_options(
        self,
        preset: str | None = None,
        speed: float | None = None,
        duration: float | None = None,
        normalize_text: bool = True,
        fast: bool = False,
    ) -> dict[str, Any]:
        preset_name = (preset or self._active_preset).strip().lower()
        selected = OMNIVOICE_PRESETS.get(preset_name)
        if selected is None:
            raise ValueError(
                "Unknown OmniVoice preset: " + preset_name + ". Available: "
                + ", ".join(OMNIVOICE_PRESETS)
            )
        actual_speed = selected.speed if speed is None else self._validate_range(
            "speed", speed, 0.70, 1.35
        )
        requested_steps = int(os.getenv("CELIA_OMNIVOICE_STEPS", "28"))
        if requested_steps < 8 or requested_steps > 100:
            raise ValueError("CELIA_OMNIVOICE_STEPS must be between 8 and 100")
        identity_base = (
            selected.name in {"neutra", "calma"}
            and selected.seed == 7314
            and not selected.text_tag
        )
        # Measured 2026-09-07: base seed 7314 frequently returns empty audio at
        # 16/28 steps but is reliable at 32. A single 32-step synthesis beats a
        # failed fast attempt plus retry in both latency and voice continuity.
        runtime_steps = selected.num_step if identity_base else min(selected.num_step, requested_steps)
        fast_requested = bool(fast) and not identity_base
        fast_available = fast_requested and time.monotonic() >= self._fast_disabled_until
        chosen_steps = min(runtime_steps, 16) if fast_available else runtime_steps
        if fast_requested and not fast_available:
            # During cooldown skip both 16 and the intermediate 28-step route.
            # One reliable synthesis is faster than repeating a known-bad fast path.
            chosen_steps = selected.num_step
        options: dict[str, Any] = {
            "speed": actual_speed,
            "num_step": chosen_steps,
            "num_step_confiavel": selected.num_step,
            "_fast_path_requested": fast_requested,
            # Seed 7314/base identity was measured to converge to the exact same
            # waveform after the existing terminal-punctuation retry. Prefer the
            # known-good text form first; keep the punctuated form as the single
            # bounded fallback if the stripped attempt itself fails.
            "_strip_terminal_punctuation_first": identity_base,
            "guidance_scale": selected.guidance_scale,
            "normalize_text": bool(normalize_text),
            "postprocess_output": True,
        }
        if duration is not None:
            options["duration"] = self._validate_range("duration", duration, 0.5, 90.0)
        return options

    @staticmethod
    def _apply_seed(seed: int | None) -> None:
        if seed is None:
            return
        from omnivoice.utils.common import fix_random_seed
        import torch

        fix_random_seed(int(seed))
        if torch.cuda.is_available():
            torch.cuda.manual_seed_all(int(seed))

    def _prepare_tagged_text(self, text: str, selected: OmniVoicePreset) -> str:
        if not selected.text_tag:
            return text
        if not self.emotion_tags_enabled:
            raise ValueError(
                f"Preset {selected.name} requires emotion_tags_enabled=True; "
                "v1/v2 remain tag-free."
            )
        return f"{selected.text_tag} {text.lstrip()}"

    def _generate_lab_waveform(self, *, seed: int | None, **kwargs) -> np.ndarray:
        if seed is None:
            raise ValueError("OmniVoice synthesis requires an authorized seed")
        with self._generation_lock:
            self._apply_seed(seed)
            last_error: Exception | None = None
            base = dict(kwargs)
            reliable_steps = base.pop("num_step_confiavel", None)
            fast_requested = bool(base.pop("_fast_path_requested", False))
            strip_terminal_first = bool(base.pop("_strip_terminal_punctuation_first", False))
            first_steps = int(base.get("num_step") or 0)
            fast_attempted = bool(
                fast_requested and reliable_steps and first_steps < int(reliable_steps)
            )

            original_text = str(base.get("text") or "")
            stripped_text = original_text.rstrip().rstrip(".!?…,;:").rstrip()
            punctuation_changed = bool(
                stripped_text and stripped_text != original_text.rstrip()
            )
            if strip_terminal_first and punctuation_changed:
                base["text"] = stripped_text

            attempts: list[dict[str, Any]] = [dict(base)]

            # At most one fallback synthesis. Base identity seed 7314 uses the
            # measured-good stripped form first; if that rare path fails, retain
            # the original punctuated form as the only fallback. Other presets
            # keep the historical order (punctuated -> stripped).
            fallback = dict(base)
            if reliable_steps:
                fallback["num_step"] = int(reliable_steps)
            if punctuation_changed:
                fallback["text"] = (
                    original_text.rstrip() if strip_terminal_first else stripped_text
                )
            if fallback != attempts[0]:
                attempts.append(fallback)

            for number, attempt in enumerate(attempts[:2]):
                try:
                    audio = self._load_model().generate(**attempt)
                    waveform = self._first_waveform(audio)
                    self._validate_waveform_quality(
                        waveform, str(attempt.get("text") or "")
                    )
                    if number == 0 and fast_attempted:
                        self._fast_failure_streak = 0
                    if number:
                        logger.info(
                            f"OmniVoice recuperou a fala na tentativa {number + 1} "
                            f"(num_step={attempt.get('num_step')})"
                        )
                    return waveform
                except (RuntimeError, ValueError) as error:
                    last_error = error
                    logger.warning(
                        f"OmniVoice devolveu audio inutilizavel na tentativa "
                        f"{number + 1} (num_step={attempt.get('num_step')}): "
                        f"{type(error).__name__}"
                    )
                    if number == 0 and fast_attempted:
                        self._fast_failure_streak += 1
                        threshold = max(1, int(os.getenv("CELIA_OMNIVOICE_FAST_FAILURE_THRESHOLD", "2")))
                        if self._fast_failure_streak >= threshold:
                            cooldown = max(5.0, float(os.getenv("CELIA_OMNIVOICE_FAST_COOLDOWN", "30")))
                            self._fast_disabled_until = time.monotonic() + cooldown
                            logger.warning(
                                f"OmniVoice fast-path suspenso por {cooldown:.0f}s "
                                f"apos {self._fast_failure_streak} falhas consecutivas"
                            )
                    self._apply_seed(seed)

            assert last_error is not None
            raise OmniVoiceSynthesisError(
                f"OmniVoice exhausted {len(attempts[:2])} bounded synthesis attempt(s): "
                f"{type(last_error).__name__}"
            ) from last_error

    def _write_waveform(self, samples: np.ndarray, file_name_no_ext=None) -> str:
        file_name = self.generate_cache_file_name(file_name_no_ext, "wav")
        sf.write(file_name, samples, self.sample_rate, subtype="PCM_16")
        with wave.open(file_name, "rb") as output:
            if output.getnchannels() != 1 or output.getframerate() != self.sample_rate:
                raise RuntimeError("OmniVoice output must be mono 24 kHz WAV")
        return file_name

    @staticmethod
    def _sha256_file(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _write_run_record(
        self,
        *,
        run_id: str,
        started_at: str,
        started_monotonic: float,
        text: str,
        generation_text: str,
        selected: OmniVoicePreset,
        requested_emotion: str | None = None,
        acoustic_emotion: str | None = None,
        fallback_emotion: str | None = None,
        output_path: Path | None = None,
        status: str,
        error: Exception | None = None,
        fallback_preset: str | None = None,
        voice_profile: dict | None = None,
    ) -> Path:
        """Persist safe, reproducible metadata beside each local WAV."""
        ended_at = datetime.now(timezone.utc).isoformat()
        record_dir = output_path.parent if output_path else Path("cache")
        record_dir.mkdir(parents=True, exist_ok=True)
        record_path = (
            output_path.with_suffix(".run.json")
            if output_path
            else record_dir / f"omnivoice-{run_id}.run.json"
        )
        output = None
        if output_path and output_path.is_file():
            with wave.open(str(output_path), "rb") as wav_file:
                output = {
                    "path": str(output_path),
                    "bytes": output_path.stat().st_size,
                    "sha256": self._sha256_file(output_path),
                    "sample_rate": wav_file.getframerate(),
                    "channels": wav_file.getnchannels(),
                    "frames": wav_file.getnframes(),
                    "duration_seconds": round(
                        wav_file.getnframes() / wav_file.getframerate(), 6
                    ),
                }
        device = "unavailable"
        try:
            import torch

            if torch.cuda.is_available():
                device = str(torch.cuda.current_device())
                device = device if device.startswith("cuda") else f"cuda:{device}"
        except Exception:
            device = "unavailable"
        record = {
            "schema_version": "celia.omnivoice.run.v1",
            "run_id": run_id,
            "operation": "tts.synthesize",
            "status": status,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_ms": round((time.monotonic() - started_monotonic) * 1000, 3),
            "actor": "celia",
            "provider": "omnivoice",
            "model": str(self.model_path),
            "model_revision": "local-directory",
            "runtime": {"python": sys.executable, "platform": platform.platform()},
            "device": device,
            "parameters": {
                "language": self.language,
                "preset": selected.name,
                "seed": selected.seed,
                "tag": selected.text_tag,
                "requested_emotion": requested_emotion,
                "acoustic_emotion": acoustic_emotion,
                "fallback_emotion": fallback_emotion,
                "fallback_preset": fallback_preset,
                "emotion_tags_enabled": self.emotion_tags_enabled,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "text_length": len(text),
                "generation_text_sha256": hashlib.sha256(
                    generation_text.encode("utf-8")
                ).hexdigest(),
            },
            "input": {
                "reference_audio": str(voice_profile["ref_audio"] if voice_profile else self.ref_audio),
                "reference_audio_sha256": self._sha256_file(Path(voice_profile["ref_audio"]) if voice_profile else self.ref_audio),
                "reference_text_sha256": hashlib.sha256(
                    (voice_profile["ref_text"] if voice_profile else self.ref_text).encode("utf-8")
                ).hexdigest(),
            },
            "output": output,
            "token_usage": "unavailable: local TTS does not expose token counts",
            "warnings": [],
            "error": (
                {"class": type(error).__name__, "message": str(error)}
                if error
                else None
            ),
            "entrypoint": "celia.tts.omnivoice_tts.TTSEngine.generate_audio",
        }
        if selected.name in {VOICE_PROFILE_ID, ANCHOR_PROFILE_ID}:
            record["voice_profile"] = voice_profile or production_descriptor()
            record["voice_routing_id"] = VOICE_ROUTING_ID
            record["parameters"].update(GENERATION_OPTIONS)
            record["parameters"].update(voice_profile_id=selected.name, engine="omnivoice_v2")
        verified = getattr(self, "_last_voice_quality", None)
        if verified and status == "ok":
            record["biometric_verification"] = {
                "model": "ECAPA-TDNN", "score": verified.get("similarity_ecapa"),
                "threshold": verified.get("threshold"), "steps": verified.get("steps"),
                "attempts": verified.get("attempts"), "seed": verified.get("seed"),
                "evaluations": verified.get("evaluations", [])}
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return record_path

    def _create_prompt_without_gradients(self, reference: Path, transcript: str):
        """Encode reference audio without retaining a training graph on the GPU."""
        import torch

        with torch.inference_mode():
            return self._load_model().create_voice_clone_prompt(
                ref_audio=str(reference), ref_text=transcript
            )

    def create_voice_clone_prompt(self, ref_audio: str | Path, ref_text: str, output_path: str | Path) -> None:
        reference = Path(ref_audio).expanduser().resolve()
        transcript = str(ref_text).strip()
        if not reference.is_file():
            raise FileNotFoundError(f"OmniVoice reference audio not found: {reference}")
        if not transcript:
            raise ValueError("OmniVoice profiles require the measured reference transcript")
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._generation_lock:
            prompt = self._create_prompt_without_gradients(reference, transcript)
            prompt.save(str(destination))

    def generate_profile_audio(
        self,
        text: str,
        prompt_path: str | Path,
        file_name_no_ext=None,
        preset: str | None = None,
        speed: float | None = None,
        duration: float | None = None,
        normalize_text: bool = True,
    ) -> str:
        cache_path = Path(prompt_path).expanduser().resolve()
        if not cache_path.is_file():
            raise FileNotFoundError(f"OmniVoice profile cache not found: {cache_path}")
        from omnivoice import VoiceClonePrompt

        prompt = VoiceClonePrompt.load(str(cache_path))
        selected_name = (preset or self._active_preset).strip().lower()
        selected = OMNIVOICE_PRESETS.get(selected_name)
        if selected is None:
            raise ValueError(f"Unknown OmniVoice preset: {selected_name}")
        if selected.seed is None:
            raise ValueError(
                f"Preset {selected.name} has no authorized seed; synthesis blocked"
            )
        samples = self._generate_lab_waveform(
            seed=selected.seed,
            text=text,
            language=self.language,
            voice_clone_prompt=prompt,
            **self._generation_options(preset, speed, duration, normalize_text),
        )
        return self._write_waveform(samples, file_name_no_ext)

    def generate_voice_design_audio(
        self,
        text: str,
        instruct: str,
        file_name_no_ext=None,
        preset: str | None = None,
        speed: float | None = None,
        duration: float | None = None,
        normalize_text: bool = True,
    ) -> str:
        if not str(instruct).strip():
            raise ValueError("OmniVoice voice design requires an instruction")
        selected_name = (preset or self._active_preset).strip().lower()
        selected = OMNIVOICE_PRESETS.get(selected_name)
        if selected is None:
            raise ValueError(f"Unknown OmniVoice preset: {selected_name}")
        if selected.seed is None:
            raise ValueError(
                f"Preset {selected.name} has no authorized seed; synthesis blocked"
            )
        samples = self._generate_lab_waveform(
            seed=selected.seed,
            text=text,
            language=self.language,
            instruct=str(instruct).strip(),
            **self._generation_options(preset, speed, duration, normalize_text),
        )
        return self._write_waveform(samples, file_name_no_ext)

    def generate_default_profile_audio(
        self,
        text: str,
        file_name_no_ext=None,
        preset: str | None = None,
        speed: float | None = None,
        duration: float | None = None,
        normalize_text: bool = True,
        requested_emotion: str | None = None,
        acoustic_emotion: str | None = None,
        fallback_emotion: str | None = None,
        fast: bool = False,
        fallback_preset: str | None = None,
    ) -> str:
        run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        started_monotonic = time.monotonic()
        selected_name = (preset or self._active_preset).strip().lower()
        selected = OMNIVOICE_PRESETS.get(selected_name)
        if selected is None:
            raise ValueError(f"Unknown OmniVoice preset: {selected_name}")
        if selected.seed is None:
            raise ValueError(
                f"Preset {selected.name} has no authorized seed; synthesis blocked"
            )
        generation_text = self._prepare_tagged_text(text, selected)
        try:
            # Célia's approved identity must be conditioned directly on every turn.
            # VoiceClonePrompt remains available only for explicit Lab profiles.
            samples = self._generate_lab_waveform(
                seed=selected.seed,
                text=generation_text,
                language=self.language,
                ref_audio=str(self.ref_audio),
                ref_text=self.ref_text,
                **({"instruct": MODEL_INSTRUCT} if MODEL_INSTRUCT else {}),
                **self._generation_options(preset, speed, duration, normalize_text, fast),
            )
            output_path = Path(self._write_waveform(samples, file_name_no_ext)).resolve()
        except Exception as error:
            self._write_run_record(
                run_id=run_id,
                started_at=started_at,
                started_monotonic=started_monotonic,
                text=text,
                generation_text=generation_text,
                selected=selected,
                requested_emotion=requested_emotion,
                acoustic_emotion=acoustic_emotion,
                fallback_emotion=fallback_emotion,
                fallback_preset=fallback_preset,
                status="error",
                error=error,
            )
            raise
        self._write_run_record(
            run_id=run_id,
            started_at=started_at,
            started_monotonic=started_monotonic,
            text=text,
            generation_text=generation_text,
            selected=selected,
            requested_emotion=requested_emotion,
            acoustic_emotion=acoustic_emotion,
            fallback_emotion=fallback_emotion,
            fallback_preset=fallback_preset,
            output_path=output_path,
            status="ok",
        )
        observability.emit(
            "tts_generated",
            "service.omnivoice",
            "ok",
            {"tts_engine": TTS_ENGINE_OMNIVOICE, "audio_generated": True},
        )
        return str(output_path)

    def _validate_production_assets(self) -> None:
        if self._production_validated:
            return
        validate_production_config({
            **self._production_options, "model_path": str(self.model_path),
            "ref_audio": str(self.ref_audio), "ref_text": self.ref_text,
            "language": self.language, "emotion_tags_enabled": self.emotion_tags_enabled,
        })
        validate_production_assets(self.model_path, self.ref_audio, self.ref_text)
        if os.getenv("CELIA_OMNIVOICE_DTYPE", "float16").lower() not in {"float16", "fp16"}:
            raise ValueError("production_voice_dtype_must_be_float16")
        self._production_validated = True
        logger.info("🎙️ profile={} engine=OmniVoice V2 model={} instruct={} seed=adaptive steps=10 ref={} emotion_tags=false",
                    VOICE_PROFILE_ID, MODEL_VARIANT_ID, MODEL_INSTRUCT or "none", self.ref_audio.name)

    def _uses_central(self):
        models = Path.home() / "Documents/Projetos/central-ia/motores/omnivoicev2/models"
        return self.model_path == models.resolve() and MODEL_INSTRUCT is None

    @staticmethod
    def _central_client():
        scripts = str(Path.home() / "Documents/Projetos/central-ia/scripts")
        if scripts not in sys.path:
            sys.path.insert(0, scripts)
        import gerar_audio
        return gerar_audio

    def _generate_waveform(self, *, seed: int | None = None, **kwargs) -> np.ndarray:
        """Use Central IA's seed intelligence and fail-closed ECAPA gate.

        The private Central IA owns generation and biometric validation.
        Never synthesize an unchecked in-process fallback for production.
        """
        for key, expected in GENERATION_OPTIONS.items():
            if kwargs.get(key, expected) != expected:
                raise ValueError(f"production_voice_parameter_mismatch:{key}")
        with self._generation_lock:
            result = self._central_client().generate("celia", "pt", kwargs["text"],
                                                      num_step=10, emotion=kwargs.get("emotion"))
            if result.get("status") != "APPROVED" or not result.get("path"):
                raise OmniVoiceSynthesisError(
                    "NO_APPROVED_AUDIO: nenhum dos quatro candidatos passou no ECAPA"
                )
            if result.get("steps") != 10 or result.get("similarity_ecapa", -1) < 0.60:
                raise OmniVoiceSynthesisError("Central IA returned an unverified voice")
            waveform, rate = sf.read(result["path"], dtype="float32")
            if rate != self.sample_rate:
                raise OmniVoiceSynthesisError("Central IA returned an unexpected sample rate")
            self._validate_waveform_quality(waveform, kwargs["text"])
            self._last_voice_quality = result
            return waveform

    def generate_audio(self, text: str, file_name_no_ext=None, emotion=None, fast=False) -> str:
        """Standard speech uses the approved original/anchor reference router.

        Emotion and fast are compatibility inputs only. Manual Lab presets cannot
        affect this route, and synthesis failure never selects another voice.
        """
        run_id = uuid.uuid4().hex
        started_at = datetime.now(timezone.utc).isoformat()
        started = time.monotonic()
        selected = OmniVoicePreset(VOICE_PROFILE_ID, "V2 production", "Adaptive verified voice", 0.88, 10, 2.0, None)
        cleaned = ""
        voice_profile = production_descriptor()
        output = None
        error = None
        self._last_voice_quality = None
        try:
            self._validate_production_assets()
            cleaned = sanitize_production_tts_text(text)
            if not cleaned:
                raise OmniVoiceSynthesisError("empty_production_tts_text")
            voice_profile = production_descriptor(cleaned)
            validate_reference_descriptor(voice_profile)
            selected = OmniVoicePreset(voice_profile["voice_profile_id"], "V2 production", "ECAPA verified", 0.88, 10, 2.0, None)
            samples = self._generate_waveform(
                text=cleaned, language="pt", emotion=emotion,
                ref_audio=voice_profile["ref_audio"],
                ref_text=voice_profile["ref_text"],
                **({"instruct": MODEL_INSTRUCT} if MODEL_INSTRUCT else {}),
                **GENERATION_OPTIONS,
            )
            selected = replace(selected, seed=int(self._last_voice_quality["seed"]))
            output = Path(self._write_waveform(samples, file_name_no_ext)).resolve()
        except Exception as exc:
            error = exc
            raise
        finally:
            self._write_run_record(
                run_id=run_id, started_at=started_at, started_monotonic=started,
                text=text, generation_text=cleaned, selected=selected,
                requested_emotion=str(emotion) if emotion else None,
                status="error" if error else "ok", error=error, output_path=output,
                voice_profile=voice_profile,
            )
        observability.emit("tts_generated", "service.omnivoice", "ok", {
            "tts_engine": TTS_ENGINE_OMNIVOICE, "audio_generated": True,
            "voice_profile_id": voice_profile["voice_profile_id"], "seed": selected.seed,
            "similarity_ecapa": self._last_voice_quality.get("similarity_ecapa"),
            "attempts": self._last_voice_quality.get("attempts"),
        })
        return str(output)

    async def async_generate_audio(self, text, file_name_no_ext=None, emotion=None, fast=False):
        return await asyncio.to_thread(self.generate_audio, text, file_name_no_ext, emotion, fast)
