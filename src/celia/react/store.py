from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path
from typing import Any


class ReactStore:
    """Small atomic JSON store; media and analysis stay outside the repository."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self._lock = threading.RLock()
        self.sessions = self.root / "sessions"
        self.media = self.root / "media"
        self.analysis = self.root / "analysis"
        for path in (self.sessions, self.media, self.analysis):
            path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _check_id(session_id: str) -> str:
        # Sem isto, "..\\" codificado na URL atravessaria a pasta no Windows (Opus, 14/11).
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,63}", session_id or ""):
            raise ValueError("session_id inválido")
        return session_id

    def _path(self, session_id: str) -> Path:
        self._check_id(session_id)
        return self.sessions / f"{session_id}.json"

    def _state_path(self, session_id: str) -> Path:
        self._check_id(session_id)
        return self.sessions / f"{session_id}.state.json"

    def get_state(self, session_id: str) -> dict[str, Any]:
        path = self._state_path(session_id)
        if not path.is_file():
            return {"last_position_ms": None, "paused": True, "rate": 1.0,
                    "previous_playback": [], "released_event_ids": [], "react_session_memory": []}
        return json.loads(path.read_text(encoding="utf-8"))

    def save_state(self, session_id: str, state: dict[str, Any]) -> None:
        path = self._state_path(session_id)
        fd, name = tempfile.mkstemp(prefix=".react-state-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def save(self, value: dict[str, Any]) -> None:
        with self._lock:
            self._save(value)

    def _save(self, value: dict[str, Any]) -> None:
        path = self._path(value["playback"]["session_id"])
        fd, name = tempfile.mkstemp(prefix=".react-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(name, path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def update(self, session_id: str, change) -> dict[str, Any] | None:
        """Lê a sessão FRESCA do disco, aplica `change(session)` e grava, sob trava.

        Corrige a perda de atualização medida em 14/11 (Opus): o WS guardava a sessão lida na
        conexão e regravava essa cópia velha a cada evento de playback, apagando o READY que o
        worker tinha gravado. Todo escritor deve alterar só o seu campo por aqui.
        """
        with self._lock:
            session = self.get(session_id)
            if session is None:
                return None
            change(session)
            self.save(session)
            return session

    def get(self, session_id: str) -> dict[str, Any] | None:
        try:
            path = self._path(session_id)
        except ValueError:
            return None
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def all(self) -> list[dict[str, Any]]:
        results = []
        for path in self.sessions.glob("*.json"):
            # Os *.state.json da P5 moram na mesma pasta e não são sessões (Opus, 14/11).
            if path.name.endswith(".state.json"):
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(data, dict) and "segments" in data and "playback" in data:
                results.append(data)
        return results
