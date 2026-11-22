"""ASR OpenAI-compatible (primario + fallback OPCIONAL).

ATENCAO (2026-10-04): em producao o unico ASR e o TAGARELA local (http://127.0.0.1:8011/v1) e NAO ha
fallback configurado. O Whisper local (:8010) foi desinstalado. Se o TAGARELA cair, `transcribe_np` devolve
"" e o monitor de avisos (avisos._asr_fora) avisa que a Celia esta surda. O suporte a fallback abaixo
continua na classe so porque e generico (config opcional), mas nada o usa hoje.

agente: claude | 2026-08-08

Motivo de existir
-----------------
`groq_whisper_asr` fala com o SDK da Groq, que monta o caminho
`/openai/v1/audio/transcriptions`. O gateway 9router so atende
`/v1/audio/transcriptions`. Por isso o adapter da Groq nao pode ser
repontado para o gateway apenas trocando `base_url`.

Este adapter fala o protocolo OpenAI de audio direto por HTTP, entao serve
para qualquer backend compativel:

    - 9router  ->  http://127.0.0.1:20127/v1   (`groq/whisper-large-v3-turbo`)
    - TAGARELA  ->  http://127.0.0.1:8011/v1    (`tagarela-q2-int8`, CPU; ASR de producao)

`groq/whisper-large-v3` e o alias multi-modelo `whisper` sao proibidos pela
politica ASR de 2026-09-08: o mesmo WAV produziu transcricoes divergentes.

Implementa `README_CELIA_COMPLETO.md` secao 5:

    5.1  caminho normal: microfone -> gateway -> texto
    5.2  quando o primario falha, cai para o ASR local e SEGURA nesse estado
         por um tempo (cooldown/histerese), em vez de alternar a cada fala.

Estados expostos em `self.state`, espelhando a tabela da secao 5.3:

    gateway_ready         primario respondendo
    gateway_unavailable   primario falhou; usando local ate o cooldown vencer
    local_only            sem primario configurado, ou so local

Nenhuma credencial aparece em log. Erros sao registrados por tipo, nunca com
o valor da chave.
"""

from __future__ import annotations

import asyncio
import io
import time
import wave

import httpx
import numpy as np
from loguru import logger

from .asr_interface import ASRInterface

# Quanto tempo segurar no fallback antes de tentar o primario de novo.
# Evita ficar alternando gateway/local a cada frase (histerese).
#
# MEDIDO em 2026-10-01 nesta maquina, mesmo WAV de 2 s nos dois lados:
#   primario :20127 groq/whisper-large-v3-turbo ....... 1,035 s
#   fallback :8010  openai/whisper-large-v3-turbo ..... 3,852 s  (3,7x mais lento)
#
# Com hold fixo de 120 s, UM ConnectError isolado condenava o dono a dois
# minutos de ASR 3,7x mais lento. Os logs de 04:51-04:58 mostram 11 quedas do
# primario; cada uma segurava 120 s. Era a origem das transcricoes de 3-4 s.
#
# Agora o hold e progressivo: a primeira falha segura pouco (o caso comum e um
# blip, e o primario volta), e falhas repetidas dobram ate o teto antigo — que
# e o que a histerese realmente queria evitar (alternar a cada frase quando o
# primario esta de fato morto). Um sucesso no primario zera a contagem.
FALLBACK_HOLD_BASE_S = 8.0
FALLBACK_HOLD_MAX_S = 120.0


def _fallback_hold_seconds(falhas_seguidas: int) -> float:
    """8s, 16s, 32s, 64s, 120s… — dobra a cada falha seguida, com teto."""
    expoente = max(0, falhas_seguidas - 1)
    return min(FALLBACK_HOLD_BASE_S * (2.0**expoente), FALLBACK_HOLD_MAX_S)
REQUEST_TIMEOUT_S = 60.0


def _limite_de_cota(exc: Exception) -> bool:
    """O Groq respondeu 429 (limite de cota do Whisper)?"""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 429


def _audio_longo(exc: Exception) -> bool:
    """O primário recusou o trecho por tamanho (413)? Não é falha dele: só este áudio vai ao fallback."""
    return isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 413


BLOCKED_ASR_MODELS = {"whisper", "whisper-large-v3", "groq/whisper-large-v3", "openai/whisper-large-v3"}


