from celia import games_routes


def state(*, controller="celia", board=None, legal=None, revision=0):
    board = list(board or [""] * 9)
    legal = list(legal if legal is not None else [str(i) for i, value in enumerate(board) if not value])
    return {
        "game": "tic-tac-toe",
        "current_player": controller,
        "legal_actions": legal,
        "board": board,
        "board_revision": revision,
    }


def reset():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_SESSION_CANDIDATES.clear()
    games_routes._GAME_AUTHORITY_EPOCHS.clear()
    games_routes._GAME_CONTROLS.clear()


def test_equal_tabs_keep_existing_authority_until_focus_resolves():
    reset()
    a = games_routes.GameSessionUpdate(
        match_id="match-a", mode="criador-vs-celia", current_controller="celia",
        legal_actions=["4"], state=state(legal=["4"]),
        visible=True, focused=False, instance_id="tab-a", board_revision=0,
    )
    b = games_routes.GameSessionUpdate(
        match_id="match-b", mode="criador-vs-celia", current_controller="celia",
        legal_actions=["4"], state=state(legal=["4"]),
        visible=True, focused=False, instance_id="tab-b", board_revision=0,
    )
    assert games_routes.update_active_game_session("tic-tac-toe", a) is True
    epoch_a = games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"]["authority_epoch"]
    assert games_routes.update_active_game_session("tic-tac-toe", b) is False
    active = games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"]
    assert active["match_id"] == "match-a"
    assert active["ambiguous"] is False
    queued = games_routes.request_explicit_game_play("Célia, joga")
    assert queued["status"] == "queued"
    assert queued["command"]["instance_id"] == "tab-a"
    assert queued["command"]["authority_epoch"] == epoch_a

    b.focused = True
    assert games_routes.update_active_game_session("tic-tac-toe", b) is True
    active = games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"]
    epoch_b = active["authority_epoch"]
    assert active["match_id"] == "match-b"
    assert epoch_b > epoch_a
    assert not active.get("ambiguous", False)

    a.authority_epoch = epoch_a
    assert games_routes.update_active_game_session("tic-tac-toe", a) is False
    assert games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"]["match_id"] == "match-b"


def test_board_revision_cannot_move_backwards():
    reset()
    board = ["X", "", "", "", "", "", "", "", ""]
    payload = games_routes.GameSessionUpdate(
        match_id="rev-match", mode="criador-vs-celia", current_controller="celia",
        legal_actions=["1"], state=state(board=board, legal=["1"], revision=2),
        visible=True, focused=True, instance_id="tab-rev", board_revision=2,
    )
    assert games_routes.update_active_game_session("tic-tac-toe", payload) is True
    epoch = games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"]["authority_epoch"]
    payload.authority_epoch = epoch
    payload.board_revision = 1
    payload.state = state(board=board, legal=["1"], revision=1)
    assert games_routes.update_active_game_session("tic-tac-toe", payload) is True
    assert games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"]["board_revision"] == 2
