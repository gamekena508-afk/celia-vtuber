import json

from celia import games_routes


def setup_function():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._GAME_SESSION_CANDIDATES.clear()
    games_routes._GAME_AUTHORITY_EPOCHS.clear()


def register_chess():
    payload = games_routes.GameSessionUpdate(
        match_id="ctx-match",
        mode="criador-vs-celia",
        current_controller="celia",
        legal_actions=["e7e5", "d7d5"],
        state={
            "game": "chess",
            "match_id": "ctx-match",
            "fen": "test-fen",
            "board": [[None]],
            "current_player": "celia",
            "legal_actions": ["e7e5", "d7d5"],
            "last_move": "e2e4",
            "board_revision": 1,
            "special": None,
        },
        visible=True,
        focused=True,
        instance_id="ctx-tab",
        board_revision=1,
    )
    assert games_routes.update_active_game_session("chess", payload)


def write_latest_event(tmp_path):
    games_routes._LOG_DIR = tmp_path
    event = {
        "timestamp": "2026-09-26T00:00:00+00:00",
        "log_schema": 2,
        "match_id": "ctx-match",
        "turn": 1,
        "actor": "human",
        "action_author": "criador",
        "action_executor": "human_ui",
        "move_from": "e2",
        "move_to": "e4",
        "next_player": "celia",
        "board_before": [[None]],
        "requested_move": "e2e4",
        "valid_move": True,
        "board_after": [[None]],
        "legal_actions": ["e2e4"],
        "captured": {},
        "commentary": "",
        "result": None,
        "special": None,
    }
    (tmp_path / "chess-ctx-match.jsonl").write_text(
        json.dumps(event, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def test_tactical_context_contains_board_and_explicit_action_author(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    register_chess()
    write_latest_event(tmp_path)

    context = games_routes.active_game_context("chess", level="tactical")

    assert "Última ação confirmada: Criador jogou e2e4." in context
    assert "Executor técnico da última ação: human_ui." in context
    assert "Próximo jogador/controlador: celia." in context
    assert "FEN: test-fen." in context
    assert "Ações legais agora:" in context


def test_narrative_context_omits_tactical_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    register_chess()
    write_latest_event(tmp_path)

    context = games_routes.active_game_context("chess", level="narrative")

    assert "Última ação confirmada: Criador jogou e2e4." in context
    assert "FEN:" not in context
    assert "Ações legais agora:" not in context
    assert "não uma ordem para continuar falando do jogo" in context


def test_none_context_injects_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(games_routes, "_LOG_DIR", tmp_path)
    register_chess()
    write_latest_event(tmp_path)

    assert games_routes.active_game_context("chess", level="none") == ""
