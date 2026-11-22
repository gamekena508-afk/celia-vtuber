"""Turn-level gate/assembler between ASR text and Célia's brain.

The acoustic gate answers only whether a buffer is worth transcribing. This
module answers the different question that bit the recording on 2026-09-22:
"is this transcript strong enough to become a conversational turn?"

Very short voiced fragments are never promoted just because the waveform is
loud. They are rejected or held for a neighbouring fragment, while a small
set of explicit owner commands remains usable.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
from difflib import SequenceMatcher
import re
import unicodedata


def _fold(text: str) -> str:
    value = unicodedata.normalize("NFKD", str(text or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9 ]+", " ", value.casefold())
    return re.sub(r"\s+", " ", value).strip()


_EXPLICIT_SHORT_COMMANDS = {
    "sim", "nao", "para", "pare", "joga", "jogue", "continua", "continue",
    "repete", "repita", "volta", "volte", "ok",
    # Cumprimentos: um "oi" sozinho e fala de verdade (2026-10-25: ficava HELD como ruido e expirava sem resposta). "E ai" fica de fora de proposito: o ASR alucina isso do ruido (tests/test_asr_turn_gate.py).
    "oi", "ola", "alo",
}


# Janela de continuação MEDIDA (tentativa de 2026-10-21, 6 fragmentos inacabados): as continuações reais chegaram 1,1; 1,2; 1,7; 2,0; 2,4 s depois (a sexta,
# 19 s, não teve continuação). A janela antiga (1,4 s) pegaria 2 de 5; 3,0 s pega as 5 (p90 = 2,4 s) com folga e ainda separa o caso de 19 s.
CONTINUATION_WINDOW_MS = 3000
MAX_CHAIN_FRAGMENTS = 3
MAX_CHAIN_MS = 9000
MAX_UNFINISHED_MS = 8000
EMITTED_MEMORY = 64

# Sem "opa"/"oxe": são cumprimento/expressão com sentido e já tiveram resposta legítima.
# O ASR varia a grafia da mesma interjeição ("Hum", "Hmmm", "Ahhh", "uh", "Hã", "Ahn"): padrão, não lista. Dobrado (sem acento) e minúsculo.
_FILLER_WORD = re.compile(r"^(?:h+u*m+|m{2,}|a+h+n*|u+h+m*|e+h+|h+a+|u+e+|ne|ai|uai)$")
_UNFINISHED_TAIL = frozenset({
    "a", "o", "as", "os", "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas", "com", "para", "pra", "que", "e", "ou", "mas", "se",
    "um", "uma", "meu", "minha", "dentro", "vou", "tipo", "fazer", "ta",
})


def looks_unfinished(text: str) -> bool:
    """Frase que termina em reticências ou numa palavra que pede continuação ("dentro", "de", "que")."""
    raw = str(text or "").strip()
    if raw.endswith(("...", "…")):
        return True
    words = _fold(raw).split()
    return bool(words) and words[-1] in _UNFINISHED_TAIL and not raw.endswith(("?", "!"))


def is_backchannel_only(text: str) -> bool:
    """Só interjeição ("Hum.", "Ahhh.", "uh"): não tem conteúdo para virar turno."""
    words = _fold(text).split()
    return bool(words) and all(_FILLER_WORD.match(w) for w in words)


# Limiares MEDIDOS em 14 fragmentos curtos da tentativa de 2026-10-21 (amostra pequena: ajustar com mais gravações). "Tá preparado?" (voz 341 ms, rms 0,062,
# pico 0,72) ficou 9 ms abaixo do corte de 350 ms e foi descartado, e era a pergunta do Criador; "Se esconder não." (rms 0,017, pico 0,13) é fraco.
SUBSTANTIVE_MIN_RMS = 0.02
SUBSTANTIVE_MIN_PEAK = 0.2


def is_substantive_short(fragment: "AsrTurnFragment") -> bool:
    """Curto com cara de fala de verdade: >= 2 palavras que não são interjeição, não inacabado, sinal acústico firme. O resto é ruído e expira."""
    words = [w for w in _fold(fragment.text).split() if not _FILLER_WORD.match(w)]
    return (
        len(words) >= 2
        and not looks_unfinished(fragment.text)
        and float(fragment.rms) >= SUBSTANTIVE_MIN_RMS
        and float(fragment.peak) >= SUBSTANTIVE_MIN_PEAK
    )


def is_urgent_stop_text(text: str) -> bool:
    """A fala inteira é parada/retenção: passa na hora e cancela qualquer fragmento pendente (mesmas frases deterministas do controle da série)."""
    from .series_intent import detect_series_intent  # import tardio: series_intent não pode ser dependência de carga deste módulo

    if any(item.intent in {"control.emergency_stop", "companion.hold"} for item in detect_series_intent(text)):
        return True
    folded = re.sub(r"^(?:ei |oi )?celia ?", "", _fold(text)).strip()
    return folded in {"para", "pare", "para ai", "pare ai"}


def is_explicit_short_command(text: str) -> bool:
    """Return True only for deliberately small, unambiguous owner commands."""
    folded = _fold(text)
    if folded in _EXPLICIT_SHORT_COMMANDS:
        return True
    return bool(
        re.fullmatch(
            r"(?:ei |oi )?celia(?: (?:para|pare|joga|jogue|continua|continue|repete|repita|volta|volte))?",
            folded,
        )
    )


_MEASURED_SUSPICIOUS_ASR_RE = re.compile(
    r"\b(?:hispero|ispero|agnosticando|agnoficando|descuqueou|descuqueir|sinan)\b"
    r"|\blutar\s+vendo\s+o\s+jogo\b",
    re.IGNORECASE,
)
_FOREIGN_CLUSTER_RE = re.compile(
    r"(?:[bcdfghklmnpqrstvwxyz]j|j[bcdfghklmnpqrstvwxyz])",
    re.IGNORECASE,
)


def has_measured_suspicious_asr(text: str) -> bool:
    return bool(_MEASURED_SUSPICIOUS_ASR_RE.search(_fold(text)))


def has_language_shape_mismatch(text: str) -> bool:
    """Flag strong non-Portuguese orthography only as a verification trigger.

    This does not guess a language and never rewrites the sentence. It exists
    for recordings such as "Sarp kjúgdurta hjá unha." where two independent
    consonant+j clusters make a second ASR opinion materially safer.
    """
    words = _fold(text).split()
    if len(words) < 3:
        return False
    suspicious_words = sum(bool(_FOREIGN_CLUSTER_RE.search(word)) for word in words)
    return suspicious_words >= 2


def secondary_verification_reason(text: str, *, voiced_ms: int | None) -> str:
    """Request the heavy local judge only for reproduced transcription risk.

    Short speech is already handled by the turn assembler. Waking a second
    Whisper Large v3 Turbo merely because voiced_ms is small wastes several GB
    of CUDA memory during recording and duplicates work already done by the
    primary ASR.
    """
    folded = _fold(text)
    if _MEASURED_SUSPICIOUS_ASR_RE.search(folded):
        return "measured_pattern"
    if has_language_shape_mismatch(folded):
        return "language_shape_mismatch"
    return ""


def should_verify_secondary(text: str, *, voiced_ms: int | None) -> bool:
    """Request a second ASR only where recordings show material risk."""
    return bool(secondary_verification_reason(text, voiced_ms=voiced_ms))


def secondary_min_similarity(reason: str, configured: float = 0.58) -> float:
    """Use a stricter agreement floor only for reproduced high-risk cases."""
    base = max(0.0, min(1.0, float(configured)))
    if reason == "measured_pattern":
        return max(base, 0.82)
    if reason == "language_shape_mismatch":
        return max(base, 0.72)
    return base


def transcript_similarity(left: str, right: str) -> float:
    """Normalized transcript similarity used only for disagreement gating."""
    a, b = _fold(left), _fold(right)
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


@dataclass(frozen=True)
class AsrTurnFragment:
    utterance_id: str
    raw_text: str
    text: str
    duration_ms: int
    voiced_ms: int | None
    rms: float
    peak: float
    captured_at: float
    rules: tuple[str, ...] = ()

    def merged_with(self, other: "AsrTurnFragment") -> "AsrTurnFragment":
        left_voiced = max(0, int(self.voiced_ms or 0))
        right_voiced = max(0, int(other.voiced_ms or 0))
        total_duration = max(1, self.duration_ms + other.duration_ms)
        weighted_rms = (
            self.rms * max(1, self.duration_ms)
            + other.rms * max(1, other.duration_ms)
        ) / total_duration
        return AsrTurnFragment(
            utterance_id=other.utterance_id,
            raw_text=" ".join(part for part in (self.raw_text.strip(), other.raw_text.strip()) if part),
            text=" ".join(part for part in (self.text.strip(), other.text.strip()) if part),
            duration_ms=self.duration_ms + other.duration_ms,
            voiced_ms=(left_voiced + right_voiced)
            if (self.voiced_ms is not None or other.voiced_ms is not None)
            else None,
            rms=float(weighted_rms),
            peak=max(float(self.peak), float(other.peak)),
            captured_at=max(float(self.captured_at), float(other.captured_at)),
            rules=tuple(dict.fromkeys((*self.rules, *other.rules))),
        )


@dataclass(frozen=True)
class AsrTurnGateResult:
    action: str  # SUBMIT | HOLD | REJECT
    reason: str
    fragment: AsrTurnFragment | None = None
    merged_from: tuple[str, ...] = ()
    dropped_fragment: AsrTurnFragment | None = None
    released_fragment: AsrTurnFragment | None = None   # pendente inacabado vencido: tem de virar turno por conta própria
    cancelled_by_urgent: bool = False


def classify_fragment(
    fragment: AsrTurnFragment,
    *,
    reject_below_ms: int = 180,
    hold_below_ms: int = 350,
) -> AsrTurnGateResult:
    """Classify one transcript after ASR/normalization, before routing."""
    text = str(fragment.text or "").strip()
    if not text:
        return AsrTurnGateResult("REJECT", "empty_transcript", fragment)

    voiced = fragment.voiced_ms
    if voiced is None:
        return AsrTurnGateResult("SUBMIT", "no_voiced_metadata", fragment)

    voiced = max(0, int(voiced))
    explicit = is_explicit_short_command(text)

    if voiced < reject_below_ms:
        if explicit and voiced >= 120 and fragment.rms >= 0.012 and fragment.peak >= 0.08:
            return AsrTurnGateResult("SUBMIT", "explicit_short_command", fragment)
        return AsrTurnGateResult("REJECT", "voiced_below_turn_floor", fragment)

    if is_backchannel_only(text) and not explicit:
        return AsrTurnGateResult("REJECT", "backchannel_only", fragment)  # "Hum.", "Ahhh.", "uh": sem conteúdo (gerava "hum o quê?")

    if voiced < hold_below_ms:
        if explicit:
            return AsrTurnGateResult("SUBMIT", "explicit_short_command", fragment)
        return AsrTurnGateResult("HOLD", "short_fragment_waiting_for_neighbor", fragment)

    if looks_unfinished(text) and not explicit and fragment.duration_ms <= MAX_UNFINISHED_MS:
        return AsrTurnGateResult("HOLD", "unfinished_phrase_waiting_for_continuation", fragment)

    return AsrTurnGateResult("SUBMIT", "turn_evidence_ok", fragment)


class AsrTurnAssembler:
    """Junta fragmentos vizinhos e emite NO MÁXIMO um turno por fala.

    Dois tipos de pendente, com destinos diferentes quando o tempo vence sem vizinho:
      * ``short``        (voz < 350 ms: provável ruído)      -> descartado, como sempre;
      * ``continuation`` (frase inacabada com voz de verdade) -> LIBERADO como turno por conta própria (``release_pending``), sem esperar outra fala.
    Parada urgente passa na hora, nunca se funde e cancela o pendente. Um id de fala nunca é emitido duas vezes.
    """

    def __init__(
        self,
        *,
        merge_window_ms: int = 1400,
        reject_below_ms: int = 180,
        hold_below_ms: int = 350,
        continuation_window_ms: int = CONTINUATION_WINDOW_MS,
    ) -> None:
        self.merge_window_ms = max(100, int(merge_window_ms))
        self.continuation_window_ms = max(100, int(continuation_window_ms))
        self.reject_below_ms = max(0, int(reject_below_ms))
        self.hold_below_ms = max(self.reject_below_ms, int(hold_below_ms))
        self._pending: AsrTurnFragment | None = None
        self._pending_kind = "short"
        self._chain = 0
        self._chain_started_at = 0.0
        self._emitted: deque[str] = deque(maxlen=EMITTED_MEMORY)

    @property
    def pending(self) -> AsrTurnFragment | None:
        return self._pending

    @property
    def pending_kind(self) -> str:
        return self._pending_kind

    def clear(self) -> None:
        self._pending = None

    def _window_ms(self) -> int:
        return self.continuation_window_ms if self._pending_kind == "continuation" else self.merge_window_ms

    def pending_deadline(self) -> float | None:
        """Instante (mesmo relógio de ``captured_at``) em que o pendente vence."""
        if self._pending is None:
            return None
        return float(self._pending.captured_at) + self._window_ms() / 1000.0

    def _is_neighbor(self, newer: AsrTurnFragment) -> bool:
        if self._pending is None:
            return False
        delta_ms = (float(newer.captured_at) - float(self._pending.captured_at)) * 1000.0
        return 0 <= delta_ms <= self._window_ms()

    def _emit(self, result: AsrTurnGateResult) -> AsrTurnGateResult:
        """Uma fala nunca vira turno duas vezes (reentrega, corrida entre o temporizador e a fala seguinte)."""
        if result.action != "SUBMIT" or result.fragment is None:
            return result
        ids = tuple(dict.fromkeys((*result.merged_from, result.fragment.utterance_id)))
        if any(i and i in self._emitted for i in ids):
            return AsrTurnGateResult("REJECT", "duplicate_utterance", result.fragment, merged_from=result.merged_from)
        self._emitted.extend(i for i in ids if i)
        return result

    def release_pending(self, utterance_id: str | None = None) -> AsrTurnGateResult | None:
        """O tempo venceu. Devolve o que fazer com o pendente (e o remove, de forma atômica), ou None se ele já foi resolvido por outra via."""
        pending = self._pending
        if pending is None or (utterance_id and pending.utterance_id != utterance_id):
            return None
        kind = self._pending_kind
        self._pending = None
        if kind == "continuation":
            return self._emit(AsrTurnGateResult("SUBMIT", "released_after_continuation_window", pending))
        if is_substantive_short(pending):
            return self._emit(AsrTurnGateResult("SUBMIT", "released_short_fragment", pending))
        return AsrTurnGateResult("REJECT", "short_fragment_expired", pending)

    def _hold(self, fragment: AsrTurnFragment, kind: str) -> None:
        self._pending, self._pending_kind = fragment, kind

    def push(self, fragment: AsrTurnFragment) -> AsrTurnGateResult:
        current = classify_fragment(
            fragment,
            reject_below_ms=self.reject_below_ms,
            hold_below_ms=self.hold_below_ms,
        )
        if current.action == "REJECT":
            return current

        # Parada urgente: passa AGORA, nunca se funde, e nada que estivesse esperando pode executar depois dela.
        if is_urgent_stop_text(fragment.text):
            cancelled, self._pending = self._pending, None
            return self._emit(AsrTurnGateResult(
                "SUBMIT", "urgent_stop", fragment, dropped_fragment=cancelled, cancelled_by_urgent=cancelled is not None,
            ))

        dropped: AsrTurnFragment | None = None
        released: AsrTurnFragment | None = None
        if self._pending is not None and not self._is_neighbor(fragment):
            stale, kind = self._pending, self._pending_kind
            self._pending = None
            if kind == "continuation" or is_substantive_short(stale):
                released = stale          # inacabado com voz de verdade, ou curto com cara de fala: não se perde, vira turno
            else:
                dropped = stale           # ruído curto: descartado, como sempre

        if current.action == "HOLD":
            kind = "continuation" if current.reason.startswith("unfinished") else "short"
            if self._pending is None:
                self._hold(fragment, kind)
                self._chain, self._chain_started_at = 1, float(fragment.captured_at)
                return replace(current, dropped_fragment=dropped, released_fragment=released)

            older = self._pending
            merged = older.merged_with(fragment)
            self._chain += 1
            merged_decision = classify_fragment(
                merged,
                reject_below_ms=self.reject_below_ms,
                hold_below_ms=self.hold_below_ms,
            )
            span_ms = (float(fragment.captured_at) - self._chain_started_at) * 1000.0
            too_long = self._chain >= MAX_CHAIN_FRAGMENTS or span_ms > MAX_CHAIN_MS
            if merged_decision.action == "SUBMIT" or (
                merged_decision.action == "HOLD" and merged_decision.reason.startswith("unfinished") and too_long
            ):
                self._pending = None
                return self._emit(AsrTurnGateResult(
                    "SUBMIT", "merged_short_fragments" if merged_decision.action == "SUBMIT" else "merged_chain_limit", merged,
                    merged_from=(older.utterance_id, fragment.utterance_id),
                    dropped_fragment=dropped, released_fragment=released,
                ))
            self._hold(merged, "continuation" if merged_decision.reason.startswith("unfinished") else "short")
            return AsrTurnGateResult(
                "HOLD", "merged_fragment_still_short", merged,
                merged_from=(older.utterance_id, fragment.utterance_id),
                dropped_fragment=dropped, released_fragment=released,
            )

        if self._pending is not None and self._is_neighbor(fragment):
            older = self._pending
            self._pending = None
            merged = older.merged_with(fragment)
            return self._emit(AsrTurnGateResult(
                "SUBMIT", "merged_pending_fragment", merged,
                merged_from=(older.utterance_id, fragment.utterance_id),
                dropped_fragment=dropped, released_fragment=released,
            ))

        return self._emit(replace(current, dropped_fragment=dropped, released_fragment=released))
