"""Bounded per-turn latency samples and percentile snapshots."""

from __future__ import annotations

import threading
import uuid
from collections import OrderedDict, deque
from math import isfinite
from typing import Deque


# These names are intentionally kept in the module so instrumentation and
# diagnostics share one vocabulary for the three previously missing marks.
texto_utilizavel = "texto_utilizavel"
first_audio_generated = "first_audio_generated"
first_audio_sent = "first_audio_sent"
first_audio_playback_started = "first_audio_playback_started"
tts_synthesis_ms = "tts_synthesis_ms"
tts_to_sent_ms = "tts_to_sent_ms"
tts_first_audio = "tts_first_audio"


class Cronometro:
    """Store a bounded sample window while retaining the latest turn stages."""

    def __init__(self, janela: int = 256) -> None:
        self.janela = max(1, int(janela))
        self._lock = threading.RLock()
        self._id_prefix = uuid.uuid4().hex
        self._next_turn = 0
        self._samples: OrderedDict[str, Deque[tuple[str, float]]] = OrderedDict()
        self._turns: OrderedDict[str, dict[str, float]] = OrderedDict()

    def novo_turno(self) -> str:
        """Create an opaque, unique turn identifier."""
        with self._lock:
            self._next_turn += 1
            turno_id = f"{self._id_prefix}{self._next_turn:016x}"
            self._turns[turno_id] = {}
            self._trim_turns()
        return turno_id

    def marcar(self, turno_id, estagio, ms) -> None:
        """Record one sample; invalid or unknown inputs are ignored safely."""
        turno_id = str(turno_id or "")
        estagio = str(estagio or "")
        if not turno_id or not estagio:
            return
        try:
            value = float(ms)
        except (TypeError, ValueError):
            return
        if value < 0 or not isfinite(value):
            return

        with self._lock:
            turn = self._turns.get(turno_id)
            if turn is None:
                turn = {}
                self._turns[turno_id] = turn
                self._trim_turns()
            elif estagio not in turn and len(turn) >= self.janela:
                turn.pop(next(iter(turn)))
            turn[estagio] = value
            self._turns.move_to_end(turno_id)
            samples = self._samples.get(estagio)
            if samples is None:
                samples = deque(maxlen=self.janela)
                self._samples[estagio] = samples
            samples.append((turno_id, value))
            self._samples.move_to_end(estagio)
            self._trim_stages()

    def estagios_do_turno(self, turno_id) -> dict:
        with self._lock:
            return dict(self._turns.get(str(turno_id or ""), {}))

    def snapshot(self) -> dict:
        with self._lock:
            return {
                estagio: self._resumo(list(amostras))
                for estagio, amostras in self._samples.items()
                if amostras
            }

    def _trim_turns(self) -> None:
        while len(self._turns) > self.janela:
            self._turns.popitem(last=False)

    def _trim_stages(self) -> None:
        while len(self._samples) > self.janela:
            self._samples.popitem(last=False)

    @staticmethod
    def _resumo(samples: list[tuple[str, float]]) -> dict:
        ordered_samples = sorted(samples, key=lambda item: item[1])
        ordered = [value for _, value in ordered_samples]
        count = len(ordered)
        p95_value = Cronometro._percentil(ordered, 0.95)
        p95_turn_id, p95_sample = min(
            ordered_samples, key=lambda item: abs(item[1] - p95_value)
        )
        max_turn_id, max_value = ordered_samples[-1]
        return {
            "count": count,
            "min": ordered[0],
            "media": sum(ordered) / count,
            "p50": Cronometro._percentil(ordered, 0.50),
            "p90": Cronometro._percentil(ordered, 0.90),
            "p95": p95_value,
            "max": max_value,
            "max_turn_id": max_turn_id,
            "p95_turn_id": p95_turn_id,
            "p95_sample": p95_sample,
        }

    @staticmethod
    def _percentil(ordered: list[float], quantile: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = (len(ordered) - 1) * quantile
        lower = int(position)
        upper = min(lower + 1, len(ordered) - 1)
        fraction = position - lower
        return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction
