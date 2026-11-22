"""
Camada de orquestração inteligente da Célia (Brain Layer).

Responsabilidades:
  - Manter log de contexto (assunto atual) em RAM
  - Detectar Green Light (conclusão de assunto / convite à Célia)
  - Injetar contexto acumulado quando Célia vai comentar
  - (Opcional) Refinamento de transcrição via Gemma 2 9B no NVIDIA NIM

Variáveis de ambiente:
  NVIDIA_API_KEY      Chave NVIDIA NIM — quando presente, ativa refinamento.
  CELIA_GEMMA_MODEL   Modelo NIM (default: google/gemma-2-9b-it)
  CELIA_GEMMA_URL     URL da API (default: https://integrate.api.nvidia.com/v1/chat/completions)
  CELIA_GEMMA_TIMEOUT Timeout em segundos (default: 0.45)
  CELIA_GL_COOLDOWN   Segundos entre dois green-lights (default: 25)
  CELIA_CTX_MAX_TURNS Máximo de turnos no log (default: 24)
  CELIA_CTX_MAX_AGE   Idade máxima de um turno em segundos (default: 300)
"""

from __future__ import annotations

import difflib
import json
import os
import random
import re
import time
import threading
import unicodedata
from collections import deque
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger


def _strip_accents(text: str) -> str:
    """lowercase + remove acentos — a fala/transcrição varia (com/sem acento)."""
    nfkd = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


# ─── Introspection patterns ("por que você não fala?") ───────────────────────
# Aplicados sobre o texto SEM acento, por isso escritos sem acento.
_INTROSPECTION_PATTERNS = [
    r"\b(por\s*que|porque)\s+(voce|vc|ce|tu)\s+nao\s+fala",
    r"\bo\s+que\s+(voce|vc|ce|tu)\s+(esta|ta)\s+pensando",
    r"\bno\s+que\s+(voce|vc|ce|tu)\s+(esta|ta)\s+pensando",
    r"\bo\s+que\s+(voce|vc|ce|tu)\s+pensa\b",
    r"\bcade\s+(voce|vc|ce|tu)\b",
    r"\bcade\s+a?\s*celia\b",
    r"\bcelia[,\s]+(voce|vc|ce)\s+sumiu",
    r"\b(ta|esta)\s+(viva|acordada|ai|por\s*ai)\s*,?\s*celia",
    r"\b(ta|esta|tao|muito)\s+calad[ao]\b",
    r"\bfala\s+alguma\s+coisa\b",
]
_INTROSPECTION_RE = [re.compile(p, re.IGNORECASE) for p in _INTROSPECTION_PATTERNS]


def detect_introspection(text: str) -> bool:
    """True se o Criador está cobrando o monólogo interno da Célia."""
    norm = _strip_accents(text)
    for pattern in _INTROSPECTION_RE:
        if pattern.search(norm):
            return True
    return False


# ─── Backchannel (micro-reações de fundo) ────────────────────────────────────
# Interjeições curtíssimas que mostram que ela está ouvindo SEM cortar o Criador.
_BACKCHANNEL_INTERJECTIONS = [
    "hum-hum", "uhum", "sei", "eita", "aham", "nossa", "hã", "boa", "entendi", "tá"
]
# Emoção/expressão Live2D que acompanha a micro-reação.
_BACKCHANNEL_EXPRESSIONS = ["curious", "think", "joy", "neutral"]


# ─── Green Light patterns ─────────────────────────────────────────────────────
_GL_PATTERNS = [
    r"\bné\s+c[ée]lia\b",
    r"\bc[ée]lia[,\s]+o\s+que\s+(você|vc)\s+acha",
    r"\bvocê\s+acha\b",
    r"\bce\s+acha\b",
    r"\bentenderam\b",
    r"\bé\s+isso\s+(pessoal|galera|gente)\b",
    r"\balguma\s+(dúvida|pergunta)\b",
    r"\bvocês?\s+gostaram\b",
    r"\bo\s+que\s+acham\b",
    r"\bé\s+isso\s+aí\b",
    r"\bfala\s+c[ée]lia\b",
    r"\bc[ée]lia[,!]?\s*(me\s+ajuda|o\s+que\s+(você|vc)\s+pensa|o\s+que\s+você\s+acha)",
    r"\bvamos?\s+ouvir\s+a\s+c[ée]lia\b",
    r"\bc[ée]lia\s*,\s*(vai|fala|comenta|conta)",
]
_GL_RE = [re.compile(p, re.IGNORECASE) for p in _GL_PATTERNS]


