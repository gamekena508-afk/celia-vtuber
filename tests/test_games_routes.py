import asyncio
import time

import pytest
from fastapi import HTTPException

from celia import game_browser, games_routes
from celia.mcpp import mcp_client


def _event(**changes):
    event = {
        "log_schema": 2,
        "match_id": "match_01",
        "turn": 1,
        "actor": "human",
        "board_before": [""],
        "requested_move": "0",
        "valid_move": True,
        "board_after": ["X"],
        "legal_actions": ["0"],
        "captured": [],
        "commentary": "Criador jogou.",
        "result": None,
        "special": None,
    }
    event.update(changes)
    return event


def test_game_log_persists_a_jsonl_event(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    path = games_routes.write_game_log(
        "tic-tac-toe", "match_01", _event()
    )

    assert path == tmp_path / "tic-tac-toe-match_01.jsonl"
    record = path.read_text(encoding="utf-8")
    assert '"actor": "human"' in record
    assert '"timestamp"' in record


def test_game_log_rejects_unsafe_identifiers(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)

    with pytest.raises(HTTPException) as error:
        games_routes.write_game_log("tic-tac-toe", "../escape", {})

    assert error.value.status_code == 400
    assert not list(tmp_path.iterdir())


def test_v2_game_log_rejects_mismatched_identity_and_illegal_claim(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)

    with pytest.raises(HTTPException, match="match_id"):
        games_routes.write_game_log("tic-tac-toe", "match_01", _event(match_id="other"))
    with pytest.raises(HTTPException, match="legal_actions"):
        games_routes.write_game_log("tic-tac-toe", "match_01", _event(requested_move="9"))


def test_v2_game_log_requires_continuous_turn_and_board(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    games_routes.write_game_log("tic-tac-toe", "match_01", _event())

    with pytest.raises(HTTPException, match="turn does not continue"):
        games_routes.write_game_log("tic-tac-toe", "match_01", _event(turn=3))
    with pytest.raises(HTTPException, match="board_before"):
        games_routes.write_game_log(
            "tic-tac-toe",
            "match_01",
            _event(turn=2, actor="celia", board_before=["wrong"], requested_move="1", legal_actions=["1"], board_after=["X", "O"]),
        )


def test_terminal_game_log_rejects_followup_and_persists_pending_result(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    terminal = _event(
        result="criador",
        winner_controller="criador",
        board_revision_after=1,
    )
    path = games_routes.write_game_log("tic-tac-toe", "match_01", terminal)

    status = games_routes._read_terminal_result("tic-tac-toe", "match_01")
    assert status is not None
    assert status["acknowledged"] is False
    assert status["winner_controller"] == "criador"
    assert status["turn"] == 1

    with pytest.raises(HTTPException) as error:
        games_routes.write_game_log(
            "tic-tac-toe",
            "match_01",
            _event(
                turn=2,
                actor="celia",
                board_before=["X"],
                requested_move="1",
                legal_actions=["1"],
                board_after=["X", "O"],
            ),
        )

    assert error.value.status_code == 409
    assert error.value.detail["code"] == "game_already_ended"
    assert len(path.read_text(encoding="utf-8").splitlines()) == 1


def test_checkers_legacy_human_result_is_canonicalized_to_criador(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    games_routes.write_game_log(
        "checkers",
        "legacy-human-result",
        _event(
            match_id="legacy-human-result",
            result="human",
            winner_controller="human",
            board_revision_after=4,
        ),
    )

    status = games_routes._read_terminal_result("checkers", "legacy-human-result")

    assert status is not None
    assert status["winner_controller"] == "criador"
    assert status["result"] == "criador"


def test_terminal_result_ack_state_is_persisted(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    games_routes.write_game_log(
        "checkers",
        "match_01",
        _event(
            result="celia",
            winner_controller="celia",
            board_revision_after=9,
        ),
    )
    payload = games_routes.GameResultAck(
        match_id="match_01",
        turn=1,
        winner_controller="celia",
        result="celia",
        score={"criador": 0, "celia": 1},
        instance_id="tab-a",
        authority_epoch=3,
        board_revision=9,
    )
    games_routes._mark_terminal_result_acknowledged(
        "checkers", "match_01", payload, "celia"
    )

    status = games_routes._read_terminal_result("checkers", "match_01")
    assert status is not None
    assert status["acknowledged"] is True
    assert status["board_revision"] == 9
    assert status["score"] == {"criador": 0, "celia": 1}


def test_terminal_session_forces_empty_legal_actions():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_SESSION_CANDIDATES.clear()
    payload = games_routes.GameSessionUpdate(
        match_id="ended-match",
        mode="criador-vs-celia",
        current_controller="criador",
        legal_actions=["after-end"],
        state={
            "game": "checkers",
            "match_id": "ended-match",
            "current_player": "criador",
            "legal_actions": ["after-end"],
            "game_over": True,
            "result": "celia",
            "winner_controller": "celia",
            "board_revision": 12,
        },
        visible=True,
        focused=True,
        instance_id="tab-ended",
        board_revision=12,
    )

    assert games_routes.update_active_game_session("checkers", payload) is True
    session = games_routes._ACTIVE_GAME_SESSIONS["checkers"]
    assert session["legal_actions"] == []
    assert session["state"]["legal_actions"] == []


def test_game_control_is_bound_to_the_live_match():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_CONTROLS.clear()
    games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"] = {
        "match_id": "m-live",
        "mode": "criador-vs-celia",
        "current_controller": "celia",
        "legal_actions": ["4"],
        "updated_at": time.monotonic(),
    }

    command = games_routes.issue_game_control(
        "pause", game_id="tic-tac-toe", match_id="m-live"
    )

    assert command is not None
    assert command["action"] == "pause"
    assert command["match_id"] == "m-live"
    assert games_routes.issue_game_control(
        "resume", game_id="tic-tac-toe", match_id="old-match"
    ) is None


def test_stale_game_session_cannot_receive_recording_controls():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_CONTROLS.clear()
    games_routes._ACTIVE_GAME_SESSIONS["chess"] = {
        "match_id": "stale",
        "mode": "criador-vs-celia",
        "current_controller": "criador",
        "legal_actions": [],
        "updated_at": time.monotonic() - games_routes._GAME_SESSION_TTL_SECONDS - 1,
    }

    assert games_routes.issue_game_control("pause", game_id="chess") is None
    assert "chess" not in games_routes._ACTIVE_GAME_SESSIONS


def test_focused_game_tab_owns_session_and_background_tab_cannot_steal_it():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    focused = games_routes.GameSessionUpdate(
        match_id="match-a",
        mode="criador-vs-celia",
        current_controller="criador",
        legal_actions=["1", "2"],
        state={"game": "tic-tac-toe", "board": ["X", "", "", "", "", "", "", "", ""], "last_move": "0"},
        visible=True,
        focused=True,
        instance_id="tab-a",
    )
    background = games_routes.GameSessionUpdate(
        match_id="match-b",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["3", "4"],
        state={"game": "tic-tac-toe", "board": ["", "O", "", "", "", "", "", "", ""]},
        visible=True,
        focused=False,
        instance_id="tab-b",
    )

    assert games_routes.update_active_game_session("tic-tac-toe", focused) is True
    assert games_routes.update_active_game_session("tic-tac-toe", background) is False
    active = games_routes.active_game_session("tic-tac-toe")
    assert active is not None
    assert active[1]["match_id"] == "match-a"


def test_same_browser_instance_can_restart_match_and_context_tracks_real_board():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    first = games_routes.GameSessionUpdate(
        match_id="match-a",
        mode="criador-vs-celia",
        current_controller="criador",
        legal_actions=["1"],
        state={"game": "tic-tac-toe", "board": ["X", "", "", "", "", "", "", "", ""], "last_move": "0"},
        visible=True,
        focused=True,
        instance_id="tab-a",
    )
    second = games_routes.GameSessionUpdate(
        match_id="match-b",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["4", "8"],
        state={
            "game": "tic-tac-toe",
            "board": ["X", "", "O", "", "", "", "", "", ""],
            "current_player": "celia",
            "last_move": "2",
            "legal_actions": ["4", "8"],
        },
        visible=True,
        focused=True,
        instance_id="tab-a",
    )

    games_routes.update_active_game_session("tic-tac-toe", first)
    assert games_routes.update_active_game_session("tic-tac-toe", second) is True
    context = games_routes.active_game_context("tic-tac-toe")

    assert "jogo da velha" in context
    assert "match-b" in context
    assert "Vez/controlador atual: celia" in context
    assert '["X","","O"' in context
    assert '["4", "8"]' in context


def test_other_equally_focused_tab_cannot_steal_live_game_authority():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    first = games_routes.GameSessionUpdate(
        match_id="match-a",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["4"],
        state={"game": "tic-tac-toe", "current_player": "celia", "legal_actions": ["4"]},
        visible=True,
        focused=True,
        instance_id="tab-a",
    )
    intruder = games_routes.GameSessionUpdate(
        match_id="match-b",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["8"],
        state={"game": "tic-tac-toe", "current_player": "celia", "legal_actions": ["8"]},
        visible=True,
        focused=True,
        instance_id="tab-b",
    )

    assert games_routes.update_active_game_session("tic-tac-toe", first) is True
    assert games_routes.update_active_game_session("tic-tac-toe", intruder) is False
    active = games_routes.active_game_session("tic-tac-toe")
    assert active is not None
    assert active[1]["match_id"] == "match-a"
    assert active[1]["instance_id"] == "tab-a"


def test_exact_browser_release_frees_authority_and_drops_stale_control():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_CONTROLS.clear()
    payload = games_routes.GameSessionUpdate(
        match_id="match-a",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["4"],
        state={
            "game": "tic-tac-toe",
            "current_player": "celia",
            "legal_actions": ["4"],
            "board": ["X", "O", "X", "O", "", "X", "O", "X", "O"],
        },
        visible=True,
        focused=True,
        instance_id="tab-a",
    )
    assert games_routes.update_active_game_session("tic-tac-toe", payload) is True
    command = games_routes.issue_game_control(
        "play", game_id="tic-tac-toe", match_id="match-a"
    )
    assert command is not None

    wrong = games_routes.GameSessionRelease(match_id="match-a", instance_id="tab-b")
    assert games_routes.release_active_game_session("tic-tac-toe", wrong) is False
    assert games_routes.active_game_session("tic-tac-toe") is not None

    exact = games_routes.GameSessionRelease(match_id="match-a", instance_id="tab-a")
    assert games_routes.release_active_game_session("tic-tac-toe", exact) is True
    assert games_routes.active_game_session("tic-tac-toe") is None
    assert "tic-tac-toe" not in games_routes._GAME_CONTROLS

    replacement = games_routes.GameSessionUpdate(
        match_id="match-b",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["8"],
        state={"current_player": "celia", "legal_actions": ["8"]},
        visible=True,
        focused=True,
        instance_id="tab-b",
    )
    assert games_routes.update_active_game_session("tic-tac-toe", replacement) is True


def test_explicit_celia_play_command_uses_real_live_state():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_CONTROLS.clear()
    payload = games_routes.GameSessionUpdate(
        match_id="play-live",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["4", "8"],
        state={
            "game": "tic-tac-toe",
            "current_player": "celia",
            "legal_actions": ["4", "8"],
            "board": ["X", "", "O", "", "", "", "", "", ""],
        },
        visible=True,
        focused=True,
        instance_id="tab-play",
    )
    games_routes.update_active_game_session("tic-tac-toe", payload)

    result = games_routes.request_explicit_game_play("CÉLIA, JOGA!")

    assert result is not None
    assert result["status"] == "queued"
    assert result["command"]["action"] == "play"
    assert result["match_id"] == "play-live"

    payload.current_controller = "criador"
    payload.legal_actions = ["0", "1"]
    payload.state = {
        **(payload.state or {}),
        "current_player": "criador",
        "legal_actions": ["0", "1"],
    }
    games_routes.update_active_game_session("tic-tac-toe", payload)
    blocked = games_routes.request_explicit_game_play("celia joga")
    assert blocked is not None
    assert blocked["status"] == "not_celia_turn"
    assert blocked["current_controller"] == "criador"


@pytest.mark.parametrize(
    ("game_id", "match_id", "instance_id", "legal_actions", "extra_state"),
    [
        (
            "checkers",
            "checkers-play-live",
            "tab-checkers-play",
            ["5,0-4,1", "5,2-4,3"],
            {"variant": "damas-brasileiras", "board": [[None] * 8 for _ in range(8)]},
        ),
        (
            "chess",
            "chess-play-live",
            "tab-chess-play",
            ["e7e5", "g8f6"],
            {"fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1"},
        ),
    ],
)
def test_explicit_play_contract_is_bound_to_real_checkers_and_chess_session(
    game_id, match_id, instance_id, legal_actions, extra_state
):
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_SESSION_CANDIDATES.clear()
    games_routes._GAME_CONTROLS.clear()
    games_routes._GAME_AUTHORITY_EPOCHS.clear()
    payload = games_routes.GameSessionUpdate(
        match_id=match_id,
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=legal_actions,
        state={
            "game": game_id,
            "match_id": match_id,
            "current_player": "celia",
            "legal_actions": legal_actions,
            "board_revision": 7,
            **extra_state,
        },
        visible=True,
        focused=True,
        instance_id=instance_id,
        board_revision=7,
    )
    assert games_routes.update_active_game_session(game_id, payload) is True
    session = games_routes._ACTIVE_GAME_SESSIONS[game_id]

    result = games_routes.request_explicit_game_play("Célia, joga")

    assert result is not None
    assert result["status"] == "queued"
    command = result["command"]
    assert command["action"] == "play"
    assert command["game"] == game_id
    assert command["match_id"] == match_id
    assert command["instance_id"] == instance_id
    assert command["board_revision"] == 7
    assert command["authority_epoch"] == session["authority_epoch"]
    assert games_routes._GAME_CONTROLS[game_id]["action_id"] == command["action_id"]

    payload.current_controller = "criador"
    payload.legal_actions = [str(legal_actions[0])]
    payload.board_revision = 8
    payload.authority_epoch = session["authority_epoch"]
    payload.state = {
        **(payload.state or {}),
        "current_player": "criador",
        "legal_actions": [str(legal_actions[0])],
        "board_revision": 8,
    }
    assert games_routes.update_active_game_session(game_id, payload) is True
    blocked = games_routes.request_explicit_game_play("Célia, joga")
    assert blocked is not None
    assert blocked["status"] == "not_celia_turn"
    assert blocked["game"] == game_id
    assert blocked["match_id"] == match_id


def test_singleton_explicit_play_queues_game_control_without_conversation_reply():
    from celia.celia_singleton import CeliaSingleton

    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_CONTROLS.clear()
    payload = games_routes.GameSessionUpdate(
        match_id="singleton-play",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["4"],
        state={
            "game": "tic-tac-toe",
            "current_player": "celia",
            "legal_actions": ["4"],
            "board": ["X", "O", "X", "O", "", "X", "O", "X", "O"],
        },
        visible=True,
        focused=True,
        instance_id="tab-singleton",
    )
    games_routes.update_active_game_session("tic-tac-toe", payload)

    class Stub:
        def __init__(self):
            self._recent_human_inputs = {}
            self._human_input_dedupe_window_s = 2.5
            self.broadcasts = []

        async def broadcast(self, payload):
            self.broadcasts.append(payload)

    stub = Stub()
    accepted = asyncio.run(CeliaSingleton.submit_input(
        stub,
        "Célia, joga",
        origin="microphone",
        speaker_name="Criador",
        behavior_observed=True,
    ))

    assert accepted is True
    assert len(stub.broadcasts) == 1
    assert stub.broadcasts[0]["type"] == "game-control-status"
    assert stub.broadcasts[0]["status"] == "queued"
    assert games_routes._GAME_CONTROLS["tic-tac-toe"]["action"] == "play"
    assert not hasattr(stub, "_event_queue"), "queued game command must not enter conversational reply path"


def test_game_context_reports_finished_result_and_winner():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    payload = games_routes.GameSessionUpdate(
        match_id="finished",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=[],
        state={
            "game": "tic-tac-toe",
            "current_player": "celia",
            "game_over": True,
            "result": "celia_win",
            "winner_controller": "celia",
            "legal_actions": [],
            "board": ["X", "O", "X", "O", "X", "O", "O", "X", "X"],
        },
        visible=True,
        focused=True,
        instance_id="tab-finished",
    )
    games_routes.update_active_game_session("tic-tac-toe", payload)

    context = games_routes.active_game_context("tic-tac-toe")
    result = games_routes.request_explicit_game_play("Célia, joga")

    assert "Partida encerrada: sim." in context
    assert "Resultado: celia_win." in context
    assert "Vencedor/controlador: celia." in context
    assert result is not None and result["status"] == "ended"


def test_focused_game_wins_over_newer_background_different_game():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"] = {
        "match_id": "focused-ttt",
        "mode": "criador-vs-celia",
        "current_controller": "criador",
        "legal_actions": ["1"],
        "state": {"board": ["X", "", "", "", "", "", "", "", ""]},
        "visible": True,
        "focused": True,
        "instance_id": "tab-ttt",
        "updated_at": time.monotonic() - 1.0,
    }
    games_routes._ACTIVE_GAME_SESSIONS["chess"] = {
        "match_id": "background-chess",
        "mode": "criador-vs-celia",
        "current_controller": "celia",
        "legal_actions": ["e7e5"],
        "state": {"fen": "test-fen"},
        "visible": True,
        "focused": False,
        "instance_id": "tab-chess",
        "updated_at": time.monotonic(),
    }

    active = games_routes.active_game_session()
    assert active is not None
    assert active[0] == "tic-tac-toe"
    assert active[1]["match_id"] == "focused-ttt"


def test_generic_active_game_ignores_invalid_legacy_heartbeat():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    now = time.monotonic()
    games_routes._ACTIVE_GAME_SESSIONS["tic-tac-toe"] = {
        "match_id": "legacy-ttt",
        "mode": "criador-vs-celia",
        "current_controller": "criador",
        "legal_actions": [],
        "state": {"current_player": "criador"},
        "visible": True,
        "focused": True,
        "instance_id": "",
        "board_revision": 0,
        "updated_at": now,
    }
    games_routes._ACTIVE_GAME_SESSIONS["checkers"] = {
        "match_id": "live-checkers",
        "mode": "criador-vs-celia",
        "current_controller": "criador",
        "legal_actions": ["12-16"],
        "state": {
            "game": "checkers",
            "match_id": "live-checkers",
            "board_revision": 1,
            "current_player": "criador",
            "legal_actions": ["12-16"],
        },
        "visible": True,
        "focused": False,
        "instance_id": "tab-checkers",
        "board_revision": 1,
        "updated_at": now - 1.0,
    }

    active = games_routes.active_game_session()
    assert active is not None
    assert active[0] == "checkers"
    assert active[1]["match_id"] == "live-checkers"
    assert games_routes.active_game_session("tic-tac-toe") is not None


def test_conversational_agent_reads_authoritative_game_context():
    from celia.agent.agents.basic_memory_agent import BasicMemoryAgent

    games_routes._ACTIVE_GAME_SESSIONS.clear()
    payload = games_routes.GameSessionUpdate(
        match_id="live-conversation",
        mode="criador-vs-celia",
        current_controller="criador",
        legal_actions=["1", "3"],
        state={
            "game": "tic-tac-toe",
            "board": ["X", "", "O", "", "", "", "", "", ""],
            "current_player": "criador",
            "legal_actions": ["1", "3"],
        },
        visible=True,
        focused=True,
        instance_id="tab-live",
    )
    games_routes.update_active_game_session("tic-tac-toe", payload)

    agent = object.__new__(BasicMemoryAgent)
    context = agent._read_game_mood()

    assert context is not None
    assert "ESTADO REAL DO JOGO ATIVO" in context
    assert "Vez/controlador atual: criador" in context
    assert '["1", "3"]' in context


def test_open_game_browser_uses_normal_new_window_without_forcing_fullscreen(monkeypatch):
    calls = []

    class DummyProcess:
        def poll(self):
            return None

    def fake_popen(args, **kwargs):
        calls.append((args, kwargs))
        return DummyProcess()

    monkeypatch.setattr(game_browser, "_detect_browser_command", lambda: ["/usr/bin/firefox"])
    monkeypatch.setattr(game_browser.subprocess, "Popen", fake_popen)

    result = games_routes.open_game_browser("checkers", "criador-vs-celia")

    assert result["ok"] is True
    assert result["url"] == (
        "http://127.0.0.1:12393/games/checkers/?mode=criador-vs-celia"
    )
    assert calls[0][0] == ["/usr/bin/firefox", "--new-window", result["url"]]
    launch_line = " ".join(calls[0][0]).lower()
    assert "fullscreen" not in launch_line
    assert "--app" not in launch_line
    assert "kiosk" not in launch_line


def test_chrome_game_browser_uses_current_session_profile_and_x11(monkeypatch):
    monkeypatch.setattr(game_browser, "_session_profile_dir", lambda: "/tmp/celia-session-profile")
    args = game_browser._launch_args(
        ["/usr/bin/google-chrome-stable"],
        "http://127.0.0.1:12393/games/checkers/?mode=criador-vs-celia",
    )

    assert "--ozone-platform=x11" in args
    assert "--user-data-dir=/tmp/celia-session-profile" in args
    assert "--no-first-run" in args
    assert "--no-default-browser-check" in args
    assert "--new-window" in args
    assert "--app" not in args
    assert "--kiosk" not in args
    assert "--start-fullscreen" not in args


def test_games_mcp_inherits_only_required_gui_environment(monkeypatch):
    monkeypatch.setattr(mcp_client, "get_default_environment", lambda: {"PATH": "/usr/bin"})
    monkeypatch.setenv("DISPLAY", ":1")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XAUTHORITY", "/run/user/1000/xauth_test")
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setenv("CELIA_SECRET_SHOULD_NOT_LEAK", "nope")

    env = mcp_client._stdio_env_for_server("jogos", None)

    assert env["PATH"] == "/usr/bin"
    assert env["DISPLAY"] == ":1"
    assert env["WAYLAND_DISPLAY"] == "wayland-0"
    assert env["XAUTHORITY"] == "/run/user/1000/xauth_test"
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/run/user/1000/bus"
    assert env["XDG_RUNTIME_DIR"] == "/run/user/1000"
    assert "CELIA_SECRET_SHOULD_NOT_LEAK" not in env
    assert mcp_client._stdio_env_for_server("memoria", None) is None


def test_open_game_browser_falls_back_when_default_browser_rejects(monkeypatch):
    calls = []

    class DummyProcess:
        def __init__(self, returncode):
            self.returncode = returncode

        def poll(self):
            return self.returncode

    def fake_popen(args, **kwargs):
        calls.append(args)
        return DummyProcess(1 if "google-chrome-stable" in args[0] else 0)

    def fake_which(name):
        mapping = {
            "xdg-open": "/usr/bin/xdg-open",
            "gio": "/usr/bin/gio",
            "firefox": "/usr/bin/firefox",
            "google-chrome-stable": "/usr/bin/google-chrome-stable",
        }
        return mapping.get(name)

    monkeypatch.setattr(
        game_browser,
        "_detect_browser_command",
        lambda: ["/usr/bin/google-chrome-stable"],
    )
    monkeypatch.setattr(game_browser.shutil, "which", fake_which)
    monkeypatch.setattr(game_browser, "_session_profile_dir", lambda: "/tmp/celia-game-test-profile")
    monkeypatch.setattr(game_browser.subprocess, "Popen", fake_popen)

    result = games_routes.open_game_browser("tic-tac-toe", "criador-vs-celia")

    assert result["ok"] is True
    assert result["launcher"] == "firefox"
    assert calls[0][0] == "/usr/bin/google-chrome-stable"
    assert calls[1][0] == "/usr/bin/firefox"
    assert all(call[0] not in {"/usr/bin/xdg-open", "/usr/bin/gio"} for call in calls)
    assert result["failed_launchers"][0]["launcher"] == "google-chrome-stable"
    assert result["failed_launchers"][0]["status"] == 1
