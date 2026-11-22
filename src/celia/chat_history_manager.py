import os
from .core.raizes import CHAT_HISTORY
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal, List, TypedDict, Optional
from loguru import logger
from .core.state_store import (
    StateCorruptError,
    atomic_write_json,
    file_lock,
    locked_json_update,
    read_json,
)


class HistoryMessage(TypedDict):
    role: Literal["human", "ai"]
    timestamp: str
    content: str
    # Optional display information for the message
    name: Optional[str]
    avatar: Optional[str]


def _is_safe_filename(filename: str) -> bool:
    """Validate filename for safety and allowed characters"""
    if not filename or len(filename) > 255:
        return False

    # Allow alphanumeric, hyphen, underscore, and common unicode characters
    # Block any filesystem special characters, control characters, and path separators
    pattern = re.compile(r"^[\w\-_\u0020-\u007E\u00A0-\uFFFF]+$")
    return bool(pattern.match(filename))


def _sanitize_path_component(component: str) -> str:
    """Validate one identifier without silently reducing paths to basenames."""
    sanitized = component.strip()
    if sanitized in {".", ".."} or "/" in sanitized or "\\" in sanitized:
        raise ValueError("Path separators and traversal components are not allowed")
    if not _is_safe_filename(sanitized):
        raise ValueError(f"Invalid characters in path component: {component}")

    return sanitized


def _ensure_conf_dir(conf_uid: str) -> str:
    """Ensure the directory for a specific conf exists and return its path"""
    if not conf_uid:
        raise ValueError("conf_uid cannot be empty")

    safe_conf_uid = _sanitize_path_component(conf_uid)
    base_dir = str(CHAT_HISTORY / safe_conf_uid)
    os.makedirs(base_dir, exist_ok=True)
    return base_dir


def _get_safe_history_path(conf_uid: str, history_uid: str) -> str:
    """Get sanitized path for history file"""
    safe_conf_uid = _sanitize_path_component(conf_uid)
    safe_history_uid = _sanitize_path_component(history_uid)
    base = (CHAT_HISTORY / safe_conf_uid).resolve()
    full = (base / f"{safe_history_uid}.json").resolve()
    if not full.is_relative_to(base):
        raise ValueError("Invalid path: Path traversal detected")
    return str(full)


def create_new_history(conf_uid: str) -> str:
    """Create a new history file with a unique ID and return the history_uid"""
    if not conf_uid:
        logger.warning("No conf_uid provided")
        return ""

    # Use uuid.uuid4().hex to generate a UUID without hyphens
    # New format: UUID_YYYY-MM-DD_HH-MM-SS
    history_uid = f"{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}_{uuid.uuid4().hex}"
    conf_dir = _ensure_conf_dir(conf_uid)  # conf_uid is sanitized here

    # Create history file with metadata and initial system message with current date/time
    try:
        filepath = os.path.join(conf_dir, f"{history_uid}.json")
        
        # Get current date/time formatted in Portuguese
        now = datetime.now()
        weekday_names = ["Segunda-feira", "Terça-feira", "Quarta-feira", "Quinta-feira", "Sexta-feira", "Sábado", "Domingo"]
        month_names = ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", 
                       "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"]
        
        weekday = weekday_names[now.weekday()]
        month = month_names[now.month - 1]
        date_str = f"{weekday}, {now.day} de {month} de {now.year}"
        time_str = now.strftime("%H:%M")
        
        initial_data = [
            {
                "role": "metadata",
                "timestamp": now.isoformat(timespec="seconds"),
            },
            {
                "role": "system",
                "timestamp": now.isoformat(timespec="seconds"),
                "content": f"[DATA E HORA ATUAL: Hoje é {date_str}, agora são {time_str}. Use esta informação como contexto temporal para todas as suas respostas.]",
            }
        ]
        atomic_write_json(filepath, initial_data, backup=False)
    except Exception as e:
        logger.error(f"Failed to create new history file: {e}")
        return ""

    logger.debug(f"Created new history file with empty metadata: {filepath}")
    return history_uid


