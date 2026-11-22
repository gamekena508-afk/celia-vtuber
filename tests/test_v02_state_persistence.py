from __future__ import annotations

import concurrent.futures
import importlib
import json
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

from celia.core import raizes
from celia.core import state_store
from celia.core.memoria import Memoria
from celia.core.state_store import (
    StateCorruptError,
    atomic_write_json,
    default_state_root,
    locked_json_update,
    read_json,
)
from celia import chat_history_manager as history


def test_default_state_paths_and_legacy_override_preserve_expected_layout(tmp_path, monkeypatch):
    local = tmp_path / "LocalAppData"
    assert default_state_root({"LOCALAPPDATA": str(local)}, windows=True) == (
        local / "CeliaV02" / "state"
    ).resolve()

    try:
        with monkeypatch.context() as patch:
            patch.setenv("LOCALAPPDATA", str(local))
            patch.setenv("CELIA_STATE_DIR", str(tmp_path / "override"))
            patch.delenv("CELIA_VAR_DIR", raising=False)
            importlib.reload(raizes)
            assert raizes.CHAT_HISTORY == (tmp_path / "override" / "history").resolve()
            assert raizes.MEMORY_STATE == (tmp_path / "override" / "memory").resolve()
            assert raizes.SESSIONS == (tmp_path / "override" / "sessions").resolve()
            assert raizes.APPLICATION == (tmp_path / "override" / "application").resolve()

            patch.setenv("CELIA_VAR_DIR", str(tmp_path / "legacy-var"))
            importlib.reload(raizes)
            assert raizes.CHAT_HISTORY == (tmp_path / "legacy-var" / "chat_history").resolve()
            assert raizes.MEMORY_STATE == (tmp_path / "legacy-var" / "memoria").resolve()
    finally:
        importlib.reload(raizes)


def test_json_update_is_atomic_locked_and_keeps_a_previous_copy(tmp_path):
    path = tmp_path / "history" / "c1" / "h1.json"
    atomic_write_json(path, {"items": []}, backup=False)

    def add_item(value):
        value["items"].append(value_for_thread())

    def value_for_thread():
        return os.urandom(8).hex()

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _n: locked_json_update(path, add_item), range(80)))

    result = read_json(path)
    assert len(result["items"]) == 80
    assert len(set(result["items"])) == 80
    assert len(read_json(path.with_name(path.name + ".bak"))["items"]) == 79
    assert not list(path.parent.glob("*.tmp-*"))


def test_corrupt_history_is_preserved_without_reset_or_overwrite(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "CHAT_HISTORY", tmp_path / "history")
    path = Path(history._get_safe_history_path("celia", "existing-id"))
    path.parent.mkdir(parents=True)
    damaged = b'{"not valid json'
    path.write_bytes(damaged)

    history.store_message("celia", "existing-id", "human", "new text")

    assert path.read_bytes() == damaged
    assert not path.with_name(path.name + ".bak").exists()


def test_history_format_and_ids_survive_reopen_in_a_temporary_state_root(tmp_path, monkeypatch):
    history_dir = tmp_path / "state" / "history"
    try:
        with monkeypatch.context() as patch:
            patch.setenv("CELIA_STATE_DIR", str(tmp_path / "state"))
            patch.delenv("CELIA_VAR_DIR", raising=False)
            importlib.reload(raizes)
            importlib.reload(history)

            uid = history.create_new_history("celia_001")
            assert uid and "/" not in uid and "\\" not in uid
            history.store_message("celia_001", uid, "human", "Oi", name="Criador")
            history.store_message("celia_001", uid, "ai", "Olá")
            before = Path(history._get_safe_history_path("celia_001", uid)).read_bytes()

            importlib.reload(history)
            messages = history.get_history("celia_001", uid)
            after = Path(history._get_safe_history_path("celia_001", uid)).read_bytes()

            assert [message["role"] for message in messages][-2:] == ["human", "ai"]
            assert messages[-2]["name"] == "Criador"
            assert json.loads(after)[0]["role"] == "metadata"
            assert before == after
            assert (history_dir / "celia_001").is_dir()
    finally:
        importlib.reload(raizes)
        importlib.reload(history)


