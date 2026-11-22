from pathlib import Path

ROOT = Path(__file__).parents[1]
TTT = ROOT / "integrations" / "games" / "tic-tac-toe" / "index.html"


def test_tic_tac_toe_waits_for_authority_instead_of_fixed_turn_timer():
    source = TTT.read_text(encoding="utf-8")
    assert "turnTimer" not in source
    assert "async function scheduleTurn(reason)" in source
    assert "const heartbeat = await syncSession();" in source
    assert "heartbeat.active === false" in source
    assert "onAuthorityConfirmed(heartbeat)" in source
    assert "lastAutoPlayKey" in source


def test_tic_tac_toe_uses_per_tab_storage_and_monotonic_board_revision():
    source = TTT.read_text(encoding="utf-8")
    assert "sessionStorage.getItem(storageKey)" in source
    assert "sessionStorage.setItem(storageKey" in source
    assert "localStorage.getItem(storageKey)" not in source
    assert "board_revision: boardRevision" in source
    assert source.count("boardRevision += 1") >= 4
    assert "boardRevision = 0;" in source


def test_tic_tac_toe_reserved_speech_and_apply_share_same_move_contract():
    source = TTT.read_text(encoding="utf-8")
    assert "formatReservedCommentary(action)" in source
    assert "Number(reservation.revision || 0) !== boardRevision" in source
    assert "reservation.instanceId !== bridge.instanceId" in source
    assert "reservation.actionId" in source
    assert "window.__celiaGameDebug" in source
