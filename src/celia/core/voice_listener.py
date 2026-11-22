"""Listener de voz sempre-ligado para a Celia (porta da arquitetura RMS do React).

Infraestrutura, nao MCP tool. Captura o MICROFONE direto do SO (sem depender de
permissao do browser), detecta fala por energia (RMS) com pre-roll e fim por
silencio, transcreve pelo ASR ja configurado (Groq Whisper) e roteia:

  - palavra-chave de interrupcao  -> interrompe a Celia na hora (e limpa a fila)
  - mencao "celia"                -> submete como turno para ela responder
  - demais falas                  -> apenas registradas (contexto), sem responder

Teclas globais (lib `keyboard`, funcionam em qualquer janela):
  - Ctrl+M  liga/desliga a escuta do microfone
  - Ctrl+P  interrompe a Celia

Tudo opcional e tolerante a falha: se nao houver microfone, sounddevice ou
keyboard, o servidor sobe normalmente e o listener apenas se desabilita.

LEGADO — CAPTURA DE MICROFONE DESATIVADA NA ARQUITETURA (2026-08-24)
====================================================================
Ordem do Pai: o projeto passa a ter UM unico proprietario da captura do
microfone fisico principal, e esse proprietario e o NAVEGADOR/FRONTEND.

Medido em 2026-08-24 com `pactl list source-outputs`: tres processos liam a
mesma fonte `mic_studio_rnnoise` ao mesmo tempo — `PipeWire ALSA [python3.11]`
(este arquivo, 16 kHz), `Chromium input` (48 kHz) e OBS. A deduplicacao de
2,5 s no singleton existia para consertar isso DEPOIS; ela continua como
defesa, mas deixou de ser a arquitetura.

O que mudou aqui:
  * `sd.InputStream` NAO abre mais no fluxo normal do projeto;
  * as HOTKEYS (ctrl+m, ctrl+p) continuam ativas — sao responsabilidade
    independente da captura e nao duplicam microfone nenhum;
  * a captura so volta com o escape explicito `CELIA_LEGACY_MIC_CAPTURE=1`,
    que existe para diagnostico e NAO faz parte do boot normal.

Consequencia assumida e registrada: a interrupcao por voz ("calma celia" /
"calada"), que nascia da transcricao deste listener, deixa de existir por esta
via. A interrupcao por hotkey `ctrl+p` continua funcionando.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
import unicodedata
from collections import deque
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np
from loguru import logger


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on", "sim"}


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError:
        logger.warning(f"[VoiceListener] float invalido para {name}: {value!r}; usando {default}")
        return default


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return int(value)
    except ValueError:
        logger.warning(f"[VoiceListener] int invalido para {name}: {value!r}; usando {default}")
        return default


def _normalize(text: str) -> str:
    """lowercase + remove acentos, para casar palavra-chave de forma robusta."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    no_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return no_accents.casefold().strip()


# Frases que mandam a Celia parar de falar (ditas pelo Criador).
INTERRUPT_PHRASES = (
    "calma celia",
    "parou celia",
    "para celia",
    "calada",
    "cala boca",
    "cala a boca",
    "silencio",
    "espera ai celia",
    "espera celia",
)

# Como a Celia percebe que foi citada diretamente.
MENTION_TERMS = ("celia",)

# Alucinacoes comuns do Whisper PT-BR quando recebe ruido/silencio.
# Lista alimentada com logs reais (debug_2026-06-09.log e 2026-06-11.log).
HALLUCINATION_PHRASES = frozenset(
    {
        "e ai",
        "obrigado",
        "obrigada",
        "obrigado.",
        "obrigada.",
        "obrigado, senhor",
        "voce",
        "tchau",
        "valeu",
        "amem",
        "amen",
        "hum",
        "hmm",
        "ah",
        "uh",
        "eh",
        "mae",
        "musica",
        "tipo assim",
        "thanks for watching",
        "thank you for watching",
        "subscribe",
        "like and subscribe",
        "try 11 laps for free",
        "stop searching for the perfect voice",
        # Legendistas de TV brasileiros — alucinacao classica do Whisper PT
        "legenda por sonia ruberti",
        "legenda adriana zanotto",
        "legendas por sonia ruberti",
        "...",
        ".",
    }
)

