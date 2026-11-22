"""Shared memory command handlers for both singleton (routes.py) and classic (websocket_handler.py) WebSocket paths."""

from .chat_history_manager import delete_history


def handle_memory_book_list(agent, data: dict) -> dict:
    limit = int(data.get("limit", 200))
    items = agent.memory_book_list(limit=limit)
    return {"type": "memory-book-list", "ok": True, "items": items}


def handle_memory_book_edit(agent, data: dict) -> dict:
    item_id = data.get("item_id")
    if not item_id:
        return {"type": "memory-book-edit", "ok": False, "message": "missing item_id"}
    updates = data.get("updates", {})
    item = agent.memory_book_edit(item_id=item_id, updates=updates)
    return {"type": "memory-book-edit", "ok": item is not None, "item": item}


def handle_memory_book_delete(agent, data: dict) -> dict:
    item_id = data.get("item_id")
    if not item_id:
        return {"type": "memory-book-delete", "ok": False, "message": "missing item_id"}
    ok = agent.memory_book_delete(item_id=item_id)
    return {"type": "memory-book-delete", "ok": bool(ok), "item_id": item_id}


def handle_memory_book_clear(agent, data: dict) -> dict:
    ok = agent.memory_book_clear()
    return {"type": "memory-book-clear", "ok": bool(ok)}


def handle_memory_book_compact(agent, data: dict) -> dict:
    reason = str(data.get("reason", "manual"))
    ok = agent.memory_book_compact(reason=reason)
    return {"type": "memory-book-compact", "ok": bool(ok)}


def handle_memory_book_purge_trivial(agent, data: dict) -> dict:
    removed = agent.memory_book_purge_trivial()
    return {"type": "memory-book-purge-trivial", "ok": True, "removed": removed}


def handle_delete_history(conf_uid: str, data: dict, on_current_active=None) -> dict:
    history_uid = data.get("history_uid")
    if not history_uid:
        return {"type": "history-deleted", "success": False, "history_uid": None}

    also_purge = bool(data.get("also_purge_memory_book", False))
    purged = 0

    if also_purge and on_current_active is not None:
        agent = on_current_active.get("agent")
        if agent and hasattr(agent, "_memory_book_manager") and agent._memory_book_manager:
            purged = agent._memory_book_manager.delete_items_by_source(history_uid)

    success = delete_history(conf_uid, history_uid)
    result = {"type": "history-deleted", "success": success, "history_uid": history_uid}
    if also_purge:
        result["purged_memory_items"] = purged
    return result
