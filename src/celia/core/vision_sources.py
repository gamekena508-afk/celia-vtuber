"""Estado explícito das fontes de visão: tela e câmera, cada uma com o seu liga/desliga.

Regra do dono (2026-10-05): a visão só tira prints e analisa enquanto ELE mantém
ligado o botão da fonte na interface (a permissão do navegador já vem desse clique).
Tela e câmera são fontes independentes: ligar uma NÃO liga a outra, e desligar uma
limpa só o que veio dela.

O navegador manda um sinal explícito (`POST /bridge/vision/state`) ao ligar e ao
desligar. Como rede e abas em segundo plano falham, há um timeout de segurança: uma
fonte ligada que não dá sinal de vida (sinal ou frame) por `CELIA_VISION_SOURCE_TIMEOUT`
segundos é considerada desligada. Nada aqui captura imagem nem toca em GPU.
"""
from __future__ import annotations

import os
import threading
import time
from typing import Any, Callable

SOURCES: tuple[str, ...] = ("screen", "camera")

_ALIASES = {
    "screen": "screen",
    "tela": "screen",
    "display": "screen",
    "camera": "camera",
    "câmera": "camera",
    "cam": "camera",
    "webcam": "camera",
}


def normalize_source(value: Any) -> str | None:
    """Nome canônico da fonte, ou None se for desconhecida."""
    return _ALIASES.get(str(value or "").strip().lower())


def _timeout_seconds() -> float:
    try:
        return max(3.0, float(os.getenv("CELIA_VISION_SOURCE_TIMEOUT", "30")))
    except ValueError:
        return 30.0


Listener = Callable[[str, bool, str], None]


class VisionSourceRegistry:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.RLock()
        self._listeners: list[Listener] = []
        self._state: dict[str, dict[str, Any]] = {
            source: self._blank() for source in SOURCES
        }

    @staticmethod
    def _blank() -> dict[str, Any]:
        return {
            "active": False,
            "since": 0.0,
            "last_signal": 0.0,
            "width": None,
            "height": None,
            "frames": 0,
            "last_off_reason": "",
        }

    # ── listeners ──────────────────────────────────────────────────────────
    def subscribe(self, listener: Listener) -> None:
        """`listener(source, active, reason)` roda FORA do lock, a cada mudança real."""
        with self._lock:
            if listener not in self._listeners:
                self._listeners.append(listener)

    def _notify(self, changes: list[tuple[str, bool, str, dict[str, Any]]]) -> None:
        for source, active, reason, info in changes:
            self._emit(source, active, reason, info)
            with self._lock:
                listeners = list(self._listeners)
            for listener in listeners:
                try:
                    listener(source, active, reason)
                except Exception:  # um ouvinte quebrado nunca derruba a visão
                    pass

    @staticmethod
    def _emit(source: str, active: bool, reason: str, info: dict[str, Any]) -> None:
        try:
            from .observability import observability

            observability.emit_ambient(
                "vision.source",
                "on" if active else "off",
                {
                    "source": source,
                    "reason": reason,
                    "width": info.get("width"),
                    "height": info.get("height"),
                },
            )
        except Exception:
            pass

    # ── estado ─────────────────────────────────────────────────────────────
    def set_active(
        self,
        source: str,
        active: bool,
        *,
        width: int | None = None,
        height: int | None = None,
        reason: str = "signal",
    ) -> dict[str, Any]:
        """Aplica o sinal explícito do navegador. Devolve {changed, active, source}."""
        canonical = normalize_source(source)
        if canonical is None:
            raise ValueError(f"fonte de visão desconhecida: {source!r}")
        now = self._clock()
        changes: list[tuple[str, bool, str, dict[str, Any]]] = []
        with self._lock:
            state = self._state[canonical]
            was_active = bool(state["active"])
            state["last_signal"] = now
            if active:
                state["active"] = True
                if width:
                    state["width"] = int(width)
                if height:
                    state["height"] = int(height)
                if not was_active:
                    state["since"] = now
                    state["frames"] = 0
                    state["last_off_reason"] = ""
                    changes.append((canonical, True, reason, dict(state)))
            else:
                state["active"] = False
                if was_active:
                    state["last_off_reason"] = reason
                    changes.append((canonical, False, reason, dict(state)))
            result = {
                "source": canonical,
                "active": bool(state["active"]),
                "changed": bool(changes),
            }
        self._notify(changes)
        return result

    def touch(self, source: str) -> None:
        """Um frame aceito conta como sinal de vida."""
        canonical = normalize_source(source)
        if canonical is None:
            return
        with self._lock:
            state = self._state[canonical]
            if state["active"]:
                state["last_signal"] = self._clock()
                state["frames"] += 1

    def _expire_locked(self, source: str, now: float) -> tuple[str, bool, str, dict[str, Any]] | None:
        state = self._state[source]
        if state["active"] and now - state["last_signal"] > _timeout_seconds():
            state["active"] = False
            state["last_off_reason"] = "timeout"
            return (source, False, "timeout", dict(state))
        return None

    def expire_stale(self) -> list[str]:
        now = self._clock()
        changes = []
        with self._lock:
            for source in SOURCES:
                change = self._expire_locked(source, now)
                if change:
                    changes.append(change)
        self._notify(changes)
        return [c[0] for c in changes]

    def is_active(self, source: str) -> bool:
        canonical = normalize_source(source)
        if canonical is None:
            return False
        now = self._clock()
        with self._lock:
            change = self._expire_locked(canonical, now)
            active = bool(self._state[canonical]["active"])
        if change:
            self._notify([change])
        return active

    def active_sources(self) -> list[str]:
        return [s for s in SOURCES if self.is_active(s)]

    def reset(self) -> None:
        """Só para testes: volta tudo para desligado sem notificar."""
        with self._lock:
            self._state = {source: self._blank() for source in SOURCES}

    def snapshot(self) -> dict[str, Any]:
        self.expire_stale()
        now = self._clock()
        with self._lock:
            out: dict[str, Any] = {"timeout_s": _timeout_seconds(), "sources": {}}
            for source, state in self._state.items():
                out["sources"][source] = {
                    "active": bool(state["active"]),
                    "width": state["width"],
                    "height": state["height"],
                    "frames": state["frames"],
                    "active_for_s": (
                        round(now - state["since"], 1) if state["active"] else None
                    ),
                    "last_off_reason": state["last_off_reason"] or None,
                }
            return out


vision_sources = VisionSourceRegistry()
