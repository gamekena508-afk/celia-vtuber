"""Timestamped cache for the latest useful visual observation."""
from __future__ import annotations

import os
import re
import threading
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class VisionNote:
    ts: float
    app: str
    summary: str
    salient: str
    verified_ts: float


@dataclass(frozen=True)
class VisionSelection:
    """`age_ms` is ALWAYS the age of the analysis itself (when the VLM actually looked).

    `verified_age_ms` is how long ago the screen was last seen unchanged (touch).
    It is extra information; it never makes `age_ms` look younger.
    """

    note: VisionNote | None
    age_ms: int | None
    policy: str
    action: str  # cache | refresh | ask | unavailable
    verified_age_ms: int | None = None


_FRESH_WORDS = re.compile(r"\b(agora|neste momento|de novo)\b", re.IGNORECASE)


class VisionCache:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._note: VisionNote | None = None

    def clear(self) -> None:
        with self._lock:
            self._note = None

    def put(self, app: str, summary: str, salient: str = "", *, analyzed_at: float | None = None) -> VisionNote:
        now = time.monotonic()
        note = VisionNote(
            ts=now if analyzed_at is None else analyzed_at,
            app=str(app or "screen")[:80],
            summary=str(summary or "").strip()[:500],
            salient=str(salient or "").strip()[:240],
            verified_ts=now,
        )
        with self._lock:
            self._note = note
        return note

    def touch(self) -> None:
        """The screen was seen unchanged: renews `verified_ts` ONLY.

        The analysis time `ts` is never renewed, so an old frame cannot pass as new
        just because some process re-checked it (owner decision, 2026-10-04).
        """
        with self._lock:
            if self._note is None:
                return
            old = self._note
            self._note = VisionNote(
                ts=old.ts,
                app=old.app,
                summary=old.summary,
                salient=old.salient,
                verified_ts=time.monotonic(),
            )

    def get(self) -> tuple[VisionNote | None, int | None]:
        """Newest note and the age of its ANALYSIS (not of the last re-check)."""
        with self._lock:
            note = self._note
        if note is None:
            return None, None
        age_ms = max(0, int((time.monotonic() - note.ts) * 1000))
        return note, age_ms

    def get_ages(self) -> tuple[VisionNote | None, int | None, int | None]:
        """(note, analysis_age_ms, verified_age_ms). Only the first age is freshness."""
        with self._lock:
            note = self._note
        if note is None:
            return None, None, None
        now = time.monotonic()
        return (
            note,
            max(0, int((now - note.ts) * 1000)),
            max(0, int((now - note.verified_ts) * 1000)),
        )

    def snapshot(self) -> dict[str, Any]:
        note, age_ms, verified_age_ms = self.get_ages()
        return {
            "available": note is not None,
            "age_ms": age_ms,
            "verified_age_ms": verified_age_ms,
            "note": asdict(note) if note is not None else None,
        }


vision_cache = VisionCache()


def select_vision_for_request(
    text: str,
    *,
    policy: str | None = None,
    max_age_ms: int | None = None,
) -> VisionSelection:
    """Resolve cache/freshness policy without triggering GPU work itself.

    off     -> preserve the legacy on-demand vision path
    cache   -> always use the newest cached note, even if old (age is exposed)
    refresh -> stale/explicit-now requests go to the legacy/live vision path
    ask     -> stale/explicit-now requests ask/ack instead of pretending freshness
    """
    selected_policy = str(
        policy or os.getenv("CELIA_VISION_CACHE_POLICY", "off")
    ).strip().lower()
    if selected_policy not in {"off", "cache", "refresh", "ask"}:
        selected_policy = "off"
    if max_age_ms is None:
        try:
            max_age_ms = max(
                0, int(os.getenv("CELIA_VISION_MAX_AGE_MS", "8000"))
            )
        except ValueError:
            max_age_ms = 8000

    note, age_ms, verified_age_ms = vision_cache.get_ages()
    if selected_policy == "off":
        return VisionSelection(note, age_ms, selected_policy, "refresh", verified_age_ms)

    wants_fresh = bool(_FRESH_WORDS.search(text or ""))
    stale = age_ms is None or age_ms > max_age_ms
    if wants_fresh or stale:
        if selected_policy == "refresh":
            return VisionSelection(note, age_ms, selected_policy, "refresh", verified_age_ms)
        if selected_policy == "ask":
            return VisionSelection(note, age_ms, selected_policy, "ask", verified_age_ms)

    if note is None:
        return VisionSelection(None, None, selected_policy, "unavailable")
    return VisionSelection(note, age_ms, selected_policy, "cache", verified_age_ms)


def max_age_ms_default() -> int:
    try:
        return max(0, int(os.getenv("CELIA_VISION_MAX_AGE_MS", "8000")))
    except ValueError:
        return 8000


def _seconds(age_ms: int | None) -> str:
    return "?" if age_ms is None else f"{age_ms / 1000:.1f}".rstrip("0").rstrip(".")


def format_vision_context(selection: VisionSelection, max_age_ms: int | None = None) -> str:
    """Present a cached observation as EVIDENCE with its real age, never as authority.

    The age shown is the age of the analysis (`selection.age_ms`). A re-check of an
    unchanged screen is mentioned separately and never replaces it. A stale note
    (`action == "ask"`) is labelled as old and tells Célia that no new analysis was
    started, so she can decide whether to ask for a new look.
    """
    limit = max_age_ms_default() if max_age_ms is None else max_age_ms
    note = selection.note
    if selection.age_ms is not None and selection.age_ms > limit:
        return "[VISUAL SENSOR — unavailable: previous analysis expired; request a fresh look if relevant]"
    if note is None:
        return (
            "[SEM CONTEXTO VISUAL — nenhuma análise de tela disponível]\n"
            "Não descreva o que não viu. Se a conversa precisar da tela, diga que "
            "precisa olhar de novo."
        )
    seen = ""
    if (
        selection.verified_age_ms is not None
        and selection.age_ms is not None
        and selection.verified_age_ms + 500 < selection.age_ms
    ):
        seen = (
            f"A tela foi vista sem mudança há {_seconds(selection.verified_age_ms)}s, "
            "mas a análise em si é a do tempo acima.\n"
        )
    facts = (
        f"app={note.app}; resumo={note.summary}; saliente={note.salient or '-'}\n"
    )
    if selection.action == "ask":
        return (
            f"[CONTEXTO VISUAL VELHO — análise feita há {_seconds(selection.age_ms)} segundos"
            f" (limite {_seconds(limit)}s)]\n"
            "Existe uma observação visual, mas ela está velha e pode não valer mais. "
            "Nenhuma nova análise foi iniciada (a GPU não foi acionada). Use só se tiver "
            "relação com a conversa; se precisar do que está na tela agora, diga de forma "
            "curta que precisa olhar de novo, em vez de afirmar o estado atual.\n"
            f"{seen}{facts}"
        )
    return (
        f"[CONTEXTO VISUAL POSSÍVEL — análise feita há {_seconds(selection.age_ms)} segundos]\n"
        "Esta é uma observação visual anterior e pode estar desatualizada. Use-a apenas se "
        "tiver relação com o contexto atual. Não trate como estado atual garantido.\n"
        f"{seen}{facts}"
    )
