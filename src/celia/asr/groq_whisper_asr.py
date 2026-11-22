import io
import time
import wave
import numpy as np
from loguru import logger
from groq import Groq

try:
    from groq import (
        RateLimitError,
        AuthenticationError,
        APIConnectionError,
        APITimeoutError,
    )
except ImportError:  # SDK antigo — fallback genérico
    RateLimitError = Exception
    AuthenticationError = Exception
    APIConnectionError = Exception
    APITimeoutError = Exception

from .asr_interface import ASRInterface


# Cooldowns (segundos): replicam o padrão do tool_agent em conf.yaml
RATE_LIMIT_COOLDOWN_S = 60.0
AUTH_COOLDOWN_S = 900.0
REQUEST_TIMEOUT_S = 15.0


class VoiceRecognition(ASRInterface):
    # sample_rate, n_channels, and sampwidth are defined in asr_interface.py

    def __init__(
        self,
        api_key: str,
        model: str = "distil-whisper-large-v3-en",
        lang: str = "en",
        prompt: str = "",
    ) -> None:
        logger.info("Initializing Groq ASR...")
        # Timeout no client: evita pendurar indefinido em problema de rede
        self.client = Groq(api_key=api_key, timeout=REQUEST_TIMEOUT_S)
        self.lang = lang
        self.model = model
        # Prompt guia o Whisper a entender vocabulário específico (PT-BR + nomes
        # próprios do projeto). Sem prompt ele inventa "Sérgio/Coitinho/rompeito"
        # ao ouvir "Célia". Com prompt curto e direto, vira ground truth.
        self.prompt = (prompt or "").strip()

        # Cooldowns para falhas conhecidas
        self._rate_cooldown_until: float = 0.0
        self._auth_cooldown_until: float = 0.0
        self._last_error: str = ""

    def _in_cooldown(self) -> bool:
        """True se ASR está temporariamente desabilitado (rate-limit ou auth)."""
        now = time.monotonic()
        return now < self._rate_cooldown_until or now < self._auth_cooldown_until

    def _cooldown_remaining(self) -> float:
        now = time.monotonic()
        return max(
            self._rate_cooldown_until - now,
            self._auth_cooldown_until - now,
            0.0,
        )

    def _cooldown_reason(self) -> str:
        now = time.monotonic()
        if now < self._auth_cooldown_until:
            return "auth"
        if now < self._rate_cooldown_until:
            return "rate_limit"
        return ""

    def transcribe_np(self, audio: np.ndarray) -> str:
        """Transcribe speech audio in numpy array format and return the transcription.

        Args:
            audio: The numpy array of the audio data to transcribe.

        Returns:
            String com a transcrição, ou "" em falha. Erros são logados e ativam
            cooldown quando aplicável.
        """
        if self._in_cooldown():
            reason = self._cooldown_reason()
            remaining = self._cooldown_remaining()
            logger.warning(
                f"[ASR] em cooldown ({reason}, {remaining:.0f}s restantes) — descartando buffer"
            )
            return ""

        logger.info("Transcribing audio (GroqWhisperASR)...")

        # Turn the audio into an audio file
        # Make sure the audio is in the range [-1, 1]
        audio = np.clip(audio, -1, 1)
        # Convert the audio to 16-bit PCM
        audio_integer = (audio * 32767).astype(np.int16)

        # groq api requires a file-like object for the audio data, so we use a BytesIO object
        audio_buffer = io.BytesIO()

        with wave.open(audio_buffer, "wb") as wf:
            wf.setnchannels(self.NUM_CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(audio_integer.tobytes())

        audio_bytes = audio_buffer.getvalue()

        # 1 retry para falhas transientes de rede; rate/auth não retryam
        last_exc: Exception | None = None
        for attempt in range(2):
            try:
                kwargs = dict(
                    file=("audio.wav", audio_bytes),
                    model=self.model,
                    response_format="text",
                    language=self.lang,
                    temperature=0.0,
                )
                if self.prompt:
                    kwargs["prompt"] = self.prompt
                transcription = self.client.audio.transcriptions.create(**kwargs)
                self._last_error = ""
                return transcription
            except AuthenticationError as e:
                self._auth_cooldown_until = time.monotonic() + AUTH_COOLDOWN_S
                self._last_error = f"auth: {e}"
                logger.error(
                    f"[ASR] auth falhou — cooldown {AUTH_COOLDOWN_S:.0f}s. "
                    "Verifique GROQ_WHISPER_API_KEY no .env."
                )
                return ""
            except RateLimitError as e:
                self._rate_cooldown_until = time.monotonic() + RATE_LIMIT_COOLDOWN_S
                self._last_error = f"rate_limit: {e}"
                logger.warning(
                    f"[ASR] rate limit — cooldown {RATE_LIMIT_COOLDOWN_S:.0f}s"
                )
                return ""
            except (APIConnectionError, APITimeoutError) as e:
                last_exc = e
                if attempt == 0:
                    logger.warning(f"[ASR] erro de rede ({type(e).__name__}), retry em 2s...")
                    time.sleep(2.0)
                    continue
                self._last_error = f"network: {e}"
                logger.error(f"[ASR] erro de rede definitivo: {e}")
                return ""
            except Exception as e:
                self._last_error = f"unknown: {e}"
                logger.error(f"[ASR] erro inesperado: {type(e).__name__}: {e}")
                return ""

        # Improvável: caiu aqui sem retornar
        logger.error(f"[ASR] esgotou retries: {last_exc}")
        return ""
