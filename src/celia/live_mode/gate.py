"""Portões de isolamento usados pelas entradas existentes (microfone, texto do dono, submit_input, /bridge/process).

Quando o modo Live está ativo (test ou live), a conversa só entra pelo chat da live.
O ASR continua rodando; apenas o que ele transcreve deixa de ser encaminhado. Fora do modo Live
todas estas funções devolvem "permitido" e nada muda no comportamento normal.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

from .manager import LiveModeManager

_MANAGER: LiveModeManager | None = None

# origens que o modo Live aceita em submit_input (o chat da live usa /bridge/process, mas deixamos explícito)
LIVE_ORIGINS = {"tiktok_live_chat", "live_test"}

# Marca o turno da iniciativa da própria Célia (loop cognitivo). Só ele passa com origin="system";
# outros "system" (corpo no Minecraft, roteiro) continuam barrados no modo Live.
_PROACTIVE_TURN: contextvars.ContextVar[bool] = contextvars.ContextVar("live_proactive_turn", default=False)


def get_manager() -> LiveModeManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = LiveModeManager()
    return _MANAGER


def owner_input_blocked() -> bool:
    """True = microfone/texto digitado do dono NÃO podem entrar (e não podem aparecer em legenda/transmissão)."""
    return _MANAGER is not None and _MANAGER.blocks_owner_input()


def note_mic_blocked() -> None:
    if _MANAGER is not None:
        _MANAGER.note_mic_blocked()


def live_active() -> bool:
    """True = modo Live (test ou live) ligado: a cena da bancada é o único corpo e a única voz."""
    return _MANAGER is not None and _MANAGER.active


def proactive_allowed() -> bool:
    """True = modo Live ativo e `proactive_speech` ligado: a Célia pode puxar assunto sozinha pro chat."""
    return (_MANAGER is not None and _MANAGER.active
            and bool(_MANAGER.settings.get().get("proactive_speech"))
            and not _MANAGER.settings.get().get("idle_host_enabled", False))


@contextmanager
def proactive_turn():
    token = _PROACTIVE_TURN.set(True)
    try:
        yield
    finally:
        _PROACTIVE_TURN.reset(token)


def submit_input_allowed(origin: str | None) -> bool:
    if _MANAGER is None or not _MANAGER.active:
        return True
    origin = (origin or "").strip().lower()
    if origin == "system" and _PROACTIVE_TURN.get() and proactive_allowed():
        return True
    return origin in LIVE_ORIGINS


def process_source_allowed(fonte: str | None) -> bool:
    return True if _MANAGER is None else _MANAGER.source_allowed(fonte)
