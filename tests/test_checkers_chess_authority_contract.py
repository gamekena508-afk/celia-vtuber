from pathlib import Path

ROOT = Path(__file__).parents[1]
CHECKERS = ROOT / "integrations" / "games" / "checkers" / "index.html"
CHESS = ROOT / "integrations" / "games" / "chess" / "index.html"


def test_checkers_waits_for_authority_and_validates_reserved_revision():
    source = CHECKERS.read_text(encoding="utf-8")
    assert "turnTimer" not in source
    assert "async function scheduleTurn(reason)" in source
    assert "const heartbeat = await syncSession();" in source
    assert "heartbeat.active === false" in source
    assert "onAuthorityConfirmed(heartbeat)" in source
    assert "lastAutoPlayKey" in source
    assert "Number(reservation.revision || 0) !== boardRevision" in source
    assert "reservation.instanceId !== bridge.instanceId" in source


def test_checkers_uses_per_tab_storage_and_terminal_revision_sync():
    source = CHECKERS.read_text(encoding="utf-8")
    assert "sessionStorage.getItem(storageKey)" in source
    assert "sessionStorage.setItem(storageKey" in source
    assert "localStorage.getItem(storageKey)" not in source
    assert "board_revision: boardRevision" in source
    assert "if (applied) boardRevision += 1;" in source
    assert "await syncSession();\n                await bridge.finalizeResult" in source
    assert "game-authority-20260923b" in source
    assert "bridge.beginNewMatch(options.reason || 'manual_restart')" in source
    assert "window.__celiaCheckersTest" in source
    assert '<div class="panel-title">CAPTURAS</div>' in source
    assert "window.__celiaGameDebug" in source


def test_chess_waits_for_authority_and_validates_reserved_revision():
    source = CHESS.read_text(encoding="utf-8")
    assert "turnTimer" not in source
    assert "async function maybeBot(reason)" in source
    assert "const heartbeat=await syncGameSession();" in source
    assert "heartbeat.active===false" in source
    assert "onAuthorityConfirmed(heartbeat)" in source
    assert "lastAutoPlayKey" in source
    assert "Number(reservation.revision||0)!==boardRevision" in source
    assert "reservation.instanceId!==gameBridge.instanceId" in source


def test_chess_uses_per_tab_storage_and_terminal_revision_sync():
    source = CHESS.read_text(encoding="utf-8")
    assert "sessionStorage.getItem(gameStorageKey)" in source
    assert "sessionStorage.setItem(gameStorageKey" in source
    assert "localStorage.getItem(gameStorageKey)" not in source
    assert "board_revision:boardRevision" in source
    assert "boardRevision+=1;" in source
    assert "await syncGameSession();\n    await gameBridge.finalizeResult" in source
    assert "game-authority-20260923b" in source
    assert "gameBridge.beginNewMatch(options.reason||'manual_restart')" in source
    assert "window.__celiaChessTest" in source
    assert "window.__celiaGameDebug" in source