# Prefixos que delatam alucinacao de legenda (mata variantes futuras de legendista).
_HALLUCINATION_PREFIXES = ("legenda", "legendado", "legendas", "subtitles", "subtitle")


def _is_hallucination(text: str) -> bool:
    """True se o texto transcrito parece alucinacao do Whisper (ruido virou frase)."""
    if not text:
        return True
    t = _normalize(text).rstrip(".,!?…").strip()
    if len(t) < 3:
        return True
    if t in HALLUCINATION_PHRASES:
        return True
    # Qualquer coisa que comece com "legenda*"/"subtitle*" e alucinacao de legenda de TV
    if t.startswith(_HALLUCINATION_PREFIXES):
        return True
    # frase muito curta (<=2 palavras) E todas as palavras na blocklist
    words = t.split()
    if len(words) <= 2 and all(w in HALLUCINATION_PHRASES for w in words):
        return True
    return False


# Correcao fonetica: o Whisper confunde "Celia" com palavras parecidas quando o audio
# e curto/ruidoso. So corrige quando a palavra e o conteudo isolado OU abre a frase —
# evita quebrar "que cena absurda". Resolve o "ela nao me ouve" (sai 'seria'/'cena').
_PHONETIC_CELIA = frozenset(
    {"seria", "selia", "serie", "series", "cena", "selha", "celio", "selio", "celya", "selya"}
)


def _phonetic_celia(text: str) -> str:
    """Use the same contextual vocative rules as the main ASR path."""
    from .asr_normalization import _normalize_celia_vocative
    return _normalize_celia_vocative(text)[0]


@dataclass
class VoiceListenerConfig:
    enabled: bool = True
    device: Optional[str] = None          # nome/indice do mic; None = device padrao do SO
    sample_rate: int = 16000
    chunk: int = 1024
    silence_threshold: float = 0.018      # RMS (float32 [-1,1]); calibravel por env (+5% sobre 0.015)
    silence_duration: float = 1.5         # s de silencio para fechar a fala
    min_speech_duration: float = 0.9      # s minimo para valer transcricao (anti-clique/anti-alucinacao)
    max_speech_duration: float = 30.0     # teto de seguranca para falas sem pausa
    pre_roll_chunks: int = 10             # chunks guardados antes do inicio da fala
    hotkeys_enabled: bool = True

    @classmethod
    def from_env(cls) -> "VoiceListenerConfig":
        return cls(
            enabled=_env_bool("CELIA_VOICE_LISTENER_ENABLED", True),
            device=(os.getenv("CELIA_MIC_DEVICE") or None),
            sample_rate=_env_int("CELIA_VOICE_SAMPLE_RATE", 16000),
            chunk=_env_int("CELIA_VOICE_CHUNK", 1024),
            silence_threshold=_env_float("CELIA_VOICE_SILENCE_THRESHOLD", 0.018),
            silence_duration=_env_float("CELIA_VOICE_SILENCE_DURATION", 1.5),
            min_speech_duration=_env_float("CELIA_VOICE_MIN_SPEECH", 0.9),
            max_speech_duration=_env_float("CELIA_VOICE_MAX_SPEECH", 30.0),
            pre_roll_chunks=_env_int("CELIA_VOICE_PRE_ROLL", 10),
            hotkeys_enabled=_env_bool("CELIA_VOICE_HOTKEYS", True),
        )


