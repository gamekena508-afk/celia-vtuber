"""Live teleprompter state for the recording profile.

The runtime is deliberately a view over existing Célia events. It does not
run ASR, LLM, TTS, tools, or a second conversation loop.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
import socket
import time
import uuid
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import WebSocket
from loguru import logger

from .raizes import RAIZ

SCRIPT_PATH = RAIZ / "roteiros" / "ia_vs_criador.json"
SESSION_LOG_DIR = RAIZ / "var" / "roteiro_sessions"
MAX_HISTORY = 180
SCRIPT_PROFILES = frozenset({"roteiro", "gravacao"})
_WORD = re.compile(r"[a-z0-9áàâãéêíóôõúç×]+", re.IGNORECASE)
_SCRIPT_STOPWORDS = {"a", "ao", "as", "de", "do", "e", "em", "eu", "o", "os", "que", "tu"}


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _canonical_game_id(value: Any) -> str:
    normalized = _norm(value).replace("_", " ").replace("-", " ")
    aliases = {
        "tic tac toe": "tic-tac-toe",
        "jogo da velha": "tic-tac-toe",
        "velha": "tic-tac-toe",
        "checkers": "checkers",
        "dama": "checkers",
        "damas": "checkers",
        "damas brasileiras": "checkers",
        "chess": "chess",
        "xadrez": "chess",
    }
    return aliases.get(normalized, _norm(value))


def _words(value: Any) -> set[str]:
    return set(_WORD.findall(_norm(value)))


def _private_ipv4_addresses() -> list[str]:
    """Return LAN candidates without advertising loopback or public DNS."""
    addresses: set[str] = set()
    try:
        import fcntl
        import struct

        ignored = (
            "docker",
            "veth",
            "br-",
            "virbr",
            "wg",
            "tun",
            "tap",
            "cloudflare",
        )
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            for _index, interface in socket.if_nameindex():
                if interface == "lo" or interface.lower().startswith(ignored):
                    continue
                request = struct.pack("256s", interface.encode()[:15])
                try:
                    result = fcntl.ioctl(probe.fileno(), 0x8915, request)
                    addresses.add(socket.inet_ntoa(result[20:24]))
                except OSError:
                    continue
    except (ImportError, OSError):
        pass
    # UDP connect selects a local interface without sending application data.
    if not addresses:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                probe.connect(("192.0.2.1", 1))
                addresses.add(probe.getsockname()[0])
        except OSError:
            pass
    try:
        _host, _aliases, values = socket.gethostbyname_ex(socket.gethostname())
        addresses.update(values)
    except OSError:
        pass
    valid = [
        address
        for address in addresses
        if ipaddress.ip_address(address).is_private
        and not address.startswith(("127.", "169.254."))
        and not address.startswith("0.")
    ]
    return sorted(valid, key=lambda value: (not value.startswith("192.168."), value))


class RoteiroLiveRuntime:
    """Bounded event-driven state for one recording session."""

    def __init__(self, script_path: Path = SCRIPT_PATH) -> None:
        self.script_path = script_path
        self._subscribers: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._script: dict[str, Any] = {"id": "unloaded", "title": "", "beats": []}
        self._beats: dict[str, dict[str, Any]] = {}
        self.active = False
        self.profile = "chat"
        self.session_id = ""
        self.auto_follow = True
        self.current_beat_id: str | None = None
        self.route: list[str] = []
        self.completed_lines: set[str] = set()
        self.history: deque[dict[str, Any]] = deque(maxlen=MAX_HISTORY)
        self.criador = {"partial": "", "last_final": "", "utterance_id": ""}
        self.criador_match = {"matched": False, "score": 0, "beat_id": None, "source": None}
        self.celia = {"state": "idle", "last_text": "", "speech_id": ""}
        self.status = "INATIVO"
        self.last_event: dict[str, Any] | None = None
        self._behavior = None
        self.take_id = ""
        self.take_mark = "active"
        self._take_sequence = 0
        self._pending_audio: dict[str, dict[str, Any]] = {}
        self._active_audio: dict[str, dict[str, Any]] = {}
        self._turn_takes: dict[str, str] = {}
        self._turn_groups: dict[str, dict[str, Any]] = {}
        self._forced_line_by_turn: dict[str, str] = {}
        self._line_memory_marks: dict[str, int] = {}
        self._last_celia_bundle: dict[str, Any] | None = None
        self._last_failed_celia_text = ""
        self._completed_matches: set[str] = set()
        self._game_results: dict[str, dict[str, Any]] = {}
        self._recording_started_at = 0.0
        self.markers: deque[dict[str, Any]] = deque(maxlen=240)
        self.audio_pack_status = {"prepared_lines": 0, "missing_line_ids": []}

    def _load_script(self, data: dict[str, Any] | None = None) -> None:
        try:
            if data is None:
                data = json.loads(self.script_path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("invalid_script")
        except (OSError, ValueError) as exc:
            logger.warning("Roteiro Live: script unavailable: %s", type(exc).__name__)
            raise ValueError("recording_script_unavailable") from exc
        beats = [
            item
            for item in data.get("beats", [])
            if isinstance(item, dict) and item.get("id")
        ]
        if not beats:
            raise ValueError("recording_script_empty")
        self._script = {
            "id": data.get("id", "ia-vs-criador"),
            "title": data.get("title", "IA VS CRIADOR"),
            "subtitle": data.get("subtitle", ""),
            "version": data.get("version", 1),
            "start": data.get("start"),
            "beats": beats,
        }
        self._beats = {str(item["id"]): item for item in beats}

    def prepare_operational_script(self, behavior, script=None) -> dict[str, Any]:
        from .continuous_behavior import ScriptTracker
        if script is None:
            # ORDEM DO DONO, 2026-09-30: "no início é pra eu escolher o roteiro
            # e que este bug de volta não se repita."
            #
            # Antes daqui sempre saía o canônico do disco (ia_vs_criador.json).
            # Como os dois chamadores sem script são as trocas para o perfil
            # `roteiro` (bridge_routes.set_behavior_profile e
            # profile_switch_intent), qualquer caminho que mudasse o perfil
            # APAGAVA o roteiro que o dono tinha escolhido. Reproduzido: com
            # celia-3d-passo-a-passo carregado, um clique em "Roteiro Live"
            # devolveu o disco a ia-vs-criador-volta-do-canal.
            #
            # Agora: um roteiro já carregado VENCE o canônico. Trocar de perfil
            # não é escolher roteiro. Quem escolhe é o dono, em
            # POST /roteiro/scripts/select — ou apagando o roteiro carregado.
            carregado = None
            try:
                exportado = behavior.script.export()
                if isinstance(exportado, dict) and exportado.get("beats"):
                    carregado = exportado
            except Exception:  # noqa: BLE001 - sem roteiro carregado, cai no canônico
                carregado = None
            if carregado is not None:
                logger.info(
                    "[Roteiro] mantendo o roteiro já carregado ({}) em vez de "
                    "recarregar o canônico do disco.",
                    carregado.get("id") or "sem id",
                )
                script = carregado
            else:
                self._load_script()
                script = self._script
        validated = ScriptTracker()
        validated.load(script)
        if not validated.beats:
            raise ValueError("recording_script_empty")
        from .roteiro_audio_pack import roteiro_audio_pack
        pack_status = roteiro_audio_pack.load(validated.export())
        self.audio_pack_status = pack_status
        logger.info("[Roteiro] prepared audio pack: {}", pack_status)
        exported = validated.export()
        if behavior.script.export() != exported:
            behavior.load_script(exported)
        self._behavior = behavior
        return exported

    def _sync_operational_position(self) -> None:
        if self._behavior is not None and self.current_beat_id:
            self._behavior.set_script_position(self.current_beat_id)

    def _beat_view(
        self, beat_id: str | None, *, include_next: bool = True
    ) -> dict[str, Any] | None:
        beat = self._beats.get(beat_id or "")
        if not beat:
            return None
        next_views = []
        if include_next:
            next_views = [
                self._beat_view(item, include_next=False)
                for item in (beat.get("next") or [])
                if item in self._beats
            ]
        return {
            "id": beat["id"],
            "title": beat.get("title", beat["id"]),
            "objective": beat.get("objective", ""),
            "suggested": list(beat.get("suggested") or [])[:8],
            "lines": self._line_views(beat),
            "next": next_views,
            "branches": dict(beat.get("branches") or {}),
            "branch_source": str(beat.get("branch_source") or ""),
            "result_game": str(beat.get("result_game") or ""),
        }

    def _route_view(self) -> list[dict[str, str]]:
        return [
            {"id": beat_id, "title": self._beats.get(beat_id, {}).get("title", beat_id)}
            for beat_id in self.route[-32:]
        ]

    def _script_tokens(self, value: Any) -> set[str]:
        return {word for word in _words(value) if len(word) > 1 and word not in _SCRIPT_STOPWORDS}

    def _match_text_to_beat(self, text: str, beat: dict[str, Any]) -> int:
        line_text = " ".join(
            str(line.get("text") or "")
            for line in (beat.get("lines") or [])
            if isinstance(line, dict)
        )
        source = " ".join(
            [
                str(beat.get("title") or ""),
                " ".join(beat.get("match") or []),
                " ".join(beat.get("suggested") or []),
                line_text,
            ]
        )
        return len(self._script_tokens(text) & self._script_tokens(source))

    def _has_direct_match_term(self, text: str, beat: dict[str, Any]) -> bool:
        text_tokens = self._script_tokens(text)
        return any(
            len(self._script_tokens(term)) == 1
            and next(iter(self._script_tokens(term))) in text_tokens
            and len(next(iter(self._script_tokens(term)))) > 3
            for term in beat.get("match") or []
        )

    def _mark_script_line(self, text: str, speaker: str) -> dict[str, Any] | None:
        candidates: list[tuple[int, str, str]] = []
        current = self._beats.get(self.current_beat_id or "") or {}
        previous = self.route[-2] if len(self.route) > 1 else None
        # A response can arrive after Criador's line advances the route. Keep the
        # immediately previous beat eligible so the real Célia line still lights.
        beat_ids = [previous, self.current_beat_id] + list(current.get("next") or [])
        wanted = _norm(speaker)
        for beat_id in beat_ids:
            beat = self._beats.get(beat_id or "") or {}
            for index, line in enumerate(beat.get("lines") or []):
                if not isinstance(line, dict) or _norm(line.get("speaker")) != wanted:
                    continue
                key = f"{beat_id}:{index}"
                if key in self.completed_lines:
                    continue
                line_text = str(line.get("text") or "")
                line_tokens = self._script_tokens(line_text)
                text_tokens = self._script_tokens(text)
                common_tokens = text_tokens & line_tokens
                score = len(common_tokens)
                exact = _norm(line_text) in _norm(text)
                single_term = len(line_tokens) == 1 and len(next(iter(line_tokens), "")) > 4
                # Short scripted cues commonly lose one word in ASR (observed:
                # "Fala, pessoal." -> "Pá, pessoal!"). One long shared anchor
                # is sufficient only when both utterances are short.
                short_anchor = (
                    score == 1
                    and len(line_tokens) <= 3
                    and len(text_tokens) <= 3
                    and len(next(iter(common_tokens), "")) >= 5
                )
                if exact or score >= 2 or (single_term and score == 1) or short_anchor:
                    candidates.append((score, key, str(beat_id)))
        if not candidates:
            return None
        score, key, beat_id = max(candidates, key=lambda item: item[0])
        threshold = 2 if any(item[0] >= 2 for item in candidates) else score
        for candidate_score, candidate_key, _candidate_beat in candidates:
            if candidate_score >= threshold:
                self.completed_lines.add(candidate_key)
        return {"matched": True, "score": score, "line_id": key, "beat_id": beat_id}

    def _line_views(self, beat: dict[str, Any] | None) -> list[dict[str, Any]]:
        views = []
        for index, line in enumerate((beat or {}).get("lines") or []):
            if not isinstance(line, dict) or not line.get("text"):
                continue
            key = f"{beat.get('id')}:{index}"
            views.append(
                {
                    "id": key,
                    "speaker": line.get("speaker", ""),
                    "text": line.get("text", ""),
                    "optional": bool(line.get("optional", False)),
                    "status": "done" if key in self.completed_lines else "pending",
                }
            )
        return views

    def _required_lines_complete(self, beat_id: str | None) -> bool:
        beat = self._beats.get(beat_id or "") or {}
        required = [
            f"{beat_id}:{index}"
            for index, line in enumerate(beat.get("lines") or [])
            if isinstance(line, dict) and line.get("text") and not line.get("optional")
        ]
        return bool(required) and all(key in self.completed_lines for key in required)

    def _awaiting_celia_response(self, beat_id: str | None) -> bool:
        """Keep a beat visible until its conversational Célia turn arrives."""
        beat = self._beats.get(beat_id or "") or {}
        if beat.get("await_celia") is False:
            return False
        lines = [line for line in (beat.get("lines") or []) if isinstance(line, dict)]
        has_celia = any(_norm(line.get("speaker")) in {"celia", "célia"} for line in lines)
        has_required_criador = any(
            _norm(line.get("speaker")) == "criador"
            and line.get("text")
            and not line.get("optional")
            for line in lines
        )
        return bool(beat.get("await_celia") or (has_celia and has_required_criador))

    def planned_celia_reply(self) -> dict[str, str] | None:
        """Return the pending scripted Célia line once Criador completed this beat."""
        if not self.active or not self.current_beat_id:
            return None
        if (not self.criador_match.get("matched")
                or self.criador_match.get("beat_id") != self.current_beat_id):
            return None
        beat = self._beats.get(self.current_beat_id) or {}
        if not self._awaiting_celia_response(self.current_beat_id):
            return None
        if not self._required_lines_complete(self.current_beat_id):
            return None
        return self._celia_line_of_beat(beat, pending_only=True)

    def _celia_line_of_beat(self, beat: dict[str, Any], *, pending_only: bool) -> dict[str, str] | None:
        """Fala da Célia do bloco atual: a primeira pendente; sem `pending_only`, a última (para tocar de novo)."""
        found: dict[str, str] | None = None
        for index, line in enumerate(beat.get("lines") or []):
            if not isinstance(line, dict) or _norm(line.get("speaker")) not in {"celia", "célia"}:
                continue
            text = " ".join(str(line.get("text") or "").split())
            key = f"{self.current_beat_id}:{index}"
            if not text or (pending_only and key in self.completed_lines):
                continue
            reply = {"beat_id": self.current_beat_id, "line_id": key, "text": text}
            tts_text = " ".join(str(line.get("tts_text") or "").split())
            if tts_text:
                reply["tts_text"] = tts_text
            if pending_only:
                return reply
            if key not in self.completed_lines:
                return reply  # a pendente vence a já concluída
            found = reply
        return found

    def explicit_celia_reply(self) -> dict[str, str] | None:
        """Fala da Célia para o clique explícito do dono (TOCAR), sem o portão do ASR.

        `planned_celia_reply` só abre quando o ASR casou o que o Criador disse com o bloco. Quem avança na mão
        (`forward_line`) conclui as linhas sem casar nada, e o botão ficava mudo ("RECUPERAÇÃO INDISPONÍVEL",
        medido em 2026-10-18). O clique é a ordem do dono: toca a pendente do bloco atual ou, se ele já passou
        por ela, a última daquele bloco. O disparo automático NÃO usa isto.
        """
        if not self.active or not self.current_beat_id:
            return None
        return self._celia_line_of_beat(self._beats.get(self.current_beat_id) or {}, pending_only=False)

    def _classify_script_text(self, text: str) -> dict[str, Any]:
        current = self._beats.get(self.current_beat_id or "")
        if current:
            score = self._match_text_to_beat(text, current)
            if score >= 2 or self._has_direct_match_term(text, current):
                return {
                    "matched": True,
                    "score": score,
                    "beat_id": current.get("id"),
                    "source": "current",
                }
        for candidate_id in (current or {}).get("next") or []:
            beat = self._beats.get(candidate_id)
            if not beat:
                continue
            score = self._match_text_to_beat(text, beat)
            if score >= 2 or self._has_direct_match_term(text, beat):
                return {
                    "matched": True,
                    "score": score,
                    "beat_id": candidate_id,
                    "source": "next",
                }
        return {"matched": False, "score": 0, "beat_id": None, "source": None}

    def _write_session_log(self, event: str, data: dict[str, Any] | None = None) -> None:
        if not self.session_id:
            return
        record = {
            "schema": "celia.roteiro.session.v1",
            "timestamp": time.time(),
            "session_id": self.session_id,
            "event": event,
            "profile": self.profile,
            "beat_id": self.current_beat_id,
            "route": list(self.route),
            "data": data or {},
        }
        try:
            SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
            path = SESSION_LOG_DIR / f"{self.session_id}.jsonl"
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        except OSError as exc:
            logger.warning("Roteiro Live: session log unavailable: %s", type(exc).__name__)

    def _begin_take(self, reason: str) -> str:
        """Invalidate late recording events and create a fresh recording take."""
        self._take_sequence += 1
        prefix = self.session_id[:8] if self.session_id else "session"
        self.take_id = f"{prefix}-take-{self._take_sequence:04d}"
        self.take_mark = "active"
        self._pending_audio.clear()
        self._active_audio.clear()
        self.celia = {
            "state": "idle",
            "last_text": str(self.celia.get("last_text") or ""),
            "speech_id": "",
        }
        self._write_session_log("take.started", {"take_id": self.take_id, "reason": reason})
        return self.take_id

    def _reopen_beats(self, beat_ids: list[str]) -> None:
        prefixes = tuple(f"{beat_id}:" for beat_id in beat_ids if beat_id)
        if prefixes:
            self.completed_lines = {
                key for key in self.completed_lines if not key.startswith(prefixes)
            }

    def _agent_dialogue_length(self) -> int:
        try:
            from ..celia_singleton import CeliaSingleton

            singleton = CeliaSingleton.get_instance()
            agent = singleton.context.agent_engine if singleton.context else None
            dialogue = getattr(agent, "current_session_dialogue", None)
            return len(dialogue) if isinstance(dialogue, list) else 0
        except Exception:
            return 0

    def _rollback_agent_context(self, keep_count: int | None, reason: str) -> int:
        if keep_count is None:
            return 0
        try:
            from ..celia_singleton import CeliaSingleton

            singleton = CeliaSingleton.get_instance()
            agent = singleton.context.agent_engine if singleton.context else None
            rollback = getattr(agent, "rollback_current_session_dialogue", None)
            if not callable(rollback):
                return 0
            removed = int(rollback(keep_count) or 0)
            self._write_session_log(
                "context.rolled_back",
                {"keep_count": keep_count, "removed": removed, "reason": reason},
            )
            return removed
        except Exception as exc:
            logger.warning("Roteiro context rollback unavailable: {}", type(exc).__name__)
            return 0

    def _remove_last_agent_assistant(self, reason: str) -> bool:
        try:
            from ..celia_singleton import CeliaSingleton

            singleton = CeliaSingleton.get_instance()
            agent = singleton.context.agent_engine if singleton.context else None
            remove = getattr(agent, "remove_last_current_session_message", None)
            if not callable(remove):
                return False
            removed = bool(remove("assistant"))
            if removed:
                self._write_session_log(
                    "context.assistant_removed", {"reason": reason}
                )
            return removed
        except Exception as exc:
            logger.warning(
                "Roteiro assistant rollback unavailable: {}", type(exc).__name__
            )
            return False

    def _route_lines(self) -> list[tuple[str, int, dict[str, Any], str]]:
        items: list[tuple[str, int, dict[str, Any], str]] = []
        for beat_id in self.route:
            beat = self._beats.get(beat_id) or {}
            for index, line in enumerate(beat.get("lines") or []):
                if not isinstance(line, dict) or not line.get("text"):
                    continue
                items.append((beat_id, index, line, f"{beat_id}:{index}"))
        return items

    def _last_completed_line(self, speaker: str | None = None) -> tuple[str, int, dict[str, Any], str] | None:
        wanted = _norm(speaker) if speaker else ""
        for item in reversed(self._route_lines()):
            _beat_id, _index, line, key = item
            if key not in self.completed_lines:
                continue
            if wanted and _norm(line.get("speaker")) != wanted:
                continue
            return item
        return None

    def _resolve_word_anchor(self, anchor: str) -> tuple[str, int, dict[str, Any], str] | None:
        needle = _norm(anchor)
        if not needle:
            return None
        candidates = list(reversed(self._route_lines()))
        route_keys = {item[3] for item in candidates}
        for beat_id, beat in self._beats.items():
            if any(key.startswith(f"{beat_id}:") for key in route_keys):
                continue
            for index, line in enumerate(beat.get("lines") or []):
                if isinstance(line, dict) and line.get("text"):
                    candidates.append((beat_id, index, line, f"{beat_id}:{index}"))
        for item in candidates:
            if needle in _norm(item[2].get("text")):
                return item
        return None

    def _reopen_from_line(
        self, target: tuple[str, int, dict[str, Any], str], reason: str
    ) -> None:
        beat_id, index, line, key = target
        try:
            route_index = len(self.route) - 1 - self.route[::-1].index(beat_id)
        except ValueError:
            route_index = len(self.route)
            self.route.append(beat_id)
        routed_after = self.route[route_index + 1 :]
        for later_beat in routed_after:
            self._reopen_beats([later_beat])
        self.route = self.route[: route_index + 1]
        self.current_beat_id = beat_id
        for line_index, _line in enumerate((self._beats.get(beat_id) or {}).get("lines") or []):
            if line_index >= index:
                self.completed_lines.discard(f"{beat_id}:{line_index}")
        if _norm(line.get("speaker")) == "criador":
            mark = self._line_memory_marks.get(key)
            if mark is None:
                mark = self._memory_mark_for_beat(beat_id)
            self._rollback_agent_context(mark, reason)
        else:
            self._remove_last_agent_assistant(reason)
        self._sync_operational_position()
        self._add_marker("retake", f"{reason}:{key}")

    def _memory_mark_for_beat(self, beat_id: str | None) -> int | None:
        prefix = f"{beat_id}:" if beat_id else ""
        marks = [
            mark
            for line_id, mark in self._line_memory_marks.items()
            if prefix and line_id.startswith(prefix)
        ]
        return min(marks) if marks else None

    def _add_marker(self, kind: str, note: str = "") -> dict[str, Any]:
        marker = {
            "kind": str(kind or "marker"),
            "note": str(note or "")[:160],
            "take_id": self.take_id,
            "beat_id": self.current_beat_id,
            "elapsed_s": (
                round(time.monotonic() - self._recording_started_at, 3)
                if self._recording_started_at else 0.0
            ),
            "timestamp": time.time(),
        }
        self.markers.append(marker)
        self._write_session_log("recording.marker", marker)
        return marker

    @staticmethod
    def _audio_payload_snapshot(message: dict[str, Any]) -> dict[str, Any]:
        return {
            key: message.get(key)
            for key in (
                "audio", "volumes", "slice_length", "display_text", "actions",
                "bank_playback", "cancel_token",
            )
            if key in message
        }

    def _turn_group(self, turn_id: str, take_id: str = "") -> dict[str, Any]:
        group = self._turn_groups.setdefault(
            turn_id,
            {
                "turn_id": turn_id,
                "take_id": take_id or self.take_id,
                "speech_ids": [],
                "entries": {},
                "finished": {},
                "sealed": False,
                "completed": False,
            },
        )
        if take_id and not group.get("take_id"):
            group["take_id"] = take_id
        return group

    async def _maybe_complete_turn_group(self, turn_id: str) -> bool:
        group = self._turn_groups.get(turn_id)
        if not group or group.get("completed") or not group.get("sealed"):
            return False
        speech_ids = [str(item) for item in group.get("speech_ids") or [] if item]
        if not speech_ids:
            return False
        finished = group.get("finished") or {}
        if any(speech_id not in finished for speech_id in speech_ids):
            return False
        success = {"ended", "finished", "complete", "completed", "ok", "success"}
        statuses = [str(finished[speech_id]).casefold() for speech_id in speech_ids]
        if not all(status in success for status in statuses):
            self.celia["state"] = "idle"
            self.status = "LIVE • FALA RECUPERÁVEL"
            self._write_session_log(
                "celia.turn_incomplete",
                {"turn_id": turn_id, "speech_ids": speech_ids, "statuses": statuses},
            )
            group["completed"] = True
            return False
        if group.get("take_id") != self.take_id:
            self._write_session_log(
                "celia.turn_stale",
                {
                    "turn_id": turn_id,
                    "take_id": group.get("take_id"),
                    "current_take_id": self.take_id,
                },
            )
            group["completed"] = True
            return False
        entries = group.get("entries") or {}
        ordered_entries = [entries.get(speech_id) or {} for speech_id in speech_ids]
        combined = " ".join(
            " ".join(str(entry.get("text") or "").split())
            for entry in ordered_entries
            if entry.get("text")
        ).strip()
        no_complete = any(bool(entry.get("no_complete")) for entry in ordered_entries)
        if combined and not no_complete:
            await self._observe_celia(
                combined,
                speech_ids[-1],
                forced_line_id=self._forced_line_by_turn.pop(turn_id, ""),
            )
        if combined:
            self._last_celia_bundle = {
                "text": combined,
                "payloads": [
                    dict(entry.get("payload") or {})
                    for entry in ordered_entries
                    if entry.get("payload")
                ],
                "speech_ids": speech_ids,
                "turn_id": turn_id,
            }
        group["completed"] = True
        self.celia["state"] = "idle"
        self.status = "LIVE • CONECTADO"
        self._write_session_log(
            "celia.turn_finished",
            {"turn_id": turn_id, "speech_ids": speech_ids, "text": combined},
        )
        return True

    def _start_celia_playback(self, speech_id: str, entry: dict[str, Any]) -> None:
        clean = " ".join(str(entry.get("text") or "").split())[:800]
        if not clean:
            return
        self.celia = {"state": "falando", "last_text": clean, "speech_id": speech_id}
        self.status = "CÉLIA • FALANDO"
        self._write_session_log(
            "celia.playback_started",
            {"speech_id": speech_id, "take_id": entry.get("take_id", "")},
        )

    def snapshot(self) -> dict[str, Any]:
        current = self._beats.get(self.current_beat_id or "")
        planned = self.planned_celia_reply()
        last_completed = self._last_completed_line()
        scoreboard = {"criador": 0, "celia": 0, "draw": 0}
        for result in self._game_results.values():
            winner = _norm(result.get("winner"))
            if winner in {"celia", "célia"}:
                scoreboard["celia"] += 1
            elif winner in {"draw", "empate", "tie", "1/2-1/2"}:
                scoreboard["draw"] += 1
            else:
                scoreboard["criador"] += 1
        return {
            "active": self.active,
            "profile": self.profile,
            "status": self.status,
            "session_id": self.session_id,
            "take_id": self.take_id,
            "take_mark": self.take_mark,
            "recording_elapsed_s": (
                round(time.monotonic() - self._recording_started_at, 3)
                if self._recording_started_at else 0.0
            ),
            "markers": list(self.markers)[-24:],
            "recovery": {
                "planned_celia": dict(planned) if planned else None,
                "can_replay": bool(
                    self._last_celia_bundle
                    and self._last_celia_bundle.get("payloads")
                ),
                "can_regenerate": bool(
                    planned
                    or (self._last_celia_bundle or {}).get("text")
                    or self._last_failed_celia_text
                ),
                "can_new_response": bool(
                    self.criador.get("last_final")
                    or any(item.get("speaker") == "Criador" for item in self.history)
                ),
                "last_completed_line_id": last_completed[3] if last_completed else None,
            },
            "audio_pack": dict(self.audio_pack_status),
            "script": {
                key: value for key, value in self._script.items() if key != "beats"
            },
            "current_beat": self._beat_view(self.current_beat_id),
            "route": self._route_view(),
            "next_beats": (
                []
                if (current or {}).get("branch_source") == "game_result"
                else [
                    self._beat_view(item)
                    for item in (current or {}).get("next", [])
                    if item in self._beats
                ]
            ),
            "branch_options": dict((current or {}).get("branches") or {}),
            "game_results": {key: dict(value) for key, value in self._game_results.items()},
            "game_scoreboard": scoreboard,
            "criador": dict(self.criador),
            "criador_match": dict(self.criador_match),
            "celia": dict(self.celia),
            "history": list(self.history),
            "auto_follow": self.auto_follow,
            "last_event": self.last_event,
            "session_log": f"var/roteiro_sessions/{self.session_id}.jsonl" if self.session_id else None,
            "limits": {"history": MAX_HISTORY},
        }

    def lan_urls(self, port: int) -> list[str]:
        return [
            f"http://{address}:{port}/roteiro/" for address in _private_ipv4_addresses()
        ]

    async def _send(self, websocket: WebSocket, payload: dict[str, Any]) -> bool:
        try:
            await asyncio.wait_for(websocket.send_json(payload), timeout=2.0)
            return True
        except Exception:  # noqa: BLE001 - a dead phone must not affect the runtime.
            return False

    async def _notify(self) -> None:
        payload = {"type": "roteiro.snapshot", "snapshot": self.snapshot()}
        dead: list[WebSocket] = []
        for websocket in list(self._subscribers):
            if not await self._send(websocket, payload):
                dead.append(websocket)
        for websocket in dead:
            self._subscribers.discard(websocket)

    async def connect(self, websocket: WebSocket) -> None:
        self._subscribers.add(websocket)
        await self._send(
            websocket, {"type": "roteiro.snapshot", "snapshot": self.snapshot()}
        )

    def disconnect(self, websocket: WebSocket) -> None:
        self._subscribers.discard(websocket)

    async def restore_from_behavior(self, behavior) -> str | None:
        """No boot, volta ao roteiro que o dono estava usando. Decisão 6(a).

        Antes disto o roteiro tinha dois donos e só um sobrevivia ao reinício:
        `continuous_behavior` restaura do disco no `__init__`, o teleprompter não.
        Resultado medido em 2026-09-30: depois de reiniciar, o celular mostrava
        `ia-vs-criador-volta-do-canal` enquanto o comportamento já tinha o
        `discord-tutorial-2026-09-29` certo, e era preciso um comando para casar
        os dois.

        Só restaura no perfil de gravação, e **sempre com o script explícito** —
        `prepare_operational_script(behavior, None)` recarregaria o canônico do
        disco, que é justamente o defeito que esta função existe para fechar.
        Falhar aqui não pode impedir o servidor de subir.
        """
        if behavior is None or self.active:
            return None
        if str(getattr(behavior, "profile", "")) not in SCRIPT_PROFILES:
            return None
        try:
            script = behavior.script.export()
        except Exception:  # noqa: BLE001 - boot não depende desta restauração
            return None
        if not isinstance(script, dict) or not script.get("beats"):
            return None
        try:
            # ORDEM DO DONO, 2026-09-30: "no início é pra eu escolher o roteiro."
            #
            # Antes daqui saía um `activate(...)`, que marca a gravação como
            # ATIVA. O seletor do painel só aparece com `!snapshot.active`
            # (frontend/roteiro-live/app.js), então o boot escolhia por ele e a
            # tela de escolha nunca chegava a existir.
            #
            # Restaurar não é ativar. O roteiro dele volta carregado — a decisão
            # 6(a), de não precisar de comando após reiniciar, continua de pé —
            # mas a gravação começa parada, com o seletor à mostra.
            preparado = self.prepare_operational_script(behavior, script)
            # Deixa o teleprompter com OS BEATS do roteiro dele prontos. Sem
            # isto, `self._script` continuaria no canônico do disco e o painel
            # mostraria o roteiro errado assim que o dono mandasse começar.
            # `_load_script` popula beats e script; quem liga a gravação é
            # `activate`, e ela não é chamada aqui de propósito.
            self._load_script(preparado)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "[Roteiro] não restaurei o roteiro no boot: {}", type(exc).__name__
            )
            return None
        restaurado = str((preparado or {}).get("id") or self._script.get("id") or "")
        logger.info(
            "[Roteiro] roteiro restaurado no boot (parado, aguardando escolha): {}",
            restaurado,
        )
        return restaurado or None

    async def activate(
        self, profile: str = "roteiro", *, new_session: bool = True,
        script: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._lock:
            if new_session or not self._beats:
                self._load_script(script)
                self.session_id = uuid.uuid4().hex
                self.current_beat_id = self._script.get("start") or (
                    next(iter(self._beats), None)
                )
                self.route = [self.current_beat_id] if self.current_beat_id else []
                self.completed_lines.clear()
                self.history.clear()
                self._pending_audio.clear()
                self._active_audio.clear()
                self._turn_takes.clear()
                self._turn_groups.clear()
                self._forced_line_by_turn.clear()
                self._line_memory_marks.clear()
                self._last_celia_bundle = None
                self._last_failed_celia_text = ""
                self._completed_matches.clear()
                self._game_results.clear()
                self.markers.clear()
                self._recording_started_at = time.monotonic()
                self._take_sequence = 0
                self.criador = {"partial": "", "last_final": "", "utterance_id": ""}
                self.criador_match = {"matched": False, "score": 0, "beat_id": None, "source": None}
                self.celia = {"state": "idle", "last_text": "", "speech_id": ""}
            self.profile = profile
            self.active = True
            self._sync_operational_position()
            self.status = "LIVE • CONECTADO"
            self.last_event = {"type": "roteiro.started", "timestamp": time.time()}
            self._write_session_log("session.started", {"script_id": self._script.get("id")})
            if new_session or not self.take_id:
                self._begin_take("session_start")
        await self._notify()
        return self.snapshot()

    async def deactivate(self, profile: str = "chat") -> dict[str, Any]:
        async with self._lock:
            self.active = False
            self.profile = profile
            self.status = "INATIVO"
            self.last_event = {"type": "roteiro.stopped", "timestamp": time.time()}
            self._write_session_log("session.stopped")
        await self._notify()
        return self.snapshot()

    async def _advance(self, beat_id: str, reason: str, *, force: bool = False) -> None:
        if beat_id not in self._beats or (not force and not self.auto_follow):
            return
        if beat_id == self.current_beat_id:
            return
        self.current_beat_id = beat_id
        self._sync_operational_position()
        self.route.append(beat_id)
        self.last_event = {
            "type": "roteiro.beat_changed",
            "beat_id": beat_id,
            "reason": reason,
            "timestamp": time.time(),
        }
        self._write_session_log("beat.changed", {"beat_id": beat_id, "reason": reason})

    async def _observe_criador_final(self, text: str, utterance_id: str = "") -> None:
        clean = " ".join(str(text or "").split())[:800]
        if not clean:
            return
        memory_mark = self._agent_dialogue_length()
        self.criador = {
            "partial": "",
            "last_final": clean,
            "utterance_id": utterance_id or self.criador.get("utterance_id", ""),
        }
        self.criador_match = self._classify_script_text(clean)
        line_match = self._mark_script_line(clean, "Criador")
        if line_match:
            self._line_memory_marks[str(line_match["line_id"])] = memory_mark
            self.criador_match = {**self.criador_match, **line_match, "source": "line"}
        self.status = "CRIADOR • FINAL"
        self.history.append(
            {
                "speaker": "Criador",
                "text": clean,
                "timestamp": time.time(),
                "kind": "final",
                "script_match": bool(self.criador_match.get("matched")),
            }
        )
        self._write_session_log(
            "criador.final",
            {
                "text": clean,
                "utterance_id": utterance_id,
                "script_match": dict(self.criador_match),
            },
        )
        current = self._beats.get(self.current_beat_id or "")
        candidates = list((current or {}).get("next") or [])
        structured_branch = (current or {}).get("branch_source") == "game_result"
        if (
            line_match
            and line_match["beat_id"] == self.current_beat_id
            and self._required_lines_complete(self.current_beat_id)
            and not self._awaiting_celia_response(self.current_beat_id)
            and not structured_branch
        ):
            if candidates:
                await self._advance(candidates[0], "criador_required_lines")
                current = self._beats.get(self.current_beat_id or "")
                candidates = list((current or {}).get("next") or [])
        if line_match and line_match["beat_id"] in candidates:
            await self._advance(line_match["beat_id"], "criador_script_line")
            current = self._beats.get(self.current_beat_id or "")
            candidates = list((current or {}).get("next") or [])
        text_words = _words(clean)
        best: tuple[int, str] | None = None
        for candidate_id in candidates:
            beat = self._beats.get(candidate_id, {})
            match_words = _words(" ".join(beat.get("match") or []))
            score = len(text_words & match_words)
            if score and (best is None or score > best[0]):
                best = (score, candidate_id)
        if best and not structured_branch:
            await self._advance(best[1], "criador_context")

    async def _observe_celia(
        self, text: str, speech_id: str = "", forced_line_id: str = ""
    ) -> None:
        clean = " ".join(str(text or "").split())[:800]
        if not clean or clean.casefold() in {"thinking...", "thinking"}:
            return
        # This method is reached only after a successful playback FINISH.
        # START may already expose the same text in the panel, but it is not a
        # completed line yet. Completion identity is the speech lifecycle, not
        # a text comparison.
        is_new_text = True
        self.celia = {
            "state": "falando",
            "last_text": clean,
            "speech_id": speech_id or "",
        }
        if forced_line_id and forced_line_id in {
            f"{beat_id}:{index}"
            for beat_id, beat in self._beats.items()
            for index, _line in enumerate(beat.get("lines") or [])
        }:
            beat_id = forced_line_id.rsplit(":", 1)[0]
            self.completed_lines.add(forced_line_id)
            line_match = {
                "matched": True,
                "score": 999,
                "line_id": forced_line_id,
                "beat_id": beat_id,
            }
        else:
            line_match = self._mark_script_line(clean, "Célia")
        if is_new_text:
            self._write_session_log("celia.approved", {"text": clean, "speech_id": speech_id})
        self.status = "CÉLIA • FALANDO"
        if is_new_text:
            self.history.append(
                {
                    "speaker": "Célia",
                    "text": clean,
                    "timestamp": time.time(),
                    "kind": "approved",
                    "script_match": bool(line_match),
                }
            )
        current = self._beats.get(self.current_beat_id or "") or {}
        if (
            line_match
            and line_match["beat_id"] == self.current_beat_id
            and self._awaiting_celia_response(self.current_beat_id)
            and self._required_lines_complete(self.current_beat_id)
            and current.get("branch_source") != "game_result"
        ):
            next_ids = [item for item in current.get("next") or [] if item in self._beats]
            if next_ids:
                await self._advance(next_ids[0], "celia_response")
        elif line_match and line_match["beat_id"] in (current.get("next") or []):
            await self._advance(line_match["beat_id"], "celia_script_line")

    async def observe_message(self, message: dict[str, Any]) -> None:
        """Consume existing broadcast events; no new producer is created."""
        if not isinstance(message, dict):
            return
        message_type = str(message.get("type") or "")
        if message_type == "profile.changed":
            profile = str(message.get("profile") or "chat")
            if profile in SCRIPT_PROFILES:
                from .continuous_behavior import continuous_behavior
                source_script = None
                if profile == "gravacao" and continuous_behavior.script.beats:
                    source_script = continuous_behavior.script.export()
                script = self.prepare_operational_script(continuous_behavior, source_script)
                needs_reload = (
                    not self.active
                    or self._script.get("id") != script.get("id")
                    or self._script.get("version") != script.get("version")
                )
                if needs_reload:
                    await self.activate(profile, new_session=True, script=script)
                else:
                    self.profile = profile
                    self.active = True
                    self._sync_operational_position()
                    await self._notify()
            elif self.active:
                await self.deactivate(profile)
            self._sync_operational_position()
            return
        if not self.active:
            return
        async with self._lock:
            if message_type == "speech.started":
                utterance_id = str(message.get("utterance_id") or "").strip()
                if utterance_id:
                    self._turn_takes[utterance_id] = self.take_id
                    while len(self._turn_takes) > 128:
                        self._turn_takes.pop(next(iter(self._turn_takes)))
            elif message_type == "asr-partial":
                utterance_id = str(message.get("utterance_id") or "").strip()
                if utterance_id and utterance_id not in self._turn_takes:
                    self._turn_takes[utterance_id] = self.take_id
                if utterance_id and self._turn_takes.get(utterance_id) != self.take_id:
                    self._write_session_log(
                        "criador.partial_stale",
                        {"utterance_id": utterance_id, "take_id": self._turn_takes.get(utterance_id)},
                    )
                    return
                self.criador["partial"] = str(message.get("text") or "")[:400]
                self.criador["utterance_id"] = str(
                    message.get("utterance_id") or self.criador.get("utterance_id", "")
                )
                self.criador_match = self._classify_script_text(self.criador["partial"])
                self.status = "CRIADOR • OUVINDO"
                self._write_session_log(
                    "criador.partial",
                    {"text": self.criador["partial"], "script_match": dict(self.criador_match)},
                )
            elif message_type == "user-input-transcription":
                utterance_id = str(message.get("utterance_id") or "").strip()
                if utterance_id and utterance_id not in self._turn_takes:
                    self._turn_takes[utterance_id] = self.take_id
                if utterance_id and self._turn_takes.get(utterance_id) != self.take_id:
                    self._write_session_log(
                        "criador.final_stale",
                        {"utterance_id": utterance_id, "take_id": self._turn_takes.get(utterance_id)},
                    )
                    return
                await self._observe_criador_final(
                    str(message.get("text") or ""), utterance_id=utterance_id
                )
            elif message_type == "audio":
                display = message.get("display_text") or {}
                if isinstance(display, dict):
                    if message.get("tts_error"):
                        self.celia["state"] = "tts_error"
                        self._write_session_log(
                            "celia.tts_failed",
                            {"error": str(message.get("tts_error"))},
                        )
                    elif message.get("speech_id"):
                        speech_id = str(message["speech_id"])
                        turn_id = str(
                            message.get("turn_id") or message.get("utterance_id") or ""
                        ).strip()
                        audio_take = self._turn_takes.get(turn_id, self.take_id)
                        entry = {
                            "text": str(display.get("text") or ""),
                            "turn_id": turn_id,
                            "take_id": audio_take,
                            "payload": self._audio_payload_snapshot(message),
                            "no_complete": bool(message.get("roteiro_no_complete")),
                            "recovery_mode": str(message.get("recovery_mode") or ""),
                        }
                        self._pending_audio[speech_id] = entry
                        if turn_id:
                            group = self._turn_group(turn_id, audio_take)
                            group["entries"][speech_id] = entry
                        while len(self._pending_audio) > 64:
                            self._pending_audio.pop(next(iter(self._pending_audio)))
            elif message_type == "celia.tts.turn-sealed":
                turn_id = str(message.get("turn_id") or "").strip()
                if turn_id:
                    group = self._turn_group(
                        turn_id, self._turn_takes.get(turn_id, self.take_id)
                    )
                    group["speech_ids"] = [
                        str(item) for item in (message.get("speech_ids") or []) if item
                    ]
                    group["sealed"] = True
                    await self._maybe_complete_turn_group(turn_id)
            elif message_type == "audio-playback-started":
                speech_id = str(message.get("speech_id") or "")
                if speech_id not in self._active_audio:
                    entry = self._pending_audio.pop(speech_id, None)
                    if entry is not None and entry.get("take_id") == self.take_id:
                        self._active_audio[speech_id] = entry
                        self._start_celia_playback(speech_id, entry)
                    elif entry is not None:
                        self._write_session_log(
                            "celia.playback_start_stale",
                            {
                                "speech_id": speech_id,
                                "take_id": entry.get("take_id"),
                                "current_take_id": self.take_id,
                            },
                        )
            elif message_type == "audio-playback-finished":
                speech_id = str(message.get("speech_id") or "")
                entry = self._active_audio.pop(speech_id, None)
                status = str(message.get("status") or "ended").strip().casefold()
                successful = status in {
                    "ended", "finished", "complete", "completed", "ok", "success"
                }
                if entry is not None and entry.get("turn_id"):
                    turn_id = str(entry["turn_id"])
                    group = self._turn_group(turn_id, str(entry.get("take_id") or ""))
                    group["entries"][speech_id] = entry
                    group["finished"][speech_id] = status
                    await self._maybe_complete_turn_group(turn_id)
                elif entry is not None and entry.get("take_id") == self.take_id and successful:
                    if not entry.get("no_complete"):
                        await self._observe_celia(entry.get("text", ""), speech_id)
                    self._last_celia_bundle = {
                        "text": str(entry.get("text") or ""),
                        "payloads": [dict(entry.get("payload") or {})],
                        "speech_ids": [speech_id],
                        "turn_id": "",
                    }
                    self.celia["state"] = "idle"
                    self.status = "LIVE • CONECTADO"
                    self._write_session_log(
                        "celia.playback_finished",
                        {"speech_id": speech_id, "status": status, "take_id": self.take_id},
                    )
                elif entry is not None:
                    self.celia["state"] = "idle"
                    self.status = "LIVE • FALA RECUPERÁVEL"
                    self._write_session_log(
                        "celia.playback_discarded",
                        {"speech_id": speech_id, "status": status, "take_id": entry.get("take_id", "")},
                    )
            elif message_type == "celia.text.approved":
                return
            elif message_type in {"celia.tts.failed", "playback.cancel", "frontend-playback-cancelled"}:
                speech_id = str(message.get("speech_id") or "")
                entry = self._pending_audio.pop(speech_id, None)
                entry = self._active_audio.pop(speech_id, None) or entry
                turn_id = str(
                    message.get("turn_id")
                    or (entry or {}).get("turn_id")
                    or ""
                ).strip()
                failure_status = (
                    "cancelled"
                    if message_type in {"playback.cancel", "frontend-playback-cancelled"}
                    else "error"
                )
                if turn_id:
                    group = self._turn_group(
                        turn_id, str((entry or {}).get("take_id") or self.take_id)
                    )
                    if entry:
                        group["entries"][speech_id] = entry
                    group["finished"][speech_id] = failure_status
                    await self._maybe_complete_turn_group(turn_id)
                # A failed phrase must remain recoverable and never mark the line done.
                failed_text = " ".join(str(message.get("text") or "").split())
                if failed_text:
                    self._last_failed_celia_text = failed_text[:800]
                self.celia["state"] = "idle"
                self.celia.pop("pending_text", None)
                self.status = "LIVE • FALA RECUPERÁVEL"
                self._write_session_log(
                    "celia.tts_failed",
                    {
                        "speech_id": speech_id,
                        "turn_id": turn_id,
                        "status": failure_status,
                        "error": str(message.get("error") or failure_status),
                    },
                )
            elif message_type == "celia.tts.ready":
                # `/bridge/process` returns the WAV to its caller instead of
                # sending a browser playback ACK. Mark synthesis ready so the
                # phone panel does not remain stuck at "preparando_audio".
                self.celia["state"] = "audio_pronto"
                self.status = "CÉLIA • ÁUDIO PRONTO"
                self._write_session_log("celia.tts_ready")
            elif message_type == "celia.state":
                state = str(message.get("state") or "idle").strip().lower()
                self.celia["state"] = state
                self.status = f"CÉLIA • {state.upper()}"
                self._write_session_log("celia.state", {"state": state})
            elif message_type in {"frontend-playback-complete", "playback-completed"}:
                self.celia["state"] = "idle"
                self.status = "LIVE • CONECTADO"
                self._write_session_log("celia.playback_completed")
            elif message_type == "game.result":
                await self._apply_game_result(message)
            else:
                return
            self.last_event = {"type": message_type, "timestamp": time.time()}
        await self._notify()

    async def _apply_game_result(self, message: dict[str, Any]) -> None:
        winner = _norm(
            message.get("winner_controller")
            or message.get("winner")
            or message.get("result")
        )
        game = _canonical_game_id(message.get("game"))
        match_id = str(message.get("match_id") or "").strip()
        current = self._beats.get(self.current_beat_id or "", {})
        expected_game = _canonical_game_id(current.get("result_game"))
        if current.get("branch_source") != "game_result" or not expected_game:
            self._write_session_log(
                "game.result_ignored",
                {
                    "game": game,
                    "match_id": match_id,
                    "current_beat": self.current_beat_id,
                    "reason": "current_beat_not_waiting_for_result",
                },
            )
            return
        if game != expected_game:
            self._write_session_log(
                "game.result_wrong_game",
                {
                    "game": game,
                    "expected_game": expected_game,
                    "match_id": match_id,
                    "current_beat": self.current_beat_id,
                },
            )
            return
        if match_id and match_id in self._completed_matches:
            self._write_session_log(
                "game.result_duplicate",
                {"game": game, "winner": winner, "match_id": match_id},
            )
            return
        if match_id:
            self._completed_matches.add(match_id)
        self._game_results[game] = {
            "game": game,
            "match_id": match_id,
            "winner": winner,
            "result": message.get("result"),
            "turn": message.get("turn"),
            "reason": message.get("reason"),
            "score": dict(message.get("score") or {}),
        }
        self._write_session_log(
            "game.result",
            {
                "game": game,
                "winner": winner,
                "match_id": match_id,
                "turn": message.get("turn"),
                "reason": message.get("reason"),
                "raw": dict(message),
            },
        )
        branches = current.get("branches") or {}
        if (
            winner in {"draw", "empate", "tie", "1/2-1/2"}
            or "draw" in winner
            or "empate" in winner
        ):
            branch_key = "draw"
        elif winner in {"celia", "célia"} or "celia" in winner or "célia" in winner:
            branch_key = "celia"
        elif winner in {"criador", "human", "humano", "chat"}:
            branch_key = "criador"
        else:
            # Legacy logs without winner_controller are kept readable, but no
            # symbol/color is guessed as Célia anymore.
            branch_key = "criador"
        branch_id = branches.get(branch_key)
        if branch_id:
            await self._advance(
                branch_id, f"game_result:{game or 'unknown'}", force=True
            )

    async def _broadcast_recovery_payloads(
        self,
        payloads: list[dict[str, Any]],
        *,
        mode: str,
        forced_line_id: str = "",
        complete_line: bool = True,
    ) -> bool:
        if not payloads:
            return False
        from ..celia_singleton import CeliaSingleton

        singleton = CeliaSingleton.get_instance()
        turn_id = f"roteiro-{mode}-{uuid.uuid4().hex[:12]}"
        self._turn_takes[turn_id] = self.take_id
        if forced_line_id:
            self._forced_line_by_turn[turn_id] = forced_line_id
        speech_ids: list[str] = []
        for index, source in enumerate(payloads):
            payload = dict(source)
            payload["type"] = "audio"
            payload.pop("cancel_token", None)
            speech_id = f"{turn_id}-{index + 1}-{uuid.uuid4().hex[:8]}"
            speech_ids.append(speech_id)
            payload.update(
                {
                    "speech_id": speech_id,
                    "turn_id": turn_id,
                    "utterance_id": turn_id,
                    "recovery_mode": mode,
                    "roteiro_no_complete": not complete_line,
                }
            )
            await singleton.broadcast(payload)
        await singleton.broadcast(
            {
                "type": "celia.tts.turn-sealed",
                "turn_id": turn_id,
                "speech_ids": speech_ids,
                "recovery_mode": mode,
            }
        )
        self._write_session_log(
            "recovery.dispatched",
            {
                "mode": mode,
                "turn_id": turn_id,
                "speech_ids": speech_ids,
                "forced_line_id": forced_line_id,
            },
        )
        return True

    async def _synthesize_recovery_payload(self, text: str) -> dict[str, Any] | None:
        clean = " ".join(str(text or "").split())
        if not clean:
            return None
        from ..celia_singleton import CeliaSingleton
        from ..bridge_routes import _generate_bridge_audio
        from ..agent.output_types import DisplayText
        from ..utils.stream_audio import prepare_audio_payload

        singleton = CeliaSingleton.get_instance()
        context = singleton.context
        if context is None or context.tts_engine is None:
            return None
        audio_path = await _generate_bridge_audio(
            context.tts_engine,
            clean,
            f"roteiro_recovery_{uuid.uuid4().hex}",
            None,
        )
        if not audio_path:
            return None
        return prepare_audio_payload(
            audio_path=audio_path,
            display_text=DisplayText(
                text=clean,
                name=context.character_config.character_name or "Célia",
            ),
            actions=None,
        )

    async def _play_expected_celia(self, *, regenerate: bool = False, explicit: bool = False) -> bool:
        reply = self.planned_celia_reply()
        if not reply and explicit:
            reply = self.explicit_celia_reply()
        if not reply:
            return False
        payload = None
        if not regenerate:
            try:
                from .roteiro_audio_pack import roteiro_audio_pack
                from ..agent.output_types import DisplayText
                from ..utils.stream_audio import prepare_audio_payload

                asset = roteiro_audio_pack.lookup(
                    script_id=str(self._script.get("id") or ""),
                    script_version=int(self._script.get("version") or 1),
                    beat_id=reply["beat_id"],
                    line_id=reply["line_id"],
                    text=reply["text"],
                )
                if asset and asset.get("audio_path"):
                    payload = prepare_audio_payload(
                        audio_path=asset["audio_path"],
                        display_text=DisplayText(text=reply["text"], name="Célia"),
                        actions=None,
                    )
                    payload["bank_playback"] = True
            except Exception as exc:
                logger.debug(
                    "Roteiro prepared recovery unavailable: {}", type(exc).__name__
                )
        if payload is None:
            payload = await self._synthesize_recovery_payload(
                reply.get("tts_text") or reply["text"]
            )
            if payload is not None:
                payload["display_text"] = {
                    "text": reply["text"],
                    "name": "Célia",
                }
        if payload is None:
            return False
        return await self._broadcast_recovery_payloads(
            [payload],
            mode="regenerate" if regenerate else "expected",
            forced_line_id=reply["line_id"],
            complete_line=True,
        )

    async def _replay_last_celia_audio(self) -> bool:
        bundle = self._last_celia_bundle or {}
        payloads = [
            dict(payload)
            for payload in (bundle.get("payloads") or [])
            if isinstance(payload, dict) and payload.get("audio")
        ]
        if not payloads:
            return False
        reply = self.planned_celia_reply()
        forced_line_id = str((reply or {}).get("line_id") or "")
        return await self._broadcast_recovery_payloads(
            payloads,
            mode="replay",
            forced_line_id=forced_line_id,
            complete_line=bool(forced_line_id),
        )

    async def _regenerate_last_celia_voice(self) -> bool:
        pending = self.planned_celia_reply()
        if pending:
            return await self._play_expected_celia(regenerate=True)
        bundle = self._last_celia_bundle or {}
        text = " ".join(
            str(bundle.get("text") or self._last_failed_celia_text or "").split()
        )
        if not text:
            return False
        payload = await self._synthesize_recovery_payload(text)
        if payload is None:
            return False
        payload["display_text"] = {"text": text, "name": "Célia"}
        return await self._broadcast_recovery_payloads(
            [payload],
            mode="regenerate",
            complete_line=False,
        )

    async def _request_new_celia_response(self) -> bool:
        from ..celia_singleton import CeliaSingleton

        singleton = CeliaSingleton.get_instance()
        last_criador = str(self.criador.get("last_final") or "").strip()
        if not last_criador:
            for item in reversed(self.history):
                if item.get("speaker") == "Criador" and item.get("text"):
                    last_criador = str(item["text"]).strip()
                    break
        if not last_criador:
            return False
        # The old spoken answer remains in durable/session history for audit,
        # but it must leave active RAM before a replacement is generated.
        self._remove_last_agent_assistant("new_response")
        turn_id = f"roteiro-new-{uuid.uuid4().hex[:12]}"
        self._turn_takes[turn_id] = self.take_id
        reply = self.planned_celia_reply()
        if reply:
            self._forced_line_by_turn[turn_id] = reply["line_id"]
        prompt = (
            "Direção de gravação: responda novamente, com texto novo e natural, "
            "à última fala do Criador. Não explique a instrução nem mencione bastidores. "
            f"Última fala: {last_criador}"
        )
        accepted = await singleton.submit_input(
            prompt,
            role="source",
            speaker_name="Direção",
            origin="system",
            behavior_observed=True,
            turn_id=turn_id,
            scripted_reply=None,
        )
        if accepted:
            self.status = "CÉLIA • NOVA RESPOSTA"
            self._write_session_log(
                "recovery.new_response",
                {"turn_id": turn_id, "forced_line_id": (reply or {}).get("line_id")},
            )
        return bool(accepted)

    async def _force_stop_playback(self) -> None:
        """Cancel only Roteiro audio without turning the hotkey into user speech."""
        speech_ids = sorted(
            {
                speech_id
                for speech_id in [
                    self.celia.get("speech_id"),
                    *self._pending_audio,
                    *self._active_audio,
                ]
                if isinstance(speech_id, str) and speech_id
            }
        )
        self._pending_audio.clear()
        self._active_audio.clear()
        self.celia["state"] = "force_stop"
        self.status = "FORCE-STOP • PLAYBACK CANCELADO"
        self._write_session_log(
            "playback.force_stop",
            {"speech_ids": speech_ids, "source": "roteiro.ctrl_p"},
        )

        # The authenticated Roteiro socket asks the existing browser bridge to
        # pause the actual <audio>; no transcript or conversation input is sent.
        from ..celia_singleton import CeliaSingleton

        singleton = CeliaSingleton.get_instance()
        clear_pending = getattr(singleton, "clear_pending_inputs", None)
        if callable(clear_pending):
            clear_pending()
        for speech_id in speech_ids:
            try:
                await singleton.broadcast(
                    {
                        "type": "playback.cancel",
                        "speech_id": speech_id,
                        "source": "roteiro.force_stop",
                    }
                )
            except Exception as exc:  # noqa: BLE001 - a stale browser must not block stop.
                logger.warning("Roteiro force-stop playback broadcast failed: {}", type(exc).__name__)
        # The main Célia frontend already understands interrupt-signal and stops
        # its current HTMLAudioElement. Send it even if the conversation task has
        # already finished; singleton.interrupt() only broadcasts this while a
        # processing task is still alive.
        try:
            await singleton.broadcast(
                {"type": "interrupt-signal", "text": "roteiro-force-stop"}
            )
        except Exception as exc:  # noqa: BLE001 - backend cancellation still proceeds.
            logger.warning("Roteiro force-stop legacy playback signal failed: {}", type(exc).__name__)
        try:
            await singleton.interrupt(role="browser", force=True)
        except Exception as exc:  # noqa: BLE001 - playback cancellation above remains useful.
            logger.warning("Roteiro force-stop conversation cancellation failed: {}", type(exc).__name__)

    async def control(self, data: dict[str, Any]) -> dict[str, Any]:
        action = str(data.get("action") or "").strip().casefold()

        # Cancellation broadcasts re-enter observe_message through the singleton.
        # Never hold the Roteiro lock while waiting for that broadcast, otherwise
        # VOLTAR/REFAZER can deadlock against its own cancellation event.
        needs_cancel = action in {
            "force_stop",
            "back",
            "back_line",
            "back_to_word",
            "retake",
            "retake_self",
            "repeat_celia",
            "regenerate_voice",
            "new_response",
            "play_expected",
        } or (action == "reset" and data.get("confirm") is True)
        if needs_cancel and self.active and self.profile == "roteiro":
            await self._force_stop_playback()

        # Recovery playback re-enters observe_message through singleton.broadcast,
        # so it must never run while the Roteiro lock is held.
        recovery_ok: bool | None = None
        if action == "repeat_celia":
            recovery_ok = await self._replay_last_celia_audio()
            if not recovery_ok:
                recovery_ok = await self._play_expected_celia(regenerate=False, explicit=True)
        elif action == "regenerate_voice":
            recovery_ok = await self._regenerate_last_celia_voice()
        elif action == "new_response":
            recovery_ok = await self._request_new_celia_response()
        elif action == "play_expected":
            recovery_ok = await self._play_expected_celia(regenerate=False, explicit=True)
        if recovery_ok is not None:
            # Motivo da falha no log: sem ele "indisponível" escondia se faltava fala ou se faltou áudio.
            reason = ""
            if not recovery_ok and action in {"play_expected", "repeat_celia"}:
                reason = (
                    "no_celia_line_in_beat"
                    if self.explicit_celia_reply() is None
                    else "tts_or_playback_unavailable"
                )
            self.status = (
                "LIVE • RECUPERAÇÃO DISPARADA"
                if recovery_ok
                else (
                    "LIVE • ESTE BLOCO NÃO TEM FALA DA CÉLIA"
                    if reason == "no_celia_line_in_beat"
                    else "LIVE • RECUPERAÇÃO INDISPONÍVEL"
                )
            )
            self.last_event = {
                "type": "roteiro.recovery",
                "action": action,
                "ok": recovery_ok,
                **({"reason": reason} if reason else {}),
                "timestamp": time.time(),
            }
            self._write_session_log(
                "control.recovery",
                {"action": action, "ok": recovery_ok, **({"reason": reason} if reason else {})},
            )
            await self._notify()
            return self.snapshot()

        if action in {"game_pause", "game_resume", "game_retry_commentary", "game_skip_commentary"}:
            from ..games_routes import issue_game_control

            mapped = {
                "game_pause": "pause",
                "game_resume": "resume",
                "game_retry_commentary": "retry_commentary",
                "game_skip_commentary": "skip_commentary",
            }[action]
            command = issue_game_control(
                mapped,
                game_id=str(data.get("game") or "").strip() or None,
                match_id=str(data.get("match_id") or "").strip() or None,
            )
            self.status = (
                f"JOGO • {mapped.upper()}"
                if command
                else "JOGO • SESSÃO INDISPONÍVEL"
            )
            self.last_event = {
                "type": "roteiro.game_control",
                "action": mapped,
                "command": command,
                "timestamp": time.time(),
            }
            self._write_session_log(
                "control.game", {"action": mapped, "command": command or {}}
            )
            await self._notify()
            return self.snapshot()

        if action == "open_game":
            from ..games_routes import open_game_browser

            try:
                result = open_game_browser(
                    str(data.get("game") or ""),
                    str(data.get("mode") or "criador-vs-celia"),
                )
            except ValueError:
                result = {"ok": False, "error": "invalid_game"}
            self.status = (
                "JOGO • ABERTURA ACEITA PELO NAVEGADOR"
                if result.get("ok")
                else "JOGO • NÃO FOI POSSÍVEL ABRIR"
            )
            self.last_event = {
                "type": "roteiro.open_game",
                "result": result,
                "timestamp": time.time(),
            }
            self._write_session_log("control.open_game", result)
            await self._notify()
            return self.snapshot()

        async with self._lock:
            if action == "live":
                self.auto_follow = True
            elif action == "manual":
                self.auto_follow = False
            elif action == "next":
                self.auto_follow = False
                current = self._beats.get(self.current_beat_id or "", {})
                next_ids = [
                    item for item in current.get("next") or [] if item in self._beats
                ]
                if next_ids:
                    await self._advance(next_ids[0], "manual_next", force=True)
            elif action == "forward_line":
                self.auto_follow = False
                current_id = str(self.current_beat_id or "")
                current = self._beats.get(current_id, {})
                # Avanço manual é literal e sequencial: pinta a linha visível
                # atual como concluída, seja Criador, Célia ou um lembrete de AÇÃO.
                target = next(
                    (
                        (index, line, f"{current_id}:{index}")
                        for index, line in enumerate(current.get("lines") or [])
                        if isinstance(line, dict)
                        and line.get("text")
                        and f"{current_id}:{index}" not in self.completed_lines
                    ),
                    None,
                )
                next_ids = [
                    item for item in current.get("next") or [] if item in self._beats
                ]
                if target is not None:
                    # Um clique, um efeito visível: a linha recém-concluída tem
                    # de ser pintada neste snapshot. Trocar de beat no mesmo
                    # clique apagaria a cor antes de o Criador vê-la.
                    _index, _line, line_id = target
                    self.completed_lines.add(line_id)
                    self.status = f"LIVE • AVANÇOU FALA {line_id}"
                elif next_ids and current.get("branch_source") != "game_result":
                    await self._advance(next_ids[0], "manual_forward_line", force=True)
                    self.status = f"LIVE • AVANÇOU PARA {next_ids[0]}"
                else:
                    self.status = "LIVE • NENHUMA FALA SEGUINTE"
            elif action in {"back", "back_line"}:
                self.auto_follow = False
                requested_line = str(data.get("line_id") or "").strip()
                target = next(
                    (item for item in self._route_lines() if item[3] == requested_line),
                    None,
                ) if requested_line else self._last_completed_line()
                if target is not None:
                    self._reopen_from_line(target, "back_line")
                    self.criador = {"partial": "", "last_final": "", "utterance_id": ""}
                    self.criador_match = {
                        "matched": False, "score": 0, "beat_id": None, "source": None
                    }
                    self._begin_take("back_line")
                    self.status = f"LIVE • VOLTOU PARA {target[3]}"
                else:
                    self.status = "LIVE • NENHUMA FALA ANTERIOR"
            elif action in {"retake", "retake_self"}:
                self.auto_follow = False
                target = self._last_completed_line("Criador")
                if target is None:
                    current = self._beats.get(self.current_beat_id or "") or {}
                    for index, line in enumerate(current.get("lines") or []):
                        if (
                            isinstance(line, dict)
                            and _norm(line.get("speaker")) == "criador"
                            and f"{self.current_beat_id}:{index}" not in self.completed_lines
                        ):
                            target = (
                                str(self.current_beat_id),
                                index,
                                line,
                                f"{self.current_beat_id}:{index}",
                            )
                            break
                if target is not None:
                    self._reopen_from_line(target, "retake_self")
                self.criador = {"partial": "", "last_final": "", "utterance_id": ""}
                self.criador_match = {
                    "matched": False, "score": 0, "beat_id": None, "source": None
                }
                self._begin_take("retake_self")
                self.status = (
                    f"LIVE • REFAZER {target[3]}"
                    if target is not None
                    else "LIVE • NOVA TOMADA"
                )
            elif action == "back_to_word":
                self.auto_follow = False
                anchor = str(data.get("anchor") or "").strip()
                target = self._resolve_word_anchor(anchor)
                if target is not None:
                    self._reopen_from_line(target, f"word:{anchor[:60]}")
                    self.criador = {"partial": "", "last_final": "", "utterance_id": ""}
                    self.criador_match = {
                        "matched": False, "score": 0, "beat_id": None, "source": None
                    }
                    self._begin_take("back_to_word")
                    self.status = f"LIVE • ÂNCORA {target[3]}"
                else:
                    self.status = "LIVE • ÂNCORA NÃO ENCONTRADA"
            elif action in {"mark_good", "mark_discarded"}:
                self.take_mark = "good" if action == "mark_good" else "discarded"
                self.status = (
                    "LIVE • TOMADA BOA"
                    if self.take_mark == "good"
                    else "LIVE • TOMADA DESCARTADA"
                )
                self._add_marker("take_mark", self.take_mark)
                self._write_session_log(
                    "take.marked", {"take_id": self.take_id, "mark": self.take_mark}
                )
            elif action == "marker":
                marker_kind = str(data.get("kind") or "manual").strip()[:40] or "manual"
                marker_note = str(data.get("note") or "").strip()[:160]
                self._add_marker(marker_kind, marker_note)
                self.status = "LIVE • MARCADOR REGISTRADO"
            elif action in {"branch", "result"} and data.get("confirm") is True:
                self.auto_follow = False
                branch_id = str(data.get("beat_id") or data.get("branch_id") or "")
                if branch_id in self._beats:
                    await self._advance(branch_id, "manual_branch", force=True)
            elif action == "reset" and data.get("confirm") is True:
                self._load_script(self._script)
                self.current_beat_id = self._script.get("start") or next(
                    iter(self._beats), None
                )
                self.route = [self.current_beat_id] if self.current_beat_id else []
                self.completed_lines.clear()
                self.history.clear()
                self._turn_takes.clear()
                self._turn_groups.clear()
                self._forced_line_by_turn.clear()
                self._line_memory_marks.clear()
                self._last_celia_bundle = None
                self._last_failed_celia_text = ""
                self._completed_matches.clear()
                self._game_results.clear()
                self.markers.clear()
                self._recording_started_at = time.monotonic()
                self._begin_take("reset")
                self._sync_operational_position()
            elif action == "force_stop":
                if not self.active or self.profile != "roteiro":
                    return self.snapshot()
            else:
                # Ação que o runtime carregado não conhece sai registrada: sem
                # isso, um botão novo no celular contra um processo antigo em
                # memória falha em silêncio absoluto e parece bug de pintura.
                self._write_session_log("control.unknown_action", {"action": action})
                return self.snapshot()
            self.last_event = {
                "type": "roteiro.force_stop" if action == "force_stop" else "roteiro.control",
                "action": action,
                "timestamp": time.time(),
            }
            self._write_session_log("control", {"action": action, "payload": data})
        await self._notify()
        return self.snapshot()


roteiro_live = RoteiroLiveRuntime()