def test_atomic_replace_retries_transient_windows_sharing_violation(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"generation": 1}, backup=False)
    replace = os.replace
    attempts = 0

    class SharingViolation(OSError):
        winerror = 32

    def replace_with_brief_lock(source, destination):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise SharingViolation(32, "sharing violation")
        return replace(source, destination)

    monkeypatch.setattr(state_store.os, "replace", replace_with_brief_lock)
    atomic_write_json(target, {"generation": 2}, backup=False)

    assert attempts == 3
    assert read_json(target) == {"generation": 2}


def test_sustained_winerror_32_keeps_the_previous_json_and_removes_temp(tmp_path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"generation": 1}, backup=False)

    class SharingViolation(OSError):
        winerror = 32

    monkeypatch.setattr(state_store.os, "replace", lambda *_: (_ for _ in ()).throw(SharingViolation(32, "locked")))
    with pytest.raises(SharingViolation):
        atomic_write_json(target, {"generation": 2}, backup=False)

    assert read_json(target) == {"generation": 1}
    assert not list(tmp_path.glob("*.tmp-*"))


def test_history_components_reject_separators_and_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "CHAT_HISTORY", tmp_path / "history")
    for invalid in ("..", "../outside", "..\\outside", "a/b", "a\\b"):
        with pytest.raises(ValueError):
            history._get_safe_history_path(invalid, "history-id")


def test_sqlite_memory_reopens_and_creates_consistent_non_overwriting_backup(tmp_path):
    root = tmp_path / "state" / "memory"
    memory = Memoria(root)
    record_id = memory.registrar_turno(
        turn_id="turn-001", personagem="celia_001", input="Oi", resposta="Olá"
    )
    backup = tmp_path / "backup" / "memory.sqlite3"
    assert memory.backup(backup) == backup.resolve()

    read_only = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    try:
        assert read_only.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        row = read_only.execute("SELECT id, turn_id FROM registros WHERE id = ?", (record_id,)).fetchone()
        assert row == (record_id, "turn-001")
    finally:
        read_only.close()

    memory.close()
    reopened = Memoria(root)
    try:
        assert reopened.logs_do_turno("turn-001")[0]["id"] == record_id
        with pytest.raises(FileExistsError):
            reopened.backup(backup)
    finally:
        reopened.close()


def test_sqlite_short_connections_handle_concurrent_writers_and_backup(tmp_path):
    root = tmp_path / "memory"
    writers = [Memoria(root) for _ in range(3)]

    def add(pair):
        writer_index, item_index = pair
        return writers[writer_index].registrar_turno(
            turn_id=f"turn-{writer_index}-{item_index}",
            personagem="celia_001",
            input=f"pergunta {writer_index}-{item_index}",
            resposta="resposta",
        )

    pairs = [(writer, item) for writer in range(len(writers)) for item in range(20)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        ids = list(pool.map(add, pairs))
    assert len(set(ids)) == 60

    backup = tmp_path / "concurrent-snapshot.sqlite3"
    writers[0].backup(backup)
    db = sqlite3.connect(f"file:{backup.as_posix()}?mode=ro", uri=True)
    try:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        count = db.execute("SELECT count(*) FROM registros WHERE tipo='log'").fetchone()[0]
        assert count == 60
    finally:
        db.close()
        for writer in writers:
            writer.close()


@pytest.mark.skipif(os.name != "nt", reason="DACL audit applies to Windows NTFS")
def test_localappdata_state_fixture_inherits_ntfs_acl_without_exposing_principals(tmp_path):
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        pytest.skip("LOCALAPPDATA is unavailable")
    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if not powershell:
        pytest.skip("PowerShell ACL reader is unavailable")

    state_parent = Path(local_app_data) / "CeliaV02" / "state"
    state_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="acl-probe-", dir=state_parent) as probe:
        env = {**os.environ, "CELIA_ACL_PROBE_PATH": probe}
        command = (
            "$acl = Get-Acl -LiteralPath $env:CELIA_ACL_PROBE_PATH; "
            "if ($acl.AreAccessRulesProtected) { 'protected' } else { 'inherited' }"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        # Only the inheritance bit is collected; ACL principal names are never emitted.
        assert result.stdout.strip() == "inherited"