class VoiceRecognition(ASRInterface):
    def __init__(
        self,
        base_url: str,
        model: str = "groq/whisper-large-v3-turbo",
        api_key: str | None = None,
        lang: str | None = None,
        prompt: str = "",
        fallback_base_url: str | None = None,
        fallback_model: str | None = None,
        temperature: float = 0.0,
        timeout: float = REQUEST_TIMEOUT_S,
        cloudflare_fallback: bool = False,
    ) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.model = model
        if str(self.model).strip() in BLOCKED_ASR_MODELS:
            raise ValueError(f"ASR model blocked by policy 2026-09-08: {self.model}")
        self.api_key = api_key or None
        self.lang = lang
        self.prompt = (prompt or "").strip()
        self.fallback_base_url = (fallback_base_url or "").rstrip("/") or None
        self.fallback_model = fallback_model or model
        if str(self.fallback_model).strip() in BLOCKED_ASR_MODELS:
            raise ValueError(f"Fallback ASR model blocked by policy 2026-09-08: {self.fallback_model}")
        self.temperature = float(temperature)
        self.timeout = timeout

        # Whisper da Workers AI (3 contas só de ASR) quando o Groq dá 429. Ver cloudflare_whisper.py.
        self._cf = None
        self._hold_por_429 = False
        if cloudflare_fallback:
            try:
                from .cloudflare_whisper import construir

                self._cf = construir()
            except Exception as exc:  # noqa: BLE001 - a reserva nunca impede o ASR de subir
                logger.warning("[ASR] Whisper da Cloudflare não iniciou ({})", type(exc).__name__)

        self._fallback_until: float = 0.0
        # Falhas seguidas do primario, para o backoff progressivo do hold.
        self._falhas_primario: int = 0
        self.last_error: str = ""
        self._health_cache_at: float = 0.0
        self._health_cache: dict[str, bool | None] = {
            "primary": None,
            "fallback": None,
        }

        if not self.base_url and not self.fallback_base_url:
            raise ValueError(
                "openai_compatible_asr precisa de base_url ou fallback_base_url"
            )

        if not self.base_url:
            self.state = "local_only"
        else:
            self.state = "gateway_ready"

        logger.info(
            f"Initializing OpenAI-compatible ASR | primario={self.base_url or '-'} "
            f"modelo={self.model} | fallback={self.fallback_base_url or '-'} "
            f"modelo={self.fallback_model if self.fallback_base_url else '-'}"
        )

    # ------------------------------------------------------------------ #

    def _encode_wav(self, audio: np.ndarray) -> bytes:
        audio = np.clip(audio, -1, 1)
        pcm = (audio * 32767).astype(np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.NUM_CHANNELS)
            wf.setsampwidth(self.SAMPLE_WIDTH)
            wf.setframerate(self.SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        return buf.getvalue()

    def _post(self, base_url: str, model: str, audio_bytes: bytes) -> str:
        headers = {}
        if self.api_key and base_url == self.base_url:
            headers["Authorization"] = f"Bearer {self.api_key}"

        data = {"model": model, "response_format": "json", "temperature": str(self.temperature)}
        if self.lang:
            data["language"] = self.lang
        if self.prompt:
            data["prompt"] = self.prompt

        r = httpx.post(
            f"{base_url}/audio/transcriptions",
            files={"file": ("audio.wav", audio_bytes, "audio/wav")},
            data=data,
            headers=headers,
            timeout=self.timeout,
        )
        r.raise_for_status()
        try:
            return (r.json() or {}).get("text", "").strip()
        except ValueError:
            # response_format=text devolve corpo cru
            return r.text.strip()

    # ------------------------------------------------------------------ #

    def _endpoint_healthy(self, base_url: str | None) -> bool:
        if not base_url:
            return False
        candidates = [f"{base_url}/models"]
        if base_url.endswith("/v1"):
            candidates.append(f"{base_url[:-3]}/health")
        for url in candidates:
            try:
                response = httpx.get(
                    url,
                    timeout=min(3.0, float(self.timeout)),
                )
                if 200 <= response.status_code < 400:
                    return True
            except Exception:
                continue
        return False

    def health_snapshot(self, *, force: bool = False) -> dict[str, bool | None]:
        """Return cached provider health without pretending a dead fallback exists."""
        now = time.monotonic()
        if force or now - self._health_cache_at >= 5.0:
            self._health_cache = {
                "primary": self._endpoint_healthy(self.base_url),
                "fallback": self._endpoint_healthy(self.fallback_base_url),
            }
            self._health_cache_at = now
            logger.info(
                "[ASR HEALTH] primary={} fallback={}",
                "OK" if self._health_cache["primary"] else "FAIL",
                (
                    "OK"
                    if self._health_cache["fallback"]
                    else ("FAIL" if self.fallback_base_url else "DISABLED")
                ),
            )
        return dict(self._health_cache)

    def secondary_transcribe_np(self, audio: np.ndarray) -> str:
        """Transcribe the same WAV on the fallback without changing failover state."""
        if not self.fallback_base_url:
            return ""
        health = self.health_snapshot()
        if health.get("fallback") is not True:
            self.last_error = "fallback:unavailable"
            return ""
        try:
            text = self._post(
                self.fallback_base_url,
                self.fallback_model,
                self._encode_wav(audio),
            )
            self.last_error = ""
            return text
        except Exception as exc:
            self._health_cache["fallback"] = False
            self._health_cache_at = time.monotonic()
            self.last_error = f"fallback:{type(exc).__name__}"
            logger.warning(
                "[ASR] secondary fallback failed ({})",
                type(exc).__name__,
            )
            return ""

    async def async_secondary_transcribe_np(self, audio: np.ndarray) -> str:
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        return await asyncio.to_thread(self.secondary_transcribe_np, audio)

    # ------------------------------------------------------------------ #

    def _tentar_cloudflare(self, audio_bytes: bytes, duracao_s: float) -> str | None:
        """Groq deu 429: transcreve nas contas de ASR da Cloudflare. None = nenhuma respondeu."""
        try:
            texto = self._cf.transcribe(audio_bytes, duracao_s)
        except Exception as exc:  # noqa: BLE001 - inclui CloudflareWhisperUnavailable
            self.last_error = f"cloudflare_asr:{type(exc).__name__}"
            logger.warning("[ASR] Whisper da Cloudflare indisponivel ({}); seguindo para o local", type(exc).__name__)
            return None
        self.state = "cloudflare_asr"
        self.last_error = ""
        logger.info("[ASR] Groq em 429; transcrito pelo Whisper da Cloudflare ({:.1f}s de audio)", duracao_s)
        return texto

    def _fim(self) -> None:
        """Nenhum caminho de ASR respondeu: o ledger dispara o aviso de ASR fora."""
        if self._cf is not None:
            try:
                self._cf.reportar_fim()
            except Exception:  # noqa: BLE001
                pass

    def transcribe_np(self, audio: np.ndarray) -> str:
        audio_bytes = self._encode_wav(audio)
        now = time.monotonic()

        usar_primario = bool(self.base_url) and now >= self._fallback_until

        if usar_primario:
            try:
                texto = self._post(self.base_url, self.model, audio_bytes)
                if self.state != "gateway_ready":
                    logger.info("[ASR] primario voltou; estado=gateway_ready")
                self.state = "gateway_ready"
                self.last_error = ""
                # Sucesso zera o backoff: a proxima queda volta a segurar pouco.
                self._falhas_primario = 0
                self._hold_por_429 = False
                return texto
            except Exception as e:
                self.last_error = f"{type(e).__name__}"
                self._hold_por_429 = _limite_de_cota(e)
                if _audio_longo(e) and self.fallback_base_url:
                    # Trecho acima do limite do primário (ex.: TAGARELA recusa >120 s): vai direto ao
                    # fallback, sem hold e sem contar falha, para as frases curtas seguintes não pagarem.
                    logger.info("[ASR] trecho acima do limite do primario; este audio vai ao fallback (sem hold)")
                elif self.fallback_base_url or (self._hold_por_429 and self._cf is not None):
                    self._falhas_primario += 1
                    hold = _fallback_hold_seconds(self._falhas_primario)
                    self._fallback_until = now + hold
                    self.state = "gateway_unavailable"
                    logger.warning(
                        f"[ASR] primario falhou ({type(e).__name__}); "
                        f"caindo para ASR local por {hold:.0f}s "
                        f"(falha {self._falhas_primario} seguida)"
                    )
                else:
                    logger.error(f"[ASR] primario falhou ({type(e).__name__}) e nao ha fallback")
                    return ""

        if self._hold_por_429 and self._cf is not None:
            texto_cf = self._tentar_cloudflare(audio_bytes, len(audio) / float(self.SAMPLE_RATE))
            if texto_cf is not None:
                return texto_cf

        if not self.fallback_base_url:
            self._fim()
            return ""

        try:
            texto = self._post(self.fallback_base_url, self.fallback_model, audio_bytes)
            self.last_error = ""
            return texto
        except Exception as e:
            self.last_error = f"fallback:{type(e).__name__}"
            logger.error(f"[ASR] fallback local tambem falhou ({type(e).__name__})")
            self._fim()
            return ""
