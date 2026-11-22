"""Temporizador do montador de fragmentos de ASR: o pendente vence POR TEMPO, não porque outra fala chegou.

Sem isto, o montador só decidia quando o próximo fragmento chegava: um fragmento inacabado sozinho ficava parado até a próxima fala e, se ela demorasse mais que
a janela, era DESCARTADO em silêncio (medido: "Não tem um..." esperou 19 s por "Cabe."). Aqui o prazo é armado quando o pendente nasce e cancelado quando ele
é resolvido por outra via (vizinho, parada urgente, vencimento). A resolução é do montador (``release_pending``, atômica): se o pendente já foi resolvido, o
disparo atrasado não faz nada. Relógio e ``call_later`` são injetáveis para o replay com os intervalos reais.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from .asr_turn_gate import AsrTurnAssembler, AsrTurnGateResult


class AsrFragmentScheduler:
    def __init__(
        self,
        assembler: AsrTurnAssembler,
        *,
        clock: Callable[[], float] = time.monotonic,
        call_later: Callable[..., Any] | None = None,
        on_release: Callable[[AsrTurnGateResult], Any] | None = None,
        on_expire: Callable[[AsrTurnGateResult], Any] | None = None,
    ) -> None:
        self.assembler, self.clock, self._call_later = assembler, clock, call_later
        self.on_release, self.on_expire = on_release, on_expire
        self._handle: Any = None
        self._armed: tuple[str, float] | None = None

    def _later(self, delay: float, callback: Callable[..., Any], *args: Any) -> Any:
        call_later = self._call_later or asyncio.get_running_loop().call_later
        return call_later(delay, callback, *args)

    def cancel(self) -> None:
        if self._handle is not None:
            self._handle.cancel()
        self._handle, self._armed = None, None

    def sync(self) -> None:
        """Chamar DEPOIS de cada ``assembler.push``: arma o prazo do pendente (ou cancela, se não há mais pendente)."""
        pending = self.assembler.pending
        if pending is None:
            self.cancel()
            return
        key = (pending.utterance_id, float(pending.captured_at))
        if self._armed == key:
            return
        self.cancel()
        deadline = self.assembler.pending_deadline()
        delay = max(0.0, float(deadline) - float(self.clock())) if deadline is not None else 0.0
        self._handle, self._armed = self._later(delay, self._fire, pending.utterance_id), key

    def _fire(self, utterance_id: str) -> None:
        self._handle, self._armed = None, None
        result = self.assembler.release_pending(utterance_id)
        if result is None:
            return  # o pendente já foi resolvido por outra via: nada a fazer (exatamente uma vez)
        callback = self.on_release if result.action == "SUBMIT" else self.on_expire
        if callback is not None:
            callback(result)