class VoiceListener:
    """Escuta o microfone num thread dedicado e fala com o singleton via o event loop."""

    def __init__(self, singleton: Any, config: Optional[VoiceListenerConfig] = None) -> None:
        self.singleton = singleton
        self.config = config or VoiceListenerConfig.from_env()
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._mic_on = threading.Event()
        self._mic_on.set()  # escuta comeca ligada
        self._hotkeys_bound = False

    # ─────────────────────────────────────────────────────────── ciclo de vida ──

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> bool:
        if not self.config.enabled:
            logger.info("[VoiceListener] desabilitado por env (CELIA_VOICE_LISTENER_ENABLED).")
            return False
        if self.is_running:
            return True
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None
        self._stop.clear()
        # Um microfone -> uma captura. O dono e o navegador. Este caminho so
        # abre `sd.InputStream` sob escape explicito de diagnostico.
        if _env_bool("CELIA_LEGACY_MIC_CAPTURE", False):
            self._thread = threading.Thread(
                target=self._audio_loop, name="celia-voice-listener-legado", daemon=True
            )
            self._thread.start()
            logger.warning(
                "[VoiceListener] CAPTURA LEGADA LIGADA por CELIA_LEGACY_MIC_CAPTURE=1. "
                "Isto reabre o microfone que o navegador ja possui e volta a duplicar "
                "turno. Use so para diagnostico."
            )
        else:
            self._thread = None
            logger.info(
                "[VoiceListener] captura de microfone desativada por arquitetura: "
                "o dono da fonte fisica e o navegador. Hotkeys seguem ativas."
            )
        self._setup_hotkeys()
        return True

    async def stop(self) -> None:
        self._stop.set()
        self._teardown_hotkeys()
        thread = self._thread
        if thread:
            await asyncio.to_thread(thread.join, 3.0)
        self._thread = None

    # ─────────────────────────────────────────────────────────────── teclas ────

    def _setup_hotkeys(self) -> None:
        if not self.config.hotkeys_enabled:
            return
        try:
            import keyboard  # type: ignore
        except Exception as exc:
            logger.warning(f"[VoiceListener] teclas globais indisponiveis (keyboard): {exc}")
            return
        try:
            keyboard.add_hotkey("ctrl+m", self._toggle_mic, suppress=True)
            keyboard.add_hotkey("ctrl+p", self._hotkey_interrupt, suppress=True)
            self._hotkeys_bound = True
            logger.info("[VoiceListener] teclas: Ctrl+M (liga/desliga mic), Ctrl+P (interromper).")
        except Exception as exc:
            logger.warning(f"[VoiceListener] falha ao registrar teclas globais: {exc}")

    def _teardown_hotkeys(self) -> None:
        if not self._hotkeys_bound:
            return
        try:
            import keyboard  # type: ignore

            keyboard.remove_hotkey("ctrl+m")
            keyboard.remove_hotkey("ctrl+p")
        except Exception:
            pass
        self._hotkeys_bound = False

    def _toggle_mic(self) -> None:
        if self._mic_on.is_set():
            self._mic_on.clear()
            logger.info("[VoiceListener] microfone DESLIGADO (Ctrl+M).")
        else:
            self._mic_on.set()
            logger.info("[VoiceListener] microfone LIGADO (Ctrl+M).")

    def _hotkey_interrupt(self) -> None:
        logger.info("[VoiceListener] interrupcao por tecla (Ctrl+P).")
        self._interrupt_celia()

    # ──────────────────────────────────────────────────────── loop de captura ──

    def _rms(self, block: np.ndarray) -> float:
        if block.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))

    def _audio_loop(self) -> None:
        try:
            import sounddevice as sd
        except Exception as exc:
            logger.warning(f"[VoiceListener] sounddevice indisponivel, listener off: {exc}")
            return

        cfg = self.config
        chunk = cfg.chunk
        rate = cfg.sample_rate
        silence_chunks_needed = max(1, int(rate / chunk * cfg.silence_duration))
        min_speech_chunks = max(1, int(rate / chunk * cfg.min_speech_duration))
        max_speech_chunks = max(1, int(rate / chunk * cfg.max_speech_duration))

        pre_roll: deque = deque(maxlen=cfg.pre_roll_chunks)
        speech_buf: list = []
        silence_count = 0
        in_speech = False

        try:
            stream = sd.InputStream(
                samplerate=rate,
                channels=1,
                dtype="float32",
                blocksize=chunk,
                device=cfg.device,
            )
        except Exception as exc:
            logger.warning(f"[VoiceListener] nao consegui abrir o microfone, listener off: {exc}")
            return

        logger.info("[VoiceListener] escutando o microfone…")
        with stream:
            while not self._stop.is_set():
                try:
                    data, _overflow = stream.read(chunk)
                except Exception as exc:
                    logger.warning(f"[VoiceListener] erro lendo o microfone: {exc}")
                    break

                # Mic desligado por tecla: zera estado e ignora.
                if not self._mic_on.is_set():
                    if in_speech:
                        in_speech = False
                        speech_buf = []
                        silence_count = 0
                        pre_roll.clear()
                    continue

                block = data[:, 0] if data.ndim > 1 else data
                energy = self._rms(block)
                is_voice = energy > cfg.silence_threshold

                if not in_speech:
                    pre_roll.append(block.copy())
                    if is_voice:
                        in_speech = True
                        silence_count = 0
                        speech_buf = [b for b in pre_roll]
                else:
                    speech_buf.append(block.copy())
                    if is_voice:
                        silence_count = 0
                    else:
                        silence_count += 1

                    fim_por_silencio = silence_count >= silence_chunks_needed
                    fim_por_teto = len(speech_buf) >= max_speech_chunks
                    if fim_por_silencio or fim_por_teto:
                        in_speech = False
                        pre_roll.clear()
                        if len(speech_buf) >= min_speech_chunks:
                            audio = np.concatenate(speech_buf).astype(np.float32)
                            self._handle_utterance(audio)
                        speech_buf = []
                        silence_count = 0

        logger.info("[VoiceListener] loop de captura encerrado.")

    # ───────────────────────────────────────────────────── pos-transcricao ────

    def _handle_utterance(self, audio: np.ndarray) -> None:
        """Transcreve a fala e roteia (interrupcao / mencao). Roda no thread do listener."""
        text = self._transcribe(audio)
        if not text:
            return
        if _is_hallucination(text):
            logger.debug(f"[VoiceListener] alucinacao filtrada: {text!r}")
            return
        # Conserta "Celia" mal-transcrito (seria/cena/serie...) antes de rotear.
        fixed = _phonetic_celia(text)
        if fixed != text:
            logger.info(f"[VoiceListener] fonetico: {text!r} -> {fixed!r}")
            text = fixed
        norm = _normalize(text)
        logger.info(f"[VoiceListener] transcricao: {text!r}")

        # Linux: browser mic pode nao funcionar (Firefox HTTP bloqueia getUserMedia).
        # Neste caso o voice_listener server-side precisa fazer TUDO: mencion + stop.
        # O browser e o listener compartilham o mesmo mic fisico; prioridade do listener.
        # Sempre transmite transcricao pro frontend (aparece no chat).
        self._broadcast_transcription(text)

        if any(phrase in norm for phrase in INTERRUPT_PHRASES):
            logger.info("[VoiceListener] palavra-chave de interrupcao detectada.")
            self._interrupt_celia()
            return
        if any(term in norm for term in MENTION_TERMS):
            logger.info(f"[VoiceListener] mencao detectada: {text!r}")
            self._submit_turn(text)
            return
        return

    def _transcribe(self, audio: np.ndarray) -> str:
        asr = getattr(getattr(self.singleton, "context", None), "asr_engine", None)
        if asr is None or self._loop is None:
            return ""
        try:
            fut = asyncio.run_coroutine_threadsafe(
                asr.async_transcribe_np(audio), self._loop
            )
            return (fut.result(timeout=30.0) or "").strip()
        except Exception as exc:
            logger.warning(f"[VoiceListener] falha na transcricao: {exc}")
            return ""

    def _run_coro(self, coro) -> None:
        if self._loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(coro, self._loop)
        except Exception as exc:
            logger.warning(f"[VoiceListener] falha ao agendar coro: {exc}")

    def _interrupt_celia(self) -> None:
        async def _do() -> None:
            try:
                await self.singleton.interrupt(role="browser", force=True)
            except Exception as exc:
                logger.warning(f"[VoiceListener] interrupt falhou: {exc}")
            try:
                self.singleton.clear_pending_inputs()
            except Exception:
                pass

        self._run_coro(_do())

    def _submit_turn(self, text: str) -> None:
        speaker = "Criador"
        ctx = getattr(self.singleton, "context", None)
        if ctx is not None:
            speaker = getattr(getattr(ctx, "character_config", None), "human_name", "Criador") or "Criador"
        logger.info(f"[VoiceListener] Celia citada -> submetendo turno ({speaker}).")
        self._run_coro(
            self.singleton.submit_input(text, role="source", speaker_name=speaker, origin="owner")
        )

    def _broadcast_transcription(self, text: str) -> None:
        self._run_coro(
            self.singleton.broadcast(
                {
                    "type": "user-input-transcription",
                    "source": "microphone",
                    "text": text,
                }
            )
        )
