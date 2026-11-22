"""Lifecycle manager for the independent Celia VTS tracker."""
from __future__ import annotations

import atexit
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from loguru import logger

from .track_emotion_client import send_command


class TrackProcessManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        # 2026-08-11 (claude): era `project_root.parents[1] / "track"`, conta que
        # dependia do pacote morar 2 niveis abaixo da arvore. Com o achatamento
        # isso apontaria pra FORA da arvore. Agora vem da raiz unica.
        from .raizes import TRACK
        self.track_root = TRACK
        self.process: subprocess.Popen | None = None
        self.owned = False
        self.running = False
        self._heartbeat_thread: threading.Thread | None = None

    def _ping(self) -> bool:
        try:
            return bool(send_command({"cmd": "ping"}, timeout=0.6).get("ok"))
        except Exception:
            return False

    def start(self) -> None:
        if self._ping():
            logger.info("Tracker V11 ja esta ativo em :8770; reutilizando instancia.")
            self.running = True
            self._start_heartbeat()
            return
        python = self.track_root / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        script = self.track_root / "vts_fake_tracker_v11.py"
        if not python.exists() or not script.exists():
            logger.warning(f"Tracker V11 externo indisponivel em {self.track_root}; servidor continua sem ele.")
            return
        logs = self.track_root / "logs"
        logs.mkdir(parents=True, exist_ok=True)
        log_file = open(logs / "tracker.log", "a", encoding="utf-8")
        command = [
            str(python), str(script), "--managed", "--parent-pid", str(os.getpid()),
            "--spool-dir", str(self.track_root / "spool"),
            "--emotion-mirror-file", str(self.project_root / "current_emotion.txt"),
        ]
        self.process = subprocess.Popen(
            command,
            cwd=self.track_root,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
        self.owned = True
        for _ in range(30):
            if self._ping():
                self.running = True
                self._start_heartbeat()
                logger.info("Tracker V11 externo iniciado e respondendo em :8770.")
                return
            if self.process.poll() is not None:
                break
            time.sleep(0.2)
        logger.warning("Tracker V11 nao respondeu no startup; servidor continua sem bloquear.")

    def _start_heartbeat(self) -> None:
        if self._heartbeat_thread and self._heartbeat_thread.is_alive():
            return

        def loop():
            while self.running:
                try:
                    send_command({"cmd": "supervisor_heartbeat"}, timeout=0.8)
                except Exception:
                    pass
                time.sleep(5)

        self._heartbeat_thread = threading.Thread(target=loop, daemon=True, name="celia-track-heartbeat")
        self._heartbeat_thread.start()

    def stop(self) -> None:
        self.running = False
        if self.owned and self.process and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


def start_track_process(project_root: Path) -> TrackProcessManager:
    manager = TrackProcessManager(project_root)
    manager.start()
    atexit.register(manager.stop)
    return manager