# ─── Data structures ─────────────────────────────────────────────────────────
@dataclass
class Utterance:
    speaker: str
    text: str
    ts: float = field(default_factory=time.time)


class ContextLog:
    """Rolling window de utterances recentes em RAM."""

    def __init__(
        self,
        max_turns: int = 24,
        max_age_sec: float = 300.0,
    ) -> None:
        self._lock = threading.Lock()
        self._turns: deque[Utterance] = deque(maxlen=max_turns)
        self._max_age = max_age_sec

    def add(self, speaker: str, text: str) -> None:
        text = text.strip()
        if not text:
            return
        with self._lock:
            self._turns.append(Utterance(speaker=speaker, text=text))

    def recent_summary(self, max_chars: int = 900) -> str:
        """Retorna as falas recentes como bloco de texto."""
        now = time.time()
        with self._lock:
            recent = [u for u in self._turns if now - u.ts <= self._max_age]
        if not recent:
            return ""
        lines = [f"{u.speaker}: {u.text}" for u in recent[-12:]]
        body = "\n".join(lines)
        if len(body) > max_chars:
            body = "…" + body[-max_chars:]
        return body

    def clear(self) -> None:
        with self._lock:
            self._turns.clear()


# ─── Green Light detection ────────────────────────────────────────────────────
def detect_green_light(text: str) -> bool:
    """Retorna True se o texto indica conclusão de assunto ou convite à Célia."""
    for pattern in _GL_RE:
        if pattern.search(text):
            return True
    return False


# "Célia" é intenção; "séria" é apenas um vizinho acústico possível.
# O texto canônico NÃO é reescrito. A aproximação só vale em formas de vocativo.
_PROBABLE_CELIA_VOCATIVE_RE = [
    re.compile(r"^\s*(?:ei|oi|o|ô)?\s*[,!:-]?\s*s[eé]ria\b\s*[,!:-]\s*.+", re.IGNORECASE),
    re.compile(r"\b(?:ei|oi|o|ô)\s+s[eé]ria\b", re.IGNORECASE),
    re.compile(r"\bs[eé]ria\s+(?:faz|fala|olha|v[eê]|vai|vem|para|volta|comenta|conta|ajuda)\b", re.IGNORECASE),
    re.compile(r"\bn[eé]\s*,?\s*s[eé]ria\s*[?!.]*\s*$", re.IGNORECASE),
    re.compile(r"^\s*(?:(?:ei|oi|o|ô)\s*[,!]?\s*)?(?:s[eé]ria\s*[,!.!?]?\s*){1,3}$", re.IGNORECASE),
]


def detect_celia_vocative(text: str) -> bool:
    """Detecta chamada à Célia sem transformar toda ocorrência de 'séria' no nome."""
    if not text or not text.strip():
        return False
    if re.search(r"\bc[ée]lia\b", text, re.IGNORECASE):
        return True
    return any(pattern.search(text) for pattern in _PROBABLE_CELIA_VOCATIVE_RE)


def build_context_prompt(context_summary: str, trigger_text: str) -> str:
    """Monta o prompt de contexto que será injetado como input da Célia."""
    return (
        "[CONTEXTO DO QUE FOI FALADO RECENTEMENTE]\n"
        f"{context_summary}\n\n"
        "[FRASE QUE CONVIDOU VOCÊ A FALAR]\n"
        f"{trigger_text}\n\n"
        "Com base nisso, faça um comentário natural e pertinente. "
        "Seja espontânea, use seu jeito debochado e cheio de si."
    )


# ─── Gemma transcript judge (opcional) ──────────────────────────────────────
@dataclass(frozen=True)
class TranscriptRefinement:
    status: str
    text: str
    latency_ms: int = 0
    transport_status: str = "ok"
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "status": self.status,
            "text": self.text,
            "latency_ms": self.latency_ms,
            "transport_status": self.transport_status,
            "reason": self.reason,
        }


