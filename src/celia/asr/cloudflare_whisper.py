"""Whisper da Workers AI (Cloudflare) como reserva do Whisper do Groq.

Quando o Groq responde 429, `openai_compatible_asr.VoiceRecognition` chama este adaptador. Ele
fala o endpoint nativo da Cloudflare (não o do Groq, nem o do 9router):

    POST https://api.cloudflare.com/client/v4/accounts/{conta}/ai/run/@cf/openai/whisper-large-v3-turbo
    corpo {"audio": <WAV em base64>, "language": "pt"}  ->  {"result": {"text": ...}, "success": true}

Entra o WAV que a Célia já monta, sai o texto simples que ela já usa.

Só usa as 3 contas de ASR (`cloudflare_contas.asr`), em rodízio. Cada conta é um elo do
QuotaLedger (`cf-asr/conta1..3`, provedor `cloudflare-asr`, `block_scope: model`): o 429 de uma
conta tira só ela. O gasto vai para o provedor `cloudflare-asr`, separado do `cloudflare` do
texto, em neurons estimados por minuto de áudio. Credenciais nunca vão para log.
"""

from __future__ import annotations

import base64
import os
import threading
import time
from typing import Any

import httpx
from loguru import logger

from ..core.quota_ledger import classify_error

API = "https://api.cloudflare.com/client/v4/accounts"
MODEL = "@cf/openai/whisper-large-v3-turbo"
# US$ 0,00051 por minuto de áudio ÷ US$ 0,011 por mil neurons ≈ 46,4 neurons/min.
NEURONS_PER_AUDIO_MIN = 46.4
BRAIN = "asr"


class CloudflareWhisperUnavailable(Exception):
    """Nenhuma das contas de ASR conseguiu transcrever."""


class _HttpError(Exception):
    def __init__(self, status_code: int, text: str) -> None:
        super().__init__(f"[{status_code}] {text}")
        self.status_code = status_code


class CloudflareWhisperPool:
    def __init__(self, contas: list[dict[str, str]], ledger: Any, *, model: str = MODEL,
                 language: str | None = None, timeout: float = 20.0,
                 neurons_per_min: float = NEURONS_PER_AUDIO_MIN, client: Any = None) -> None:
        self.contas = contas
        self.ledger = ledger
        self.model = model
        self.language = language
        self.timeout = timeout
        self.neurons_per_min = neurons_per_min
        self._client = client or httpx.Client()
        self._next = 0
        self._lock = threading.Lock()

    def _ordem(self) -> list[int]:
        with self._lock:
            n = len(self.contas)
            return [(self._next + k) % n for k in range(n)]

    def transcribe(self, wav: bytes, duration_sec: float) -> str:
        payload: dict[str, Any] = {"audio": base64.b64encode(wav).decode("ascii")}
        if self.language:
            payload["language"] = self.language
        puladas: list[str] = []
        for idx in self._ordem():
            conta = self.contas[idx]
            link = conta["link"]
            usavel, why = self.ledger.can_use(link, BRAIN)
            if not usavel:
                puladas.append(f"{link}: {why}")
                continue
            inicio = time.monotonic()
            try:
                resp = self._client.post(
                    f"{API}/{conta['account_id']}/ai/run/{self.model}",
                    headers={"Authorization": f"Bearer {conta['token']}"},
                    json=payload, timeout=self.timeout)
                if resp.status_code >= 400:
                    raise _HttpError(resp.status_code, " ".join(resp.text.split())[:200])
                corpo = resp.json()
                if corpo.get("success") is False:
                    raise ValueError("success=false")
                texto = str(((corpo.get("result") or {}).get("text")) or "").strip()
            except _HttpError as exc:
                code, pausa, msg = classify_error(exc)
                self.ledger.record(BRAIN, link, code, total_ms=(time.monotonic() - inicio) * 1000,
                                   message=msg, pause_sec=pausa)
                continue
            except ValueError:
                self.ledger.record(BRAIN, link, "JSN", total_ms=(time.monotonic() - inicio) * 1000,
                                   message="resposta fora do formato da Workers AI")
                continue
            except Exception as exc:  # noqa: BLE001 - só o tipo, nunca a URL (tem o id da conta)
                self.ledger.record(BRAIN, link, "ERR", total_ms=(time.monotonic() - inicio) * 1000,
                                   message=type(exc).__name__, pause_sec=30.0)
                continue
            ms = (time.monotonic() - inicio) * 1000
            self.ledger.record(BRAIN, link, "OK", ttft_ms=ms, total_ms=ms,
                               units_override=max(0.0, duration_sec) / 60.0 * self.neurons_per_min)
            with self._lock:
                self._next = (idx + 1) % len(self.contas)
            return texto
        raise CloudflareWhisperUnavailable("; ".join(puladas)[:200] or "todas as contas falharam")

    def reportar_fim(self) -> None:
        """Todo o ASR falhou (Groq, estas contas e o local): vira o aviso 🔴 de ASR fora."""
        self.ledger.record(BRAIN, "", "FIM", message="ASR sem nenhum caminho")


def construir(cfg: dict[str, Any] | None = None) -> CloudflareWhisperPool | None:
    """Pool pronto a partir do config/model_chains.yaml, ou None se estiver desligado/sem contas."""
    from ..core.cloudflare_contas import carregar_contas, validar_separacao
    from ..core.model_chain import load_config
    from ..core.quota_ledger import get_ledger

    cfg = cfg if cfg is not None else load_config()
    sec = cfg.get("cloudflare_asr") or {}
    if os.environ.get("CELIA_ASR_CF") == "0" or not sec.get("enabled", False):
        return None
    validar_separacao(cfg)
    contas = carregar_contas(cfg, "asr")
    if not contas:
        logger.warning("[ASR] Whisper da Cloudflare ligado, mas sem contas de ASR utilizáveis")
        return None
    pool = CloudflareWhisperPool(
        contas, get_ledger(cfg), model=str(sec.get("model") or MODEL), language=sec.get("language"),
        timeout=float(sec.get("timeout_sec", 20.0)),
        neurons_per_min=float(sec.get("neurons_per_audio_min", NEURONS_PER_AUDIO_MIN)))
    logger.info("[ASR] Whisper da Cloudflare como reserva do Groq: {} contas de ASR", len(contas))
    return pool