def store_message(
    conf_uid: str,
    history_uid: str,
    role: Literal["human", "ai"],
    content: str,
    name: str | None = None,
    avatar: str | None = None,
):
    """Store a message in a specific history file

    Args:
        conf_uid: Configuration unique identifier
        history_uid: History unique identifier
        role: Message role ("human" or "ai")
        content: Message content
        name: Optional display name (default None)
        avatar: Optional avatar URL (default None)
    """
    if not conf_uid or not history_uid:
        if not conf_uid:
            logger.warning("Missing conf_uid")
        if not history_uid:
            logger.warning("Missing history_uid")
        return

    filepath = _get_safe_history_path(conf_uid, history_uid)
    logger.debug(f"Storing {role} message to {filepath}")

    now_str = datetime.now().isoformat(timespec="seconds")
    new_item = {"role": role, "timestamp": now_str, "content": content}
    if name is not None:
        new_item["name"] = name
    if avatar is not None:
        new_item["avatar"] = avatar

    def append_message(history_data):
        if not isinstance(history_data, list):
            raise StateCorruptError(f"invalid history shape: {Path(filepath).name}")
        history_data.append(new_item)

    try:
        locked_json_update(filepath, append_message, default_factory=list)
        logger.debug(f"Successfully stored {role} message")
    except (PermissionError, OSError, TimeoutError) as exc:
        if getattr(exc, "errno", None) in (13, 32) or isinstance(exc, PermissionError):
            logger.warning(
                f"chat_history locked ({exc.__class__.__name__}): pulando save deste turn ({filepath})"
            )
        elif isinstance(exc, TimeoutError):
            logger.warning(f"chat_history lock timeout: {Path(filepath).name}")
        else:
            raise
    except StateCorruptError as exc:
        # Never replace damaged history with an empty list; preserve bytes for recovery.
        logger.error(f"History JSON corrupt; write skipped ({Path(filepath).name}): {exc}")


def get_metadata(conf_uid: str, history_uid: str) -> dict:
    """Get metadata from history file"""
    if not conf_uid or not history_uid:
        return {}

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        return {}

    try:
        with file_lock(filepath):
            history_data = read_json(filepath)

        if history_data and history_data[0]["role"] == "metadata":
            return history_data[0]
    except Exception as e:
        logger.error(f"Failed to get metadata: {e}")
    return {}


def update_metadate(conf_uid: str, history_uid: str, metadata: dict) -> bool:
    """Set metadata in history file

    Updates existing metadata with new fields, preserving existing ones.
    If no metadata exists, creates new metadata entry.
    """
    if not conf_uid or not history_uid:
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        return False

    try:
        with file_lock(filepath):
            history_data = read_json(filepath)
            if not isinstance(history_data, list):
                raise StateCorruptError(f"invalid history shape: {Path(filepath).name}")
            if history_data and history_data[0]["role"] == "metadata":
                history_data[0].update(metadata)
            else:
                new_metadata = {
                    "role": "metadata",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                }
                new_metadata.update(metadata)
                history_data.insert(0, new_metadata)
            atomic_write_json(filepath, history_data, already_locked=True)

        logger.debug(f"Updated metadata for history {history_uid}")
        return True
    except Exception as e:
        logger.error(f"Failed to set metadata: {e}")
    return False


def get_history(conf_uid: str, history_uid: str) -> List[HistoryMessage]:
    """Read chat history for the given conf_uid and history_uid"""
    if not conf_uid or not history_uid:
        if not conf_uid:
            logger.warning("Missing conf_uid")
        if not history_uid:
            logger.warning("Missing history_uid")
        return []

    filepath = _get_safe_history_path(conf_uid, history_uid)

    if not os.path.exists(filepath):
        logger.warning(f"History file not found: {filepath}")
        return []

    try:
        with file_lock(filepath):
            history_data = read_json(filepath)
        return [msg for msg in history_data if msg["role"] != "metadata"]
    except Exception:
        return []


def delete_history(conf_uid: str, history_uid: str) -> bool:
    """Delete a specific history file"""
    if not conf_uid or not history_uid:
        logger.warning("Missing conf_uid or history_uid")
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    try:
        with file_lock(filepath):
            if os.path.exists(filepath):
                os.remove(filepath)
                logger.debug(f"Successfully deleted history file: {Path(filepath).name}")
                return True
    except Exception as e:
        logger.error(f"Failed to delete history file: {e}")
    return False


