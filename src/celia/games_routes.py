"""Safe persistence endpoints for browser game adapters."""

from __future__ import annotations

import json
import os
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from .core.raizes import RAIZ
from .core.roteiro_live import roteiro_live
from .game_browser import open_game_browser

_GAME_ID = re.compile(r"^[a-z0-9-]{1,48}$")
_MATCH_ID = re.compile(r"^[a-zA-Z0-9_-]{1,96}$")
_LOG_DIR = RAIZ / "integrations" / "games" / "logs"
_EVENT_SCHEMA_VERSION = 2
_ACTORS = frozenset({"human", "celia"})
_EVENT_FIELDS = frozenset(
    {
        "log_schema",
        "match_id",
        "turn",
        "actor",
        "board_before",
        "requested_move",
        "valid_move",
        "board_after",
        "legal_actions",
        "captured",
        "commentary",
        "result",
        "special",
    }
)


class GameLogEvent(BaseModel):
    match_id: str
    event: dict[str, Any]


class GameSessionUpdate(BaseModel):
    match_id: str
    mode: str
    current_controller: str
    legal_actions: list[str | int]
    state: dict[str, Any] | None = None
    visible: bool = True
    focused: bool = False
    instance_id: str | None = None
    board_revision: int = 0
    authority_epoch: int | None = None
    request_takeover: bool = False


class GameSessionRelease(BaseModel):
    match_id: str
    instance_id: str


class GameControlRequest(BaseModel):
    action: str
    match_id: str | None = None
    requested_action: str | int | None = None


class GameResultAck(BaseModel):
    match_id: str
    turn: int
    winner_controller: str
    result: str
    reason: Any = None
    score: dict[str, Any] | None = None
    instance_id: str | None = None
    authority_epoch: int | None = None
    board_revision: int | None = None


_GAME_MODES = frozenset({"criador-vs-celia", "chat-vs-celia", "chat-vs-criador"})
_GAME_CONTROLLERS = frozenset({"criador", "chat", "celia"})
_ACTIVE_GAME_SESSIONS: dict[str, dict[str, Any]] = {}
_GAME_SESSION_CANDIDATES: dict[str, dict[str, dict[str, Any]]] = {}
_GAME_AUTHORITY_EPOCHS: dict[str, int] = {}
_CHAT_GAME_MOVES: dict[tuple[str, str], dict[str, str]] = {}
_GAME_CONTROLS: dict[str, dict[str, Any]] = {}
_GAME_RESULT_ACKS: set[tuple[str, str, int, str]] = set()
_GAME_CONTROL_SEQUENCE = 0
_GAME_SESSION_TTL_SECONDS = 8.0
_GAME_CONTROL_TTL_SECONDS = 8.0
_GAME_CONTRACT_VERSION = "2026-09-23.authority.v1"
_GAME_ASSET_VERSION = "game-authority-20260923b"
_GAME_CONTROL_ACTIONS = frozenset({"pause", "resume", "retry_commentary", "skip_commentary", "close", "play"})


