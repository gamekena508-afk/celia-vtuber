import json
import subprocess
import time
from pathlib import Path

from celia import games_routes

ROOT = Path(__file__).parents[1]
GAME_REQUEST = ROOT / "tools" / "pet-electron" / "game-request.js"


def test_game_request_accepts_only_canonical_games_and_modes():
    script = (
        "const g=require(process.argv[1]);"
        "process.stdout.write(JSON.stringify(["
        "g.parseGameRequest(['--game=chess','--game-mode=chat-vs-criador']),"
        "g.parseGameRequest(['--game=checkers']),"
        "g.parseGameRequest(['--game=doom']),"
        "g.parseGameCommand(['--close-game'])]));"
    )
    out = subprocess.check_output(["node", "-e", script, str(GAME_REQUEST)], text=True)
    parsed = json.loads(out)
    assert parsed[0] == {"game": "chess", "mode": "chat-vs-criador"}
    assert parsed[1] == {"game": "checkers", "mode": "criador-vs-celia"}
    assert parsed[2] is None
    assert parsed[3] == {"action": "close"}


def test_chat_message_routes_only_on_a_waiting_chat_turn():
    games_routes._ACTIVE_GAME_SESSIONS.clear()
    games_routes._CHAT_GAME_MOVES.clear()
    games_routes._ACTIVE_GAME_SESSIONS["chess"] = {
        "match_id": "m1",
        "mode": "chat-vs-celia",
        "current_controller": "chat",
        "legal_actions": ["e2e4", "d2d4"],
        "updated_at": time.monotonic(),
    }
    routed = games_routes.submit_chat_game_message("alice", "joga e2e4")
    assert routed and routed["action"] == "e2e4"
    assert games_routes._CHAT_GAME_MOVES[("chess", "m1")]["author"] == "alice"

    games_routes._CHAT_GAME_MOVES.clear()
    games_routes._ACTIVE_GAME_SESSIONS["chess"]["current_controller"] = "criador"
    assert games_routes.submit_chat_game_message("bob", "d2d4") is None


def test_tic_tac_toe_chat_uses_human_friendly_cells_1_to_9():
    assert games_routes._resolve_chat_action("tic-tac-toe", "casa 5", ["0", "4", "8"]) == "4"
    assert games_routes._resolve_chat_action("tic-tac-toe", "9", ["0", "4", "8"]) == "8"
    assert games_routes._resolve_chat_action("tic-tac-toe", "2", ["0", "4", "8"]) is None


def test_automatic_game_opening_no_longer_routes_through_electron():
    main = (ROOT / "tools" / "pet-electron" / "main.js").read_text()
    mcp = (ROOT / "mcp" / "jogos_mcp.py").read_text()
    routes = (ROOT / "src" / "celia" / "games_routes.py").read_text()
    browser = (ROOT / "src" / "celia" / "game_browser.py").read_text()

    # The old Electron game window remains only as legacy code; neither voice
    # nor the recording panel may invoke it automatically anymore.
    assert "function createGameWindow(request)" in main
    assert "PET_LAUNCHER" not in mcp
    assert "run_pet.sh" not in mcp
    assert "open_game_browser" in mcp
    assert "from .game_browser import open_game_browser" in routes
    assert "--new-window" in browser
    assert "--app" not in browser
    assert "--kiosk" not in browser