def _safe_local_correction(original: str, candidate: str) -> bool:
    """Aceita só correção local; reescrita ampla vira UNCERTAIN."""
    original = (original or "").strip()
    candidate = (candidate or "").strip()
    if not original or not candidate:
        return False
    if original == candidate:
        return True
    original_tokens = original.split()
    candidate_tokens = candidate.split()
    if abs(len(original_tokens) - len(candidate_tokens)) > max(2, len(original_tokens) // 4):
        return False

    # Mudanças pequenas de caracteres ainda podem inverter sentido.
    polarity_words = {"nao", "nunca", "jamais", "nem", "sim"}
    original_norm = {_strip_accents(token).strip(".,!?;:") for token in original_tokens}
    candidate_norm = {_strip_accents(token).strip(".,!?;:") for token in candidate_tokens}
    if (original_norm & polarity_words) != (candidate_norm & polarity_words):
        return False
    if re.findall(r"\b\d+(?:[.,]\d+)?\b", original) != re.findall(
        r"\b\d+(?:[.,]\d+)?\b", candidate
    ):
        return False

    length_ratio = len(candidate) / max(1, len(original))
    if not 0.65 <= length_ratio <= 1.35:
        return False
    return difflib.SequenceMatcher(
        None, original.casefold(), candidate.casefold()
    ).ratio() >= 0.72


async def maybe_refine_transcript(
    text: str,
    context_summary: str = "",
) -> TranscriptRefinement:
    """Julga ASR como KEEP/CORRECT/UNCERTAIN sem inventar frase substituta."""
    text = (text or "").strip()
    started = time.monotonic()
    api_key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not text:
        return TranscriptRefinement("KEEP", text, 0, "disabled", "empty")
    if not api_key:
        return TranscriptRefinement("KEEP", text, 0, "disabled", "no_api_key")

    import asyncio
    import urllib.request

    model = os.getenv("CELIA_GEMMA_MODEL", "google/gemma-2-9b-it")
    url = os.getenv(
        "CELIA_GEMMA_URL",
        "https://integrate.api.nvidia.com/v1/chat/completions",
    )
    timeout = float(os.getenv("CELIA_GEMMA_TIMEOUT", "0.45"))
    context_summary = (context_summary or "").strip()[-700:] or "(sem contexto anterior útil)"

    body = json.dumps({
        "model": model,
        "messages": [{
            "role": "user",
            "content": (
                "Você é um juiz conservador de transcrição ASR em português brasileiro. "
                "Não reescreva estilo, gramática ou intenção. Não complete fala ausente. "
                "Use o contexto apenas para desambiguar erro acústico local fortemente sustentado. "
                "Escolha KEEP se o texto é plausível; CORRECT só para correção local óbvia; "
                "UNCERTAIN se há lixo, ambiguidade ou mais de uma leitura plausível. "
                "Em KEEP e UNCERTAIN devolva o texto original. Responda SOMENTE JSON: "
                "{\"status\":\"KEEP|CORRECT|UNCERTAIN\",\"text\":\"...\",\"reason\":\"curto\"}.\n\n"
                f"CONTEXTO RECENTE:\n{context_summary}\n\nTRANSCRIÇÃO:\n{text}"
            ),
        }],
        "temperature": 0.0,
        "max_tokens": 192,
        "stream": False,
    }).encode("utf-8")

    def _call() -> str:
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                .strip()
            )

    try:
        raw = await asyncio.wait_for(asyncio.to_thread(_call), timeout=timeout + 0.15)
    except asyncio.TimeoutError:
        elapsed = int((time.monotonic() - started) * 1000)
        return TranscriptRefinement("KEEP", text, elapsed, "timeout", "timeout")
    except Exception as exc:
        elapsed = int((time.monotonic() - started) * 1000)
        return TranscriptRefinement("KEEP", text, elapsed, "error", type(exc).__name__)

    elapsed = int((time.monotonic() - started) * 1000)
    raw = (raw or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
    try:
        left, right = raw.find("{"), raw.rfind("}")
        payload = json.loads(raw[left:right + 1] if left >= 0 and right >= left else raw)
    except Exception:
        return TranscriptRefinement("UNCERTAIN", text, elapsed, "invalid", "invalid_json")

    status = str(payload.get("status") or "").strip().upper()
    reason = str(payload.get("reason") or "").strip()[:120]
    candidate = str(payload.get("text") or text).strip() or text
    if status == "KEEP":
        return TranscriptRefinement("KEEP", text, elapsed, "ok", reason or "keep")
    if status == "UNCERTAIN":
        return TranscriptRefinement("UNCERTAIN", text, elapsed, "ok", reason or "uncertain")
    if status == "CORRECT":
        if candidate == text:
            return TranscriptRefinement("KEEP", text, elapsed, "ok", reason or "no_change")
        if _safe_local_correction(text, candidate):
            return TranscriptRefinement("CORRECT", candidate, elapsed, "ok", reason or "local")
        return TranscriptRefinement(
            "UNCERTAIN", text, elapsed, "ok", "correction_rejected_as_nonlocal"
        )
    return TranscriptRefinement("UNCERTAIN", text, elapsed, "invalid", "invalid_status")


# ─── Orchestrator ─────────────────────────────────────────────────────────────
class Orchestrator:
    """
    Ponto central de orquestração.

    Prioridades de fala:
      10 — Emergência Minecraft (HP baixo, creeper perto)
      20 — Comando direto do Criador ("Célia, faz X")
      30 — Conclusão de tópico (green light detectado)
      40 — Curadoria do chat
    """

    PRIORITY_MINECRAFT_EMERGENCY = 10
    PRIORITY_DIRECT_COMMAND = 20
    PRIORITY_TOPIC_CONCLUSION = 30
    PRIORITY_CHAT_CURATION = 40

    def __init__(self) -> None:
        self.context_log = ContextLog(
            max_turns=int(os.getenv("CELIA_CTX_MAX_TURNS", "24")),
            max_age_sec=float(os.getenv("CELIA_CTX_MAX_AGE", "300")),
        )
        self._gl_cooldown_sec = float(os.getenv("CELIA_GL_COOLDOWN", "25"))
        self._gl_cooldown_until: float = 0.0
        # Backchannel (micro-reações de fundo)
        self._bc_cooldown_sec = float(os.getenv("CELIA_BACKCHANNEL_COOLDOWN", "14"))
        self._bc_min_chars = int(os.getenv("CELIA_BACKCHANNEL_MIN_CHARS", "60"))
        self._bc_cooldown_until: float = 0.0

    # ── Criador ────────────────────────────────────────────────────────────────
    def process_criador_utterance(self, text: str) -> tuple[str, Optional[str]]:
        """
        Adiciona fala do Criador ao log e verifica green light ou comando direto.

        Returns:
            tuple (intent, payload):
                - "INTROSPECTION", texto original (gatilho do monólogo interno)
                - "GREEN_LIGHT", prompt de contexto enriquecido
                - "DIRECT_COMMAND", texto original (com ou sem contexto)
                - "SILENT_LOG", None
        """
        if not text or not text.strip():
            return "SILENT_LOG", None

        self.context_log.add("Criador", text)

        # 0. Introspecção: tem prioridade máxima — entrega o monólogo interno.
        if detect_introspection(text):
            logger.info(f"[Orchestrator] 🧠 Introspecção: '{text[:70]}'")
            return "INTROSPECTION", text

        # 1. Verifica Green Light
        if time.time() >= self._gl_cooldown_until and detect_green_light(text):
            summary = self.context_log.recent_summary()
            if summary:
                self._gl_cooldown_until = time.time() + self._gl_cooldown_sec
                logger.info(f"[Orchestrator] 🟢 Green light: '{text[:70]}'")
                return "GREEN_LIGHT", build_context_prompt(summary, text)

        # 2. Verifica comando direto. "séria" só conta quando tem forma de vocativo;
        # nunca reescrevemos a transcrição para forçar o nome.
        if detect_celia_vocative(text):
            logger.info(f"[Orchestrator] ⚡ Direct Command/vocativo: '{text[:70]}'")
            return "DIRECT_COMMAND", text

        # 3. Apenas registro silencioso do contexto
        return "SILENT_LOG", None

    # ── Célia ─────────────────────────────────────────────────────────────────
    def process_celia_utterance(self, text: str) -> None:
        """Registra fala da Célia no log de contexto."""
        self.context_log.add("Célia", text)

    # ── Micro-reações de fundo (backchannel) ───────────────────────────────────
    def maybe_backchannel(self, text: str) -> Optional[dict]:
        """
        Decide uma micro-reação de fundo enquanto o criador fala longo (SILENT_LOG).
        São interjeições curtíssimas ("hum-hum", "eita") + uma micro-expressão
        Live2D, que mostram presença SEM cortar a fala dele.

        Retorna {"text": str, "expression": str} ou None (cooldown / fala curta).
        """
        if not text or len(text.strip()) < self._bc_min_chars:
            return None
        now = time.time()
        if now < self._bc_cooldown_until:
            return None
        self._bc_cooldown_until = now + self._bc_cooldown_sec
        return {
            "text": random.choice(_BACKCHANNEL_INTERJECTIONS),
            "expression": random.choice(_BACKCHANNEL_EXPRESSIONS),
        }

    # ── Controle ──────────────────────────────────────────────────────────────
    def clear_context(self) -> None:
        self.context_log.clear()
        self._gl_cooldown_until = 0.0
        logger.info("[Orchestrator] Contexto limpo.")

    def force_green_light(self) -> Optional[str]:
        """Força um green light imediato (para uso manual/debug)."""
        summary = self.context_log.recent_summary()
        if not summary:
            return None
        self._gl_cooldown_until = time.time() + self._gl_cooldown_sec
        return build_context_prompt(summary, "[Criador convidou a Célia a comentar]")


# Singleton global
orchestrator = Orchestrator()