def _normalize_result_controller(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if raw in {"human", "humano", "criador"}:
        return "criador"
    if raw in {"celia", "célia"}:
        return "celia"
    if raw in {"draw", "empate", "tie"}:
        return "draw"
    if raw == "chat":
        return "chat"
    return ""


_SESSION_STATE_KEYS = frozenset({
    "game", "match_id", "board", "fen", "current_player", "last_move",
    "legal_actions", "captured", "special", "mode", "difficulty", "history",
    "score", "scores", "result", "winner", "winner_controller", "status", "turn", "game_over",
    "board_revision", "authority_epoch", "instance_id",
})


def _trace_game(event: str, game_id: str, session: dict[str, Any] | None = None, **extra: Any) -> None:
    source = session or {}
    payload = {
        "event": str(event),
        "game_id": str(game_id or ""),
        "match_id": str(extra.pop("match_id", source.get("match_id") or "")),
        "instance_id": str(extra.pop("instance_id", source.get("instance_id") or "")),
        "authority_epoch": int(extra.pop("authority_epoch", source.get("authority_epoch") or 0)),
        "board_revision": int(extra.pop("board_revision", source.get("board_revision") or 0)),
        "controller": str(extra.pop("controller", source.get("current_controller") or "")),
        **extra,
    }
    logger.info("[GAME TRACE] {}", json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _next_authority_epoch(game_id: str) -> int:
    epoch = int(_GAME_AUTHORITY_EPOCHS.get(game_id, 0)) + 1
    _GAME_AUTHORITY_EPOCHS[game_id] = epoch
    return epoch


def _snapshot_error(game_id: str, session: dict[str, Any]) -> str:
    if session.get("ambiguous"):
        return "ambiguous_session"
    state = session.get("state")
    if not isinstance(state, dict) or not state:
        return "missing_state"
    if str(state.get("match_id") or session.get("match_id") or "") != str(session.get("match_id") or ""):
        return "match_id_mismatch"
    if state.get("game") not in (None, "", game_id):
        return "game_id_mismatch"
    state_revision = state.get("board_revision", session.get("board_revision"))
    try:
        if int(state_revision) != int(session.get("board_revision") or 0):
            return "board_revision_mismatch"
    except (TypeError, ValueError):
        return "invalid_board_revision"
    current = str(state.get("current_player") or "")
    if current and current != str(session.get("current_controller") or ""):
        return "controller_mismatch"
    state_legal = state.get("legal_actions")
    if isinstance(state_legal, list):
        if [str(item) for item in state_legal] != [str(item) for item in session.get("legal_actions") or []]:
            return "legal_actions_mismatch"
    if game_id == "tic-tac-toe":
        board = state.get("board")
        if not isinstance(board, list) or len(board) != 9:
            return "invalid_board"
    return ""


def _public_session_status(game_id: str, active: bool, session: dict[str, Any], reason: str = "") -> dict[str, Any]:
    match_id = str(session.get("match_id") or "")
    terminal = _read_terminal_result(game_id, match_id) if match_id else None
    if terminal and not terminal.get("winner_controller"):
        state = dict(session.get("state") or {})
        winner = _normalize_result_controller(
            state.get("winner_controller") or state.get("winner")
        )
        if winner:
            terminal = {**terminal, "winner_controller": winner}
    return {
        "ok": True,
        "active": bool(active),
        "status": "AUTHORITATIVE" if active else "OBSERVER",
        "reason": str(reason or ("authoritative" if active else "observer")),
        "match_id": match_id,
        "instance_id": str(session.get("instance_id") or ""),
        "authority_epoch": int(session.get("authority_epoch") or 0),
        "board_revision": int(session.get("board_revision") or 0),
        "result_status": (
            {
                "match_id": match_id,
                "turn": int(terminal.get("turn") or 0),
                "winner_controller": str(terminal.get("winner_controller") or ""),
                "result": str(terminal.get("result") or ""),
                "reason": terminal.get("reason"),
                "score": dict(terminal.get("score") or {}),
                "board_revision": int(terminal.get("board_revision") or 0),
                "acknowledged": bool(terminal.get("acknowledged")),
                "pending": not bool(terminal.get("acknowledged")),
            }
            if terminal
            else None
        ),
    }


def _normalize_chat_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _resolve_chat_action(game_id: str, message: str, legal_actions: list[Any]) -> str | None:
    legal = [str(action) for action in legal_actions]
    if not legal:
        return None
    text = _normalize_chat_text(message)
    if game_id == "tic-tac-toe":
        match = re.search(r"(?<!\d)([1-9])(?!\d)", text)
        if match:
            candidate = str(int(match.group(1)) - 1)
            if candidate in legal:
                return candidate
    for candidate in sorted(legal, key=len, reverse=True):
        if candidate.lower() == text or candidate.lower() in text:
            return candidate
    return None


def submit_chat_game_message(author: str, message: str) -> dict[str, str] | None:
    """Route one YouTube chat message into the currently waiting game turn.

    First valid message wins the turn. Non-game chat is left untouched so the
    LiveChatWatcher can continue its normal conversation path.
    """
    now = time.monotonic()
    candidates = []
    for game_id, session in list(_ACTIVE_GAME_SESSIONS.items()):
        if now - float(session.get("updated_at", 0.0)) > _GAME_SESSION_TTL_SECONDS:
            _ACTIVE_GAME_SESSIONS.pop(game_id, None)
            continue
        if session.get("current_controller") != "chat":
            continue
        candidates.append((
            _session_rank(session),
            float(session.get("updated_at", 0.0)),
            game_id,
            session,
        ))
    if not candidates:
        return None
    _, _, game_id, session = max(candidates, key=lambda item: (item[0], item[1]))
    key = (game_id, str(session["match_id"]))
    if key in _CHAT_GAME_MOVES:
        return None
    action = _resolve_chat_action(game_id, message, session.get("legal_actions", []))
    if action is None:
        return None
    move = {"action": action, "author": str(author or "Chat")[:80], "message": str(message or "")[:200]}
    _CHAT_GAME_MOVES[key] = move
    return {"game": game_id, "match_id": key[1], **move}


def _event_error(message: str) -> None:
    raise HTTPException(status_code=422, detail=f"invalid game event: {message}")


def _previous_event(path: Path) -> dict[str, Any] | None:
    """Read one prior JSONL record without loading an entire long match."""
    if not path.exists():
        return None
    with path.open("rb") as handle:
        lines = handle.read().splitlines()
    if not lines:
        return None
    try:
        record = json.loads(lines[-1])
    except json.JSONDecodeError as error:
        raise HTTPException(status_code=409, detail="existing game log has invalid JSON") from error
    if not isinstance(record, dict):
        raise HTTPException(status_code=409, detail="existing game log has invalid event")
    return record


def _result_state_path(game_id: str, match_id: str) -> Path:
    return _LOG_DIR / f"{game_id}-{match_id}.result.json"


def _terminal_result_from_event(
    game_id: str,
    match_id: str,
    event: dict[str, Any],
    *,
    acknowledged: bool = False,
) -> dict[str, Any] | None:
    result = event.get("result")
    if result in (None, ""):
        return None
    normalized_result = _normalize_result_controller(result)
    winner = _normalize_result_controller(
        event.get("winner_controller")
        or event.get("winner")
        or normalized_result
    )
    persisted_result = normalized_result or str(result)
    return {
        "game_id": game_id,
        "match_id": match_id,
        "turn": int(event.get("turn") or 0),
        "winner_controller": winner,
        "result": persisted_result,
        "reason": event.get("reason", event.get("special")),
        "score": dict(event.get("score") or {}),
        "board_revision": int(event.get("board_revision_after") or event.get("board_revision") or 0),
        "instance_id": str(event.get("instance_id") or ""),
        "acknowledged": bool(acknowledged),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def _persist_terminal_result(game_id: str, match_id: str, record: dict[str, Any]) -> None:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _result_state_path(game_id, match_id)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def _read_terminal_result(game_id: str, match_id: str) -> dict[str, Any] | None:
    path = _result_state_path(game_id, match_id)
    if not path.exists():
        # Do not reinterpret historical logs as pending results. Matches
        # recorded before this recovery contract may already have advanced.
        return None
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return record if isinstance(record, dict) else None


def _mark_terminal_result_acknowledged(
    game_id: str,
    match_id: str,
    payload: GameResultAck,
    winner: str,
) -> dict[str, Any]:
    record = _read_terminal_result(game_id, match_id) or {
        "game_id": game_id,
        "match_id": match_id,
        "turn": int(payload.turn),
        "winner_controller": winner,
        "result": str(payload.result),
        "reason": payload.reason,
        "score": dict(payload.score or {}),
        "board_revision": int(payload.board_revision or 0),
        "instance_id": str(payload.instance_id or ""),
    }
    record.update({
        "turn": int(payload.turn),
        "winner_controller": winner,
        "result": str(payload.result),
        "reason": payload.reason,
        "score": dict(payload.score or {}),
        "board_revision": int(payload.board_revision or record.get("board_revision") or 0),
        "instance_id": str(payload.instance_id or record.get("instance_id") or ""),
        "acknowledged": True,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    })
    _persist_terminal_result(game_id, match_id, record)
    return record


def _validate_v2_event(match_id: str, event: dict[str, Any], previous: dict[str, Any] | None) -> None:
    missing = sorted(_EVENT_FIELDS.difference(event))
    if missing:
        _event_error("missing " + ", ".join(missing))
    if event["log_schema"] != _EVENT_SCHEMA_VERSION:
        _event_error(f"unsupported log_schema {event['log_schema']!r}")
    if event["match_id"] != match_id:
        _event_error("event match_id differs from request match_id")
    if not isinstance(event["turn"], int) or event["turn"] < 1:
        _event_error("turn must be a positive integer")
    if event["actor"] not in _ACTORS:
        _event_error("actor must be human or celia")
    if not isinstance(event["board_before"], list) or not isinstance(event["board_after"], list):
        _event_error("board_before and board_after must be lists")
    if not isinstance(event["valid_move"], bool):
        _event_error("valid_move must be boolean")
    if not isinstance(event["legal_actions"], list):
        _event_error("legal_actions must be a list")
    if not isinstance(event["captured"], (list, dict)):
        _event_error("captured must be a list or object")
    if not isinstance(event["commentary"], str) or len(event["commentary"]) > 500:
        _event_error("commentary must be a string up to 500 characters")
    if event["result"] is not None and not isinstance(event["result"], str):
        _event_error("result must be a string or null")
    # `special` e metadado descritivo do lance, nao fronteira de seguranca.
    # Medido em 2026-08-24 sobre os 191 logs ja gravados: lista 308x, string
    # 249x, null 152x. A lista SEMPRE foi a forma dominante — o jogo da velha
    # manda `['threat:celia:4', ...]` em `specialState()`. A validacao apertada
    # em `f077eee` so aceitou string/null e passou a devolver 422; o navegador
    # estourava `log_http_422` e o turno morria antes de a Celia jogar. Era esta
    # a causa dos testes de jogo em TimeoutException, e NAO a latencia.
    #
    # O que continua sendo recusado, igual a antes: dicionario, numero, booleano
    # e qualquer item de lista que nao seja texto curto. Nada foi afrouxado alem
    # da forma que os proprios jogos sempre emitiram.
    if event["special"] is not None:
        especial = event["special"]
        if isinstance(especial, str):
            if len(especial) > 200:
                _event_error("special string too long")
        elif isinstance(especial, list):
            if len(especial) > 64:
                _event_error("special list too long")
            if not all(isinstance(item, str) and len(item) <= 200 for item in especial):
                _event_error("special list must contain short strings")
        else:
            _event_error("special must be a string, a list of strings, or null")

    requested = event["requested_move"]
    if event["valid_move"]:
        if not isinstance(requested, (str, int)) or not str(requested):
            _event_error("valid move requires requested_move")
        if str(requested) not in {str(action) for action in event["legal_actions"]}:
            _event_error("valid requested_move is absent from legal_actions")
    elif event["board_before"] != event["board_after"]:
        _event_error("invalid move changed board state")

    if previous is None:
        return
    if previous.get("result") not in (None, ""):
        raise HTTPException(
            status_code=409,
            detail={
                "code": "game_already_ended",
                "match_id": match_id,
                "terminal_turn": previous.get("turn"),
                "result": previous.get("result"),
            },
        )
    previous_turn = previous.get("turn")
    if not isinstance(previous_turn, int) or event["turn"] != previous_turn + 1:
        _event_error("turn does not continue the existing match")
    if previous.get("board_after") != event["board_before"]:
        _event_error("board_before does not continue the existing match")
    if "fen_after" in previous or "fen_before" in event:
        if previous.get("fen_after") != event.get("fen_before"):
            _event_error("fen_before does not continue the existing match")


def write_game_log(game_id: str, match_id: str, event: dict[str, Any]) -> Path:
    """Validate and append one reproducible game event to its JSONL match log."""
    if not _GAME_ID.fullmatch(game_id) or not _MATCH_ID.fullmatch(match_id):
        raise HTTPException(status_code=400, detail="invalid game or match id")
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = _LOG_DIR / f"{game_id}-{match_id}.jsonl"
    if event.get("log_schema") == _EVENT_SCHEMA_VERSION:
        _validate_v2_event(match_id, event, _previous_event(path))
    record = {"timestamp": datetime.now(timezone.utc).isoformat(), **event}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    terminal = _terminal_result_from_event(game_id, match_id, event)
    if terminal is not None:
        _persist_terminal_result(game_id, match_id, terminal)
    return path


def _bounded_session_state(state: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    bounded: dict[str, Any] = {}
    for key in _SESSION_STATE_KEYS:
        if key not in state:
            continue
        value = state[key]
        if key == "history" and isinstance(value, list):
            value = value[-8:]
        bounded[key] = value
    try:
        encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return {}
    if len(encoded) <= 12000:
        return bounded
    # Board/FEN/current turn are the minimum useful live context. History and
    # captured data are the first things to drop if a game snapshot grows.
    for optional in ("history", "captured", "scores", "score", "board"):
        bounded.pop(optional, None)
        encoded = json.dumps(bounded, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) <= 12000:
            break
    return bounded


def _session_rank(session: dict[str, Any]) -> int:
    visible = bool(session.get("visible", True))
    focused = bool(session.get("focused", False))
    if visible and focused:
        return 2
    if visible:
        return 1
    return 0


def _has_equal_live_candidate(game_id: str, session: dict[str, Any], now: float) -> bool:
    instance_id = str(session.get("instance_id") or "")
    rank = _session_rank(session)
    if rank <= 0:
        return False
    for candidate in _GAME_SESSION_CANDIDATES.get(game_id, {}).values():
        if str(candidate.get("instance_id") or "") == instance_id:
            continue
        if now - float(candidate.get("updated_at", 0.0)) > _GAME_SESSION_TTL_SECONDS:
            continue
        if _session_rank(candidate) == rank:
            return True
    return False


def update_active_game_session(game_id: str, payload: GameSessionUpdate) -> bool:
    """Register one browser tab and return whether it is authoritative.

    Multiple tabs for the same game used to overwrite each other every heartbeat.
    Focused/visible tabs now win; equal-priority tabs keep the existing lease until
    it expires, preventing match_id thrashing during recording.
    """
    now = time.monotonic()
    incoming = {
        "match_id": payload.match_id,
        "mode": payload.mode,
        "current_controller": payload.current_controller,
        "legal_actions": [str(action) for action in payload.legal_actions],
        "state": _bounded_session_state(payload.state),
        "visible": bool(payload.visible),
        "focused": bool(payload.focused),
        "instance_id": str(payload.instance_id or ""),
        "board_revision": max(0, int(payload.board_revision or 0)),
        "updated_at": now,
    }
    incoming["state"].setdefault("match_id", payload.match_id)
    incoming["state"].setdefault("board_revision", incoming["board_revision"])
    incoming["state"].setdefault("instance_id", incoming["instance_id"])
    terminal_state = (
        bool(incoming["state"].get("game_over"))
        or incoming["state"].get("result") not in (None, "")
    )
    if terminal_state:
        incoming["legal_actions"] = []
        incoming["state"]["legal_actions"] = []
    candidate_key = incoming["instance_id"] or f"legacy:{payload.match_id}"
    _GAME_SESSION_CANDIDATES.setdefault(game_id, {})[candidate_key] = dict(incoming)

    existing = _ACTIVE_GAME_SESSIONS.get(game_id)
    if not existing:
        _GAME_SESSION_CANDIDATES[game_id] = {candidate_key: dict(incoming)}
        epoch = _next_authority_epoch(game_id)
        incoming["authority_epoch"] = epoch
        incoming["state"]["authority_epoch"] = epoch
        incoming["ambiguous"] = False
        _ACTIVE_GAME_SESSIONS[game_id] = incoming
        _trace_game("GAME_SESSION_CLAIMED", game_id, incoming, reason="no_current_authority")
        return True

    age = now - float(existing.get("updated_at", 0.0))
    same_instance = bool(
        incoming["instance_id"]
        and existing.get("instance_id")
        and str(existing.get("instance_id")) == incoming["instance_id"]
    )
    same_match_legacy = (
        str(existing.get("match_id") or "") == payload.match_id
        and (not incoming["instance_id"] or not existing.get("instance_id"))
    )
    same_source = same_instance or same_match_legacy
    if age > _GAME_SESSION_TTL_SECONDS:
        epoch = _next_authority_epoch(game_id)
        incoming["authority_epoch"] = epoch
        incoming["state"]["authority_epoch"] = epoch
        _ACTIVE_GAME_SESSIONS[game_id] = incoming
        _trace_game("GAME_SESSION_CLAIMED", game_id, incoming, reason="expired_authority")
        return True

    if same_source:
        current_epoch = int(existing.get("authority_epoch") or _GAME_AUTHORITY_EPOCHS.get(game_id, 0) or 1)
        _GAME_AUTHORITY_EPOCHS[game_id] = max(_GAME_AUTHORITY_EPOCHS.get(game_id, 0), current_epoch)
        supplied_epoch = int(payload.authority_epoch or 0)
        if supplied_epoch and supplied_epoch != current_epoch:
            _trace_game("GAME_STATE_REJECTED", game_id, existing, reason="stale_authority_epoch", supplied_epoch=supplied_epoch)
            return False
        old_match = str(existing.get("match_id") or "")
        old_revision = int(existing.get("board_revision") or 0)
        if old_match == payload.match_id and incoming["board_revision"] < old_revision:
            _trace_game("GAME_STATE_REJECTED", game_id, existing, reason="revision_regression", supplied_revision=incoming["board_revision"])
            return True
        if old_match != payload.match_id:
            current_epoch = _next_authority_epoch(game_id)
        incoming["authority_epoch"] = current_epoch
        incoming["state"]["authority_epoch"] = current_epoch
        incoming["ambiguous"] = False
        _ACTIVE_GAME_SESSIONS[game_id] = incoming
        error = _snapshot_error(game_id, incoming)
        _trace_game("GAME_STATE_REJECTED" if error else "GAME_STATE_ACCEPTED", game_id, incoming, reason=error or "heartbeat")
        return True

    # A higher-priority tab may take over (for example focused beats
    # background), but another equally focused tab cannot steal a live lease.
    # A restarted match in the SAME browser instance is handled above.
    if payload.request_takeover or _session_rank(incoming) > _session_rank(existing):
        epoch = _next_authority_epoch(game_id)
        incoming["authority_epoch"] = epoch
        incoming["state"]["authority_epoch"] = epoch
        _ACTIVE_GAME_SESSIONS[game_id] = incoming
        _trace_game(
            "GAME_SESSION_CLAIMED", game_id, incoming,
            reason="explicit_takeover" if payload.request_takeover else "higher_priority_instance",
            previous_match_id=str(existing.get("match_id") or ""),
            previous_instance_id=str(existing.get("instance_id") or ""),
        )
        return True

    if _session_rank(incoming) == _session_rank(existing) and _session_rank(incoming) > 0:
        # Equal-priority tabs are observers, not a reason to invalidate the
        # already-established authority. Commands carry instance_id + epoch +
        # revision, so only the lease owner can apply them. Marking the owner
        # ambiguous here made an old visible game window block "Célia, joga".
        existing = dict(existing)
        existing["ambiguous"] = False
        _ACTIVE_GAME_SESSIONS[game_id] = existing
        _trace_game(
            "GAME_SESSION_COMPETING_OBSERVER", game_id, existing,
            reason="equal_priority_authority_retained",
            competing_instance_id=str(incoming.get("instance_id") or ""),
            competing_match_id=str(incoming.get("match_id") or ""),
        )
    observer = dict(incoming)
    observer["authority_epoch"] = int(existing.get("authority_epoch") or 0)
    _trace_game(
        "GAME_SESSION_OBSERVER", game_id, observer,
        reason="existing_authority_retained",
        authoritative_match_id=str(existing.get("match_id") or ""),
        authoritative_instance_id=str(existing.get("instance_id") or ""),
    )
    return False


def release_active_game_session(game_id: str, payload: GameSessionRelease) -> bool:
    """Release only the exact browser instance that currently owns this game."""
    existing = _ACTIVE_GAME_SESSIONS.get(game_id)
    if not existing:
        return False
    if (
        str(existing.get("match_id") or "") != str(payload.match_id or "")
        or str(existing.get("instance_id") or "") != str(payload.instance_id or "")
    ):
        return False
    _ACTIVE_GAME_SESSIONS.pop(game_id, None)
    candidates = _GAME_SESSION_CANDIDATES.get(game_id)
    if candidates:
        candidates.pop(str(payload.instance_id or ""), None)
        if not candidates:
            _GAME_SESSION_CANDIDATES.pop(game_id, None)
    _trace_game("GAME_SESSION_RELEASED", game_id, existing, reason="page_release")
    _CHAT_GAME_MOVES.pop((game_id, str(payload.match_id)), None)
    command = _GAME_CONTROLS.get(game_id)
    if command and str(command.get("match_id") or "") == str(payload.match_id):
        _GAME_CONTROLS.pop(game_id, None)
    return True


def _latest_game_event(game_id: str, match_id: str) -> dict[str, Any] | None:
    """Return the latest persisted event for the authoritative match."""
    if not game_id or not match_id:
        return None
    return _previous_event(_LOG_DIR / f"{game_id}-{match_id}.jsonl")


def active_game_context(
    game_id: str | None = None,
    *,
    level: str = "tactical",
) -> str:
    """Trusted live-game context sized to the current conversational intent."""
    level = str(level or "tactical").strip().lower()
    if level == "none":
        return ""
    if level not in {"tactical", "scene", "narrative"}:
        level = "tactical"
    active = active_game_session(game_id)
    if active is None:
        return ""
    resolved_game, session = active
    state = dict(session.get("state") or {})
    snapshot_issue = _snapshot_error(resolved_game, session)
    if snapshot_issue:
        _trace_game("GAME_CONTEXT_UNSYNCED", resolved_game, session, reason=snapshot_issue)
        return (
            "[JOGO ATIVO DESINCRONIZADO — NÃO INVENTE ESTADO]\n"
            f"Jogo: {resolved_game}. Partida: {session.get('match_id')}. "
            f"Instância: {session.get('instance_id') or 'desconhecida'}. "
            f"Revisão: {session.get('board_revision', 0)}. "
            f"Motivo: {snapshot_issue}. Aguarde uma atualização válida do tabuleiro."
        )[:3500]
    _trace_game("LLM_GAME_CONTEXT", resolved_game, session, reason="validated_snapshot")
    game_name = {
        "tic-tac-toe": "jogo da velha",
        "checkers": "damas",
        "chess": "xadrez",
    }.get(resolved_game, resolved_game)
    current = str(state.get("current_player") or session.get("current_controller") or "")
    legal = state.get("legal_actions")
    if not isinstance(legal, list):
        legal = session.get("legal_actions") or []
    match_id = str(session.get("match_id") or "")
    latest_event = _latest_game_event(resolved_game, match_id)
    details = [
        "[ESTADO REAL DO JOGO ATIVO — fonte local estruturada]",
        f"Jogo: {game_name}.",
        f"Partida: {match_id}.",
        f"Revisão do tabuleiro: {int(session.get('board_revision') or 0)}.",
        f"Vez/controlador atual: {current or 'desconhecido'}.",
    ]
    if level == "tactical":
        details.insert(3, f"Instância oficial: {session.get('instance_id') or 'desconhecida'}.")
        details.insert(4, f"Epoch de autoridade: {int(session.get('authority_epoch') or 0)}.")

    if latest_event:
        raw_actor = str(latest_event.get("action_author") or latest_event.get("actor") or "")
        if raw_actor == "celia":
            action_author = "Célia"
        elif raw_actor in {"human", "criador"}:
            action_author = (
                "Criador"
                if str(session.get("mode") or "") == "criador-vs-celia"
                else "humano/chat"
            )
        else:
            action_author = raw_actor or "desconhecido"
        executor = str(latest_event.get("action_executor") or "")
        requested = latest_event.get("requested_move")
        if requested not in (None, ""):
            details.append(f"Última ação confirmada: {action_author} jogou {requested}.")
        if executor:
            details.append(f"Executor técnico da última ação: {executor}.")
        next_player = latest_event.get("next_player") or current
        if next_player:
            details.append(f"Próximo jogador/controlador: {next_player}.")
    elif state.get("last_move") not in (None, ""):
        details.append(
            f"Última jogada registrada sem autoria explícita (legado): {state['last_move']}."
        )

    if level == "tactical" and resolved_game == "chess" and state.get("fen"):
        details.append(f"FEN: {state['fen']}.")
    elif level == "tactical" and state.get("board") is not None:
        details.append(
            "Tabuleiro: "
            + json.dumps(state["board"], ensure_ascii=False, separators=(",", ":"))
            + "."
        )
    if level == "tactical" and legal:
        details.append(
            "Ações legais agora: "
            + json.dumps([str(action) for action in legal], ensure_ascii=False)
            + "."
        )
    if state.get("special") not in (None, "", [], {}):
        details.append(f"Situação especial: {state['special']}.")
    if state.get("game_over") or state.get("result") not in (None, ""):
        details.append("Partida encerrada: sim.")
    if state.get("result") not in (None, ""):
        details.append(f"Resultado: {state['result']}.")
    winner = state.get("winner_controller") or state.get("winner")
    if winner not in (None, ""):
        details.append(f"Vencedor/controlador: {winner}.")
    if level == "tactical":
        details.append(
            "Use estes dados somente para responder à pergunta atual sobre a partida. "
            "Não deixe o tabuleiro substituir o assunto pedido por Criador."
        )
    elif level == "scene":
        details.append(
            "Isto é apenas contexto curto da cena. Priorize a fala atual de Criador; "
            "não transforme toda resposta em comentário tático."
        )
    else:
        details.append(
            "Isto é memória narrativa da cena, não uma ordem para continuar falando do jogo."
        )
    return "\n".join(details)[:3500]


def active_game_session(game_id: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """Return the freshest live browser game session."""
    now = time.monotonic()
    candidates: list[tuple[int, float, str, dict[str, Any]]] = []
    for candidate_id, session in list(_ACTIVE_GAME_SESSIONS.items()):
        age = now - float(session.get("updated_at", 0.0))
        if age > _GAME_SESSION_TTL_SECONDS:
            _ACTIVE_GAME_SESSIONS.pop(candidate_id, None)
            continue
        if game_id and candidate_id != game_id:
            continue
        # Generic game context/play commands must never be hijacked by an old
        # pre-authority browser tab. Those legacy clients have no instance_id
        # and can keep heartbeating an invalid snapshot forever. Modern
        # ambiguous/unsynced sessions remain visible so their diagnostics keep
        # working; explicit per-game lookup can still inspect the legacy tab.
        if game_id is None and not str(session.get("instance_id") or "").strip():
            snapshot_issue = _snapshot_error(candidate_id, session)
            if snapshot_issue:
                _trace_game(
                    "GAME_SESSION_IGNORED",
                    candidate_id,
                    session,
                    reason=snapshot_issue,
                    scope="legacy_generic_active_selection",
                )
                continue
        candidates.append((
            _session_rank(session),
            float(session.get("updated_at", 0.0)),
            candidate_id,
            session,
        ))
    if not candidates:
        return None
    _, _, resolved_id, session = max(candidates, key=lambda item: (item[0], item[1]))
    return resolved_id, dict(session)


_GAME_CELL_WORDS = {
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "três": 3,
    "quatro": 4, "cinco": 5, "seis": 6, "sete": 7, "oito": 8, "nove": 9,
}


def parse_explicit_game_play_command(text: str) -> dict[str, Any] | None:
    """Parse an explicit owner order for Célia without stealing Criador's own move."""
    raw = re.sub(r"\s+", " ", str(text or "").strip().lower())
    if not raw:
        return None
    folded = raw.replace("célia", "celia")
    if re.search(r"\b(?:eu\s+vou|vou|irei)\s+(?:jogar|marcar)\b", folded):
        return None
    if re.search(r"\bn[aã]o\s+(?:joga|jogue)\b", folded):
        return None
    explicit = bool(
        re.fullmatch(r"\s*celia\s*[,!:\-]?\s*(?:joga|jogue)(?:\s+.*)?[.!?]*\s*", folded)
        or re.fullmatch(r"\s*(?:joga|jogue)\s*[,!:\-]?\s*celia(?:\s+.*)?[.!?]*\s*", folded)
        or re.fullmatch(r"\s*(?:e|é)\s+(?:a\s+)?tua\s+vez\s*[,!:\-]?\s*(?:joga|jogue)[.!?]*\s*", folded)
        or re.fullmatch(r"\s*(?:pode\s+jogar|faz\s+(?:a\s+)?tua\s+jogada|fa[cç]a\s+(?:a\s+)?sua\s+jogada)[.!?]*\s*", folded)
    )
    if not explicit:
        return None
    position = None
    digit = re.search(r"(?<!\d)([1-9])(?!\d)", folded)
    if digit:
        position = int(digit.group(1))
    else:
        for word, value in _GAME_CELL_WORDS.items():
            if re.search(rf"\b{re.escape(word)}\b", folded):
                position = value
                break
    return {
        "actor": "celia",
        "action": "play",
        "requested_position": position,
        "requested_action": None if position is None else str(position - 1),
    }


def is_explicit_game_play_command(text: str) -> bool:
    return parse_explicit_game_play_command(text) is not None


def request_explicit_game_play(text: str) -> dict[str, Any] | None:
    """Resolve an explicit play command against the authoritative live session."""
    intent = parse_explicit_game_play_command(text)
    if intent is None:
        return None
    active = active_game_session()
    if active is None:
        return {
            "status": "no_session",
            "reply": "Não tem nenhuma partida ativa conectada agora.",
        }
    game_id, session = active
    state = dict(session.get("state") or {})
    match_id = str(session.get("match_id") or "")
    snapshot_issue = _snapshot_error(game_id, session)
    if snapshot_issue:
        _trace_game("GAME_COMMAND_REJECTED", game_id, session, reason=snapshot_issue)
        return {
            "status": "unsynced", "game": game_id, "match_id": match_id,
            "reply": "Perdi a sincronização dessa partida. Espera o tabuleiro atualizar.",
        }
    current = str(state.get("current_player") or session.get("current_controller") or "")
    legal = state.get("legal_actions")
    if not isinstance(legal, list):
        legal = session.get("legal_actions") or []
    if state.get("game_over") or state.get("result") not in (None, ""):
        result = str(state.get("result") or "encerrada")
        return {
            "status": "ended", "game": game_id, "match_id": match_id,
            "reply": f"Essa partida já acabou. Resultado: {result}.",
        }
    if current != "celia":
        reply = (
            "Agora é a tua vez."
            if current == "criador"
            else f"Agora é a vez de {current or 'outro jogador'}."
        )
        return {
            "status": "not_celia_turn", "game": game_id, "match_id": match_id,
            "current_controller": current,
            "reply": reply,
        }
    requested_action = intent.get("requested_action")
    requested_position = intent.get("requested_position")
    if requested_action is not None and str(requested_action) not in {str(item) for item in legal}:
        board = state.get("board")
        occupied = bool(
            game_id == "tic-tac-toe"
            and isinstance(board, list)
            and 0 <= int(requested_action) < len(board)
            and board[int(requested_action)] not in (None, "")
        )
        _trace_game(
            "GAME_COMMAND_REJECTED", game_id, session,
            reason="cell_occupied" if occupied else "illegal_action",
            requested_action=str(requested_action),
        )
        return {
            "status": "cell_occupied" if occupied else "illegal_action",
            "game": game_id, "match_id": match_id,
            "requested_position": requested_position,
            "reply": (
                f"A casa {requested_position} já está ocupada."
                if occupied else f"Não posso jogar na casa {requested_position} agora."
            ),
        }
    if not legal:
        return {
            "status": "no_legal_actions", "game": game_id, "match_id": match_id,
            "reply": "Não tenho nenhuma jogada legal disponível agora.",
        }
    command = issue_game_control(
        "play", game_id=game_id, match_id=match_id,
        requested_action=requested_action,
    )
    if command is None:
        return {
            "status": "no_session", "game": game_id, "match_id": match_id,
            "reply": "A partida deixou de estar disponível antes da jogada.",
        }
    return {
        "status": "queued", "game": game_id, "match_id": match_id,
        "command": command,
    }


def issue_game_control(
    action: str,
    *,
    game_id: str | None = None,
    match_id: str | None = None,
    requested_action: str | int | None = None,
) -> dict[str, Any] | None:
    """Publish one bounded command tied to the currently active match."""
    global _GAME_CONTROL_SEQUENCE
    action = str(action or "").strip().lower()
    if action not in _GAME_CONTROL_ACTIONS:
        raise ValueError("invalid game control action")
    active = active_game_session(game_id)
    if active is None:
        return None
    resolved_game, session = active
    active_match = str(session.get("match_id") or "")
    if match_id and active_match != str(match_id):
        return None
    if action == "play":
        state = dict(session.get("state") or {})
        snapshot_issue = _snapshot_error(resolved_game, session)
        if snapshot_issue:
            _trace_game("GAME_COMMAND_REJECTED", resolved_game, session, reason=snapshot_issue)
            return None
        current = str(state.get("current_player") or session.get("current_controller") or "")
        legal = state.get("legal_actions")
        if not isinstance(legal, list):
            legal = session.get("legal_actions") or []
        if state.get("game_over") or state.get("result") not in (None, ""):
            return None
        if current != "celia" or not legal:
            return None
        if requested_action is not None and str(requested_action) not in {str(item) for item in legal}:
            _trace_game(
                "GAME_COMMAND_REJECTED", resolved_game, session,
                reason="requested_action_not_legal", requested_action=str(requested_action),
            )
            return None
    _GAME_CONTROL_SEQUENCE += 1
    command = {
        "sequence": _GAME_CONTROL_SEQUENCE,
        "action_id": str(uuid.uuid4()),
        "action": action,
        "game": resolved_game,
        "match_id": active_match,
        "instance_id": str(session.get("instance_id") or ""),
        "authority_epoch": int(session.get("authority_epoch") or 0),
        "board_revision": int(session.get("board_revision") or 0),
        "requested_action": None if requested_action is None else str(requested_action),
        "issued_at": time.monotonic(),
    }
    _GAME_CONTROLS[resolved_game] = command
    _trace_game(
        "GAME_COMMAND_ROUTED", resolved_game, session,
        action_id=command["action_id"], action=action,
        requested_action=command["requested_action"],
    )
    return dict(command)


def init_games_routes() -> APIRouter:
    router = APIRouter(prefix="/games/api", tags=["games"])
    logger.info("[GAME BOOT] contract={} assets={}", _GAME_CONTRACT_VERSION, _GAME_ASSET_VERSION)

    @router.get("/version")
    async def game_contract_version() -> dict[str, str]:
        return {"contract": _GAME_CONTRACT_VERSION, "assets": _GAME_ASSET_VERSION}

    @router.post("/{game_id}/session")
    async def update_game_session(game_id: str, payload: GameSessionUpdate) -> dict[str, Any]:
        if not _GAME_ID.fullmatch(game_id) or not _MATCH_ID.fullmatch(payload.match_id):
            raise HTTPException(status_code=400, detail="invalid game or match id")
        if payload.mode not in _GAME_MODES:
            raise HTTPException(status_code=422, detail="invalid game mode")
        if payload.current_controller not in _GAME_CONTROLLERS:
            raise HTTPException(status_code=422, detail="invalid game controller")
        active = update_active_game_session(game_id, payload)
        authoritative = _ACTIVE_GAME_SESSIONS.get(game_id) or {}
        if active:
            source = authoritative
            reason = "authoritative"
        else:
            source = {
                "match_id": payload.match_id,
                "instance_id": str(payload.instance_id or ""),
                "authority_epoch": int(authoritative.get("authority_epoch") or 0),
                "board_revision": max(0, int(payload.board_revision or 0)),
            }
            reason = "observer"
        return _public_session_status(game_id, active, source, reason)

    @router.post("/{game_id}/session/release")
    async def release_game_session(
        game_id: str, payload: GameSessionRelease
    ) -> dict[str, bool]:
        if (
            not _GAME_ID.fullmatch(game_id)
            or not _MATCH_ID.fullmatch(payload.match_id)
            or not payload.instance_id.strip()
        ):
            raise HTTPException(status_code=400, detail="invalid game session release")
        return {
            "ok": True,
            "released": release_active_game_session(game_id, payload),
        }

    @router.get("/{game_id}/chat/next")
    async def next_chat_game_move(game_id: str, match_id: str) -> dict[str, Any]:
        if not _GAME_ID.fullmatch(game_id) or not _MATCH_ID.fullmatch(match_id):
            raise HTTPException(status_code=400, detail="invalid game or match id")
        move = _CHAT_GAME_MOVES.pop((game_id, match_id), None)
        return {"ok": True, "move": move}

    @router.get("/{game_id}/control")
    async def next_game_control(
        game_id: str, match_id: str, after: int = 0
    ) -> dict[str, Any]:
        if not _GAME_ID.fullmatch(game_id) or not _MATCH_ID.fullmatch(match_id):
            raise HTTPException(status_code=400, detail="invalid game or match id")
        command = _GAME_CONTROLS.get(game_id)
        if not command:
            return {"ok": True, "command": None}
        if command.get("match_id") != match_id:
            return {"ok": True, "command": None}
        if int(command.get("sequence") or 0) <= max(0, int(after)):
            return {"ok": True, "command": None}
        if time.monotonic() - float(command.get("issued_at") or 0.0) > _GAME_CONTROL_TTL_SECONDS:
            return {"ok": True, "command": None}
        public = {
            key: value
            for key, value in command.items()
            if key != "issued_at"
        }
        return {"ok": True, "command": public}

    @router.post("/{game_id}/control")
    async def send_game_control(
        game_id: str, payload: GameControlRequest
    ) -> dict[str, Any]:
        if not _GAME_ID.fullmatch(game_id):
            raise HTTPException(status_code=400, detail="invalid game id")
        try:
            command = issue_game_control(
                payload.action, game_id=game_id, match_id=payload.match_id,
                requested_action=payload.requested_action,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid game control") from exc
        if command is None:
            raise HTTPException(status_code=409, detail="game session is stale or unavailable")
        public = {key: value for key, value in command.items() if key != "issued_at"}
        return {"ok": True, "command": public}

    @router.post("/control-active")
    async def send_active_game_control(payload: GameControlRequest) -> dict[str, Any]:
        try:
            command = issue_game_control(
                payload.action, match_id=payload.match_id,
                requested_action=payload.requested_action,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="invalid game control") from exc
        if command is None:
            raise HTTPException(status_code=409, detail="game session is stale or unavailable")
        public = {key: value for key, value in command.items() if key != "issued_at"}
        return {"ok": True, "command": public}

    @router.post("/{game_id}/result-ack")
    async def acknowledge_game_result(
        game_id: str, payload: GameResultAck
    ) -> dict[str, bool]:
        if not _GAME_ID.fullmatch(game_id) or not _MATCH_ID.fullmatch(payload.match_id):
            raise HTTPException(status_code=400, detail="invalid game or match id")
        winner = _normalize_result_controller(payload.winner_controller)
        if winner not in {*_GAME_CONTROLLERS, "draw"}:
            raise HTTPException(status_code=422, detail="invalid result controller")
        terminal = _read_terminal_result(game_id, payload.match_id)
        if terminal is not None:
            if bool(terminal.get("acknowledged")):
                return {"ok": True, "duplicate": True}
            expected_turn = int(terminal.get("turn") or 0)
            if expected_turn and expected_turn != int(payload.turn):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "stale_game_result_turn",
                        "field": "turn",
                        "expected": expected_turn,
                        "received": int(payload.turn),
                    },
                )
            expected_winner = str(terminal.get("winner_controller") or "").strip().lower()
            if expected_winner and expected_winner != winner:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "stale_game_result_winner",
                        "field": "winner_controller",
                        "expected": expected_winner,
                        "received": winner,
                    },
                )
            expected_result = str(terminal.get("result") or "")
            if expected_result and expected_result != str(payload.result):
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "stale_game_result_value",
                        "field": "result",
                        "expected": expected_result,
                        "received": str(payload.result),
                    },
                )
        active = active_game_session(game_id)
        if active is not None:
            session = active[1]
            if str(session.get("match_id") or "") != payload.match_id:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "code": "stale_game_match",
                        "field": "match_id",
                        "expected": str(session.get("match_id") or ""),
                        "received": payload.match_id,
                    },
                )
            if payload.instance_id and str(session.get("instance_id") or "") != str(payload.instance_id):
                raise HTTPException(status_code=409, detail={"code": "stale_game_instance", "field": "instance_id"})
            if payload.authority_epoch is not None and int(session.get("authority_epoch") or 0) != int(payload.authority_epoch):
                raise HTTPException(status_code=409, detail={"code": "stale_game_authority", "field": "authority_epoch", "expected": int(session.get("authority_epoch") or 0), "received": int(payload.authority_epoch)})
            if payload.board_revision is not None and int(session.get("board_revision") or 0) != int(payload.board_revision):
                raise HTTPException(status_code=409, detail={"code": "stale_game_revision", "field": "board_revision", "expected": int(session.get("board_revision") or 0), "received": int(payload.board_revision)})
        ack_key = (game_id, payload.match_id, int(payload.turn), winner)
        if ack_key in _GAME_RESULT_ACKS:
            _mark_terminal_result_acknowledged(game_id, payload.match_id, payload, winner)
            return {"ok": True, "duplicate": True}
        await roteiro_live.observe_message(
            {
                "type": "game.result",
                "game": game_id,
                "match_id": payload.match_id,
                "turn": payload.turn,
                "winner": winner,
                "winner_controller": winner,
                "result": payload.result,
                "reason": payload.reason,
                "score": payload.score or {},
            }
        )
        _GAME_RESULT_ACKS.add(ack_key)
        _mark_terminal_result_acknowledged(game_id, payload.match_id, payload, winner)
        if len(_GAME_RESULT_ACKS) > 2048:
            _GAME_RESULT_ACKS.clear()
            _GAME_RESULT_ACKS.add(ack_key)
        return {"ok": True, "duplicate": False}

    @router.post("/{game_id}/log")
    async def append_game_log(game_id: str, payload: GameLogEvent) -> dict[str, bool]:
        write_game_log(game_id, payload.match_id, payload.event)
        # Result progression is intentionally NOT triggered here. The browser
        # first speaks/records the ending and then calls result-ack. This keeps
        # the next script beat/revanche from racing ahead of the visible ending.
        return {"ok": True, "result_pending": bool(payload.event.get("result"))}

    return router
