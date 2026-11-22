"""Small, dependency-free primitives for durable user state."""
from __future__ import annotations

import json
import errno
import os
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


class StateCorruptError(ValueError):
    """Raised when an existing state document cannot be decoded safely."""


def default_state_root(
    environ: dict[str, str] | None = None,
    *,
    windows: bool | None = None,
    home: Path | None = None,
) -> Path:
    """Return the platform state directory without creating it."""
    env = os.environ if environ is None else environ
    is_windows = os.name == "nt" if windows is None else windows
    if is_windows:
        local = str(env.get("LOCALAPPDATA", "")).strip()
        base = Path(local).expanduser() if local else (home or Path.home()) / "AppData" / "Local"
    else:
        xdg = str(env.get("XDG_STATE_HOME", "")).strip()
        base = Path(xdg).expanduser() if xdg else (home or Path.home()) / ".local" / "state"
    return (base / "CeliaV02" / "state").resolve()


def _lock_path(path: Path) -> Path:
    return path.with_name(path.name + ".lock")


@contextmanager
def file_lock(path: str | Path, *, timeout: float = 30.0) -> Iterator[None]:
    """Lock a stable sidecar file across threads and processes."""
    lock_file = _lock_path(Path(path))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            deadline = time.monotonic() + timeout
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out acquiring state lock: {lock_file.name}")
                    time.sleep(0.025)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            deadline = time.monotonic() + timeout
            while True:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError as exc:
                    if exc.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise TimeoutError(f"timed out acquiring state lock: {lock_file.name}") from exc
                    time.sleep(0.025)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _replace_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")
    try:
        fd = os.open(str(temp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _replace_with_retry(source: Path, target: Path, *, attempts: int = 6) -> None:
    """Retry brief Windows sharing violations; preserve the old target on failure."""
    for attempt in range(attempts):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (32, 33) or attempt + 1 == attempts:
                raise
            time.sleep(min(0.025 * (2**attempt), 0.4))


def atomic_write_json(
    path: str | Path,
    value: Any,
    *,
    backup: bool = True,
    already_locked: bool = False,
) -> None:
    """Write JSON by same-directory replace, retaining the previous copy."""
    target = Path(path)
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

    def write() -> None:
        if backup and target.is_file():
            previous = target.read_bytes()
            _replace_bytes(target.with_name(target.name + ".bak"), previous)
        _replace_bytes(target, payload)

    if already_locked:
        write()
    else:
        with file_lock(target):
            write()


def read_json(path: str | Path) -> Any:
    target = Path(path)
    try:
        with target.open("r", encoding="utf-8-sig") as stream:
            return json.load(stream)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise StateCorruptError(f"invalid JSON state: {target.name}") from exc


def locked_json_update(
    path: str | Path,
    update: Callable[[Any], Any],
    *,
    default_factory: Callable[[], Any] = list,
) -> Any:
    """Read, update, and atomically replace one JSON document under one lock."""
    target = Path(path)
    with file_lock(target):
        value = read_json(target) if target.exists() else default_factory()
        result = update(value)
        atomic_write_json(target, value, already_locked=True)
        return result