def get_history_list(conf_uid: str) -> List[dict]:
    """Get list of histories with their latest messages"""
    if not conf_uid:
        return []

    histories = []
    conf_dir = _ensure_conf_dir(conf_uid)
    empty_history_uids = []

    try:
        for filename in os.listdir(conf_dir):
            if not filename.endswith(".json"):
                continue

            history_uid = filename[:-5]
            filepath = os.path.join(conf_dir, filename)

            try:
                with file_lock(filepath):
                    messages = read_json(filepath)

                    # Filter out metadata for checking if history is empty
                    actual_messages = [
                        msg for msg in messages if msg["role"] != "metadata"
                    ]
                    if not actual_messages:
                        empty_history_uids.append(history_uid)
                        continue

                    latest_message = actual_messages[-1]
                    history_info = {
                        "uid": history_uid,
                        "latest_message": latest_message,
                        "timestamp": (
                            latest_message["timestamp"] if latest_message else None
                        ),
                    }
                    histories.append(history_info)
            except Exception as e:
                logger.error(f"Error reading history file {filename}: {e}")
                continue

        # Clean up empty histories if there are other non-empty ones
        if len(empty_history_uids) > 0 and len(os.listdir(conf_dir)) > 1:
            for uid in empty_history_uids:
                try:
                    empty_path = os.path.join(conf_dir, f"{uid}.json")
                    with file_lock(empty_path):
                        if os.path.exists(empty_path):
                            os.remove(empty_path)
                    logger.info(f"Removed empty history file: {uid}")
                except Exception as e:
                    logger.error(f"Failed to remove empty history file {uid}: {e}")

        histories.sort(
            key=lambda x: x["timestamp"] if x["timestamp"] else "", reverse=True
        )
        return histories

    except Exception as e:
        logger.error(f"Error listing histories: {e}")
        return []


def get_latest_history_uid(conf_uid: str) -> Optional[str]:
    """
    Retorna o history_uid do histórico mais recente com mensagens reais.
    Útil para o CeliaSingleton restaurar o histórico ao reiniciar.

    Returns:
        str: history_uid do mais recente, ou None se não houver nenhum.
    """
    histories = get_history_list(conf_uid)
    if histories:
        return histories[0]["uid"]  # get_history_list já ordena por timestamp desc
    return None


def modify_latest_message(
    conf_uid: str,
    history_uid: str,
    role: Literal["human", "ai", "system"],
    new_content: str,
) -> bool:
    """Modify the latest message in a specific history file if it matches the given role"""
    if not conf_uid or not history_uid:
        logger.warning("Missing conf_uid or history_uid")
        return False

    filepath = _get_safe_history_path(conf_uid, history_uid)
    if not os.path.exists(filepath):
        logger.warning(f"History file not found: {filepath}")
        return False

    try:
        with file_lock(filepath):
            history_data = read_json(filepath)
            if not isinstance(history_data, list) or not history_data:
                logger.warning("History is empty or has an invalid shape")
                return False
            latest_message = history_data[-1]
            if latest_message["role"] != role:
                logger.warning("Latest history role does not match requested role")
                return False
            latest_message["content"] = new_content
            atomic_write_json(filepath, history_data, already_locked=True)

        logger.debug(f"Successfully modified latest {role} message")
        return True

    except Exception as e:
        logger.error(f"Failed to modify latest message: {e}")
        return False


def rename_history_file(
    conf_uid: str, old_history_uid: str, new_history_uid: str
) -> bool:
    """Rename a history file with a new history_uid"""
    if not conf_uid or not old_history_uid or not new_history_uid:
        logger.warning("Missing required parameters for rename")
        return False

    old_filepath = _get_safe_history_path(conf_uid, old_history_uid)
    new_filepath = _get_safe_history_path(conf_uid, new_history_uid)

    try:
        from contextlib import ExitStack

        with ExitStack() as stack:
            for path in sorted((old_filepath, new_filepath), key=str.casefold):
                stack.enter_context(file_lock(path))
            if os.path.exists(old_filepath):
                os.rename(old_filepath, new_filepath)
                logger.info("Renamed chat history file")
                return True
    except Exception as e:
        logger.error(f"Failed to rename history file: {e}")
    return False
