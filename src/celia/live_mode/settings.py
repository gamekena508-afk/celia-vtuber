"""Configurações do modo Live, salvas em var/live_mode/settings.json (fora do git).

Credenciais NÃO moram aqui: a chave da Euler fica em secrets/euler_api_key
(ou na variável EULER_API_KEY) e só o processo leitor a lê.
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
from copy import deepcopy
from pathlib import Path
from typing import Any

from celia.core.raizes import VAR

LIVE_DIR = Path(os.environ.get("CELIA_LIVE_DIR", "") or (VAR / "live_mode"))

DEFAULTS: dict[str, Any] = {
    "ranking_characters_enabled": True,   # Top1->slime, Top2->ursinho (desligar devolve tudo à Célia)
    "ranking_window_minutes": 0,          # 0 = sessão inteira; >0 = janela deslizante
    "proactive_speech": True,             # nos silêncios a Célia puxa assunto/curiosidade pro chat; False = só responde o chat
    "idle_host_enabled": True,            # um único relógio, dentro da fila serial dos três
    "idle_after_s": 2.0,                  # antecipa síntese para a meta de 8 s sem voz
    "silence_limit_s": 8.0,
    "gift_reactions_enabled": True,
    "allow_sources": ["tiktok_live_chat", "live_test"],
    "volumes": {"celia": 1.0, "slime": 0.9, "ursinho": 0.9},
    "gap_between_speeches_s": 0.6,
    "max_chars_read": 300,
    "per_user_interval_s": 20.0,          # mínimo entre duas mensagens aceitas do mesmo usuário
    "per_user_max_per_minute": 3,
    "queue_max": 12,
    "playback_timeout_s": 5.0,
    "reply_max_chars": 220,
    "characters": {
        "slime": {"enabled": True, "scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
        "ursinho": {"enabled": True, "scale": 1.0, "offset_x": 0.0, "offset_y": 0.0},
    },
}

_RANGES = {
    "idle_after_s": (1.0, 30.0), "silence_limit_s": (4.0, 30.0),
    "ranking_window_minutes": (0, 1440), "gap_between_speeches_s": (0.0, 10.0),
    "max_chars_read": (20, 1000), "per_user_interval_s": (0.0, 600.0),
    "per_user_max_per_minute": (1, 60), "queue_max": (1, 100),
    "playback_timeout_s": (5.0, 300.0), "reply_max_chars": (40, 600),
}


def _merge(base: dict, patch: dict) -> dict:
    out = deepcopy(base)
    for key, value in (patch or {}).items():
        if key not in base:
            continue  # chave desconhecida nunca entra
        if isinstance(base[key], dict) and isinstance(value, dict):
            out[key] = _merge(base[key], value)
        else:
            out[key] = value
    return out


def validate(data: dict[str, Any]) -> dict[str, Any]:
    """Coage tipos e limites; devolve configuração sempre utilizável."""
    out = _merge(DEFAULTS, data)
    out["ranking_characters_enabled"] = bool(out["ranking_characters_enabled"])
    out["proactive_speech"] = bool(out["proactive_speech"])
    out["idle_host_enabled"] = bool(out["idle_host_enabled"])
    out["gift_reactions_enabled"] = bool(out["gift_reactions_enabled"])
    for key, (lo, hi) in _RANGES.items():
        try:
            val = type(DEFAULTS[key])(out[key])
        except (TypeError, ValueError):
            val = DEFAULTS[key]
        out[key] = min(hi, max(lo, val))
    srcs = out.get("allow_sources")
    out["allow_sources"] = [str(s).strip().lower() for s in srcs if str(s).strip()] if isinstance(srcs, list) and srcs else list(DEFAULTS["allow_sources"])
    for name in out["volumes"]:
        try:
            out["volumes"][name] = min(1.0, max(0.0, float(out["volumes"][name])))
        except (TypeError, ValueError):
            out["volumes"][name] = DEFAULTS["volumes"][name]
    for name, cfg in out["characters"].items():
        base = DEFAULTS["characters"][name]
        cfg["enabled"] = bool(cfg.get("enabled", True))
        for field, (lo, hi) in (("scale", (0.1, 4.0)), ("offset_x", (-2.0, 2.0)), ("offset_y", (-2.0, 2.0))):
            try:
                cfg[field] = min(hi, max(lo, float(cfg[field])))
            except (TypeError, ValueError):
                cfg[field] = base[field]
    return out


class SettingsStore:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path else LIVE_DIR / "settings.json"
        self._lock = threading.Lock()
        self._data = validate({})
        self.load_error: str | None = None
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self._data = validate(raw if isinstance(raw, dict) else {})
            self.load_error = None
        except FileNotFoundError:
            self._data = validate({})
        except (OSError, ValueError) as exc:  # arquivo corrompido: usa padrão e avisa, não derruba
            self._data = validate({})
            self.load_error = type(exc).__name__

    def get(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._data = validate(_merge(self._data, patch))
            self._write()
            return deepcopy(self._data)

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
