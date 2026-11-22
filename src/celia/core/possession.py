"""Possessão da Célia, v2: a persona continua sendo quem conversa; um agente externo (Claude Code / Codex) é o dono do CORPO no jogo.

Contrato (2026-10-21): minha fala segue direto para a persona (personalidade, voz, memória, legenda, interrupção); o agente recebe a mesma fala como
EVENTO espelhado, executa ações tipadas no corpo, verifica os resultados e publica uma FICHA compacta que a persona usa. O agente NÃO escreve nem
reproduz falas finais. A conversa nunca espera uma nova inferência do agente.

Este módulo é o núcleo, sem rede: lease único, fila de eventos, versões separadas (conversa / comando do corpo / época do mundo), trancas
determinísticas (parar, silêncio, encerrar), ficha, diário. Quem toca no jogo, na voz e no daemon é `possession_routes.py`.

Versões que NÃO se misturam:
  * ``conversation_turn``  — dono: a persona (obsolescência da resposta conversacional). Aqui nada a toca.
  * ``command_version``    — só sobe com PARAR/CANCELAR do dono ou quando o agente substitui o próprio plano. Uma interjeição ("hum") ou uma nova
                             fala qualquer NÃO invalida tarefa corporal válida.
  * ``world_epoch``        — a época da máquina de estados da série (daemon); ação planejada em outra época é recusada.

Regras por construção: um controlador por vez; só o dono autenticado (sem eco) vira evento; "acabou a gravação/o vídeo" encerra NA CENTRAL; parar corta
corpo e fala na hora; fim não reinicia; reiniciar a central também encerra.
"""

from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from .series_intent import _NEGATION, _QUOTE, _clean, _fold

CONTROLLERS = frozenset({"claude", "codex"})
MAX_EVENTS = 300
MAX_TEXT = 600
COALESCE_MS = 150  # fragments of one thought arrive as separate finals; they reach the agent as ONE event. REPLAY of the real journals (tools/possession_replay.py): consecutive finals
#                   are >= 1.4 s apart (p50 3.3 s), so 150-300 ms merges NOTHING and every ms of window is pure latency: the smallest value of the range is the default.
COALESCE_MAX_MS = 750
_BACKCHANNEL = frozenset({"hum", "hm", "hmm", "ahn", "aham", "uhum", "uhu", "ah", "oh", "uh", "uf", "uff", "hein", "ue", "eita", "e", "ta", "ok", "certo", "beleza", "ne", "tipo"})
_FILL = r"(?:(?:celia|selia|cela)[ ]+)?(?:(?:entao|ai|pronto|bom|olha|gente|pessoal)[ ]+)*"
END_RE = re.compile(
    rf"^{_FILL}(?:acabou(?: o| a)? (?:video|gravacao)|acabou o episodio|encerrad[oa](?: o| a)? (?:video|gravacao)|encerra(?:r)?(?: o| a)? (?:video|gravacao)|"
    rf"(?:pode )?encerrar(?: a)? possessao|(?:pode )?encerra(?: a)? possessao|fim da possessao|fim da gravacao)(?: pessoal| gente| por hoje)?$")
STOP_RE = re.compile(rf"^{_FILL}(?:para|pare|cancela|cancele)(?: tudo)?(?: agora| ja| imediatamente)?$")
SILENCE_RE = re.compile(
    rf"^{_FILL}(?:(?:fica|ficar|fique) (?:quieta|quieto|em silencio|calada)|silencio|cala (?:a )?boca|(?:para|pare) de falar|chega de falar|"
    rf"(?:pode )?(?:ficar )?(?:um pouco )?(?:quieta|calada)|so um minuto de silencio)(?: um pouquinho| um pouco| por favor| agora)?$")


HOLD_RE = re.compile(rf"^{_FILL}(?:(?:fica|fique|ficar) (?:ai |aqui |ali )?parad[ao]|(?:para|pare) de (?:andar|se mexer|circular|caminhar))(?: um pouquinho| um pouco| por favor)?$")
RELEASE_RE = re.compile(rf"^{_FILL}(?:pode )?(?:(?:se mexer|andar|circular|caminhar)(?: de novo| novamente)?|(?:volta|volte|voltar) a (?:andar|se mexer|circular|caminhar)|(?:solta|solte|libera|libere) (?:o )?corpo(?: dela)?)$")
WIGGLE_RE = re.compile(rf"^{_FILL}(?:se mexe(?: um pouquinho| um pouco| um instante)?(?: pra eu ver| para eu ver| pra mim| para mim)?|anda um pouquinho|anda um pouco|da uma andadinha|da uma mexida|mostra que (?:voce )?(?:se )?mexe)$")
JUMP_RE = re.compile(rf"^{_FILL}(?:pula|pule|da um pulo|salta)(?: ai| aqui)?(?: agora| um pouquinho| por favor)?$")
SNEAK_RE = re.compile(rf"^{_FILL}(?:agacha|abaixa|fica agachada|fica agachado)(?: ai| aqui)?(?: agora| um pouquinho| por favor)?$")
STAND_RE = re.compile(rf"^{_FILL}(?:levanta|fica de pe|se levanta)(?: ai| aqui)?(?: agora| por favor)?$")


class PossessionError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class Verdict:
    """What the voice/text routes must do with one owner utterance."""

    consumed: bool  # True only for "end" and "silence": the persona must not answer them. "stop" is acted on here AND still reaches the series' emergency stop
    kind: str = "pass"  # pass | speech | backchannel | end | stop | silence | ignored
    reason: str = ""
    seq: int | None = None
    command_version: int | None = None


def classify(text: str) -> str:
    """Pure: ``end`` | ``stop`` | ``silence`` | ``backchannel`` | ``speech``. Whole-utterance only; quotes, negations and mentions are plain speech."""
    folded = _clean(_fold(text)).strip(" .!?")
    if not folded or _QUOTE.search(folded) or _NEGATION.search(folded):
        return "speech"
    if END_RE.match(folded):
        return "end"
    if STOP_RE.match(folded):
        return "stop"
    if SILENCE_RE.match(folded):
        return "silence"
    if HOLD_RE.match(folded):
        return "hold"
    if RELEASE_RE.match(folded):
        return "release"
    if WIGGLE_RE.match(folded):
        return "wiggle"
    if JUMP_RE.match(folded):
        return "jump"
    if SNEAK_RE.match(folded):
        return "sneak"
    if STAND_RE.match(folded):
        return "stand"
    words = folded.split()
    if 0 < len(words) <= 2 and all(w in _BACKCHANNEL for w in words):
        return "backchannel"  # "hum", "tá", "é, né" ... never a new instruction
    return "speech"


def _percentile(values: list[float], pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(round((len(ordered) - 1) * pct)))], 1)


def merge_fragments(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Pure. Consecutive ``owner_speech`` finals are ONE thought said in pieces: join them. Backchannels, controls and game events stay as they are."""
    merged: list[dict[str, Any]] = []
    for event in events:
        previous = merged[-1] if merged else None
        if event["type"] == "owner_speech" and previous is not None and previous["type"] == "owner_speech":
            previous["text"] = (previous["text"] + " " + event["text"]).strip()[:MAX_TEXT * 3]
            previous["fragments"] = previous.get("fragments", 1) + 1
            previous["seq"] = event["seq"]
            previous["utterance_id"] = event.get("utterance_id", "")
            previous["t"] = event["t"]
            previous["command_version"] = event["command_version"]
            continue
        merged.append(dict(event))
    for event in merged:
        if event["type"] == "owner_speech":
            event.setdefault("fragments", 1)
    return merged


@dataclass
class _State:
    active: bool = False
    controller: str = ""
    session_id: str = ""
    lease: str = ""
    started_at: float = 0.0
    command_version: int = 0
    world_epoch: int | None = None
    silenced: bool = False
    quest: dict[str, Any] = field(default_factory=lambda: {"objective": "", "miniquest": "", "next": "", "pending": [], "obstacles": []})
    ended_reason: str = ""


class Possession:
    def __init__(self, root: Path, *, clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "sessions").mkdir(exist_ok=True)
        self.clock, self.mono = clock, mono
        self.s = _State()
        self.events: deque[dict[str, Any]] = deque(maxlen=MAX_EVENTS)
        self._posted_keys: dict[str, int] = {}  # producer retries reuse one queue sequence for this possession session
        self._action_in_flight = False
        self.seq = 0
        self._wake: asyncio.Event | None = None  # created lazily inside the running loop
        self._ingest_mono: dict[int, float] = {}
        self._last_owner_mono: float | None = None
        self.latencies: list[float] = []
        self._delivered: list[int] = []  # owner-speech seqs the agent has READ but not yet answered with an action (latency is measured from the earliest)
        self._last_perception: dict[str, Any] | None = None  # the previous body perception, to confirm physical effects by difference
        self.policy: dict[str, Any] | None = None  # what the series allows the body NOW (phase, initiative levels, holds): read from the daemon, never written by the agent
        self.policy_mono: float | None = None
        self.shots: list[tuple[str, float]] = []  # (sha256, mono) of the owner-screen captures made in this session: framing 'confirmed' must point at one of them
        self.sheet: dict[str, Any] | None = None
        self.sheet_meta: dict[str, Any] = {}
        self.sheet_mono: float | None = None
        self.sheet_version = 0
        self.on_stop: list[Callable[[str], Awaitable[None]]] = []  # side effects owned by the routes (halt the body, release it)
        self.on_urgent: list[Callable[[str], Awaitable[None]]] = []  # "para tudo agora" / "silêncio": act on the body or the voice at once
        self._recover()

    # ---- persistence --------------------------------------------------------------------------------------------------------------------
    @property
    def _state_path(self) -> Path:
        return self.root / "state.json"

    def _journal_path(self) -> Path | None:
        return self.root / "sessions" / f"{self.s.session_id}.jsonl" if self.s.session_id else None

    def _save(self) -> None:
        data = {k: v for k, v in self.s.__dict__.items() if k != "lease"}
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self._state_path)

    def note(self, kind: str, /, **fields: Any) -> None:
        path = self._journal_path()
        if path is None:
            return
        row = {"t": round(self.clock(), 3), "m": round(self.mono(), 4), "kind": kind, **fields}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _recover(self) -> None:
        """A restart of the central never leaves a possession half alive and never restarts one by itself."""
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, dict) and data.get("active"):
            self.s.session_id = str(data.get("session_id") or "")
            self.s.controller = str(data.get("controller") or "")
            self.note("ended", reason="central_restart", controller=self.s.controller)
            self.s = _State(ended_reason="central_restart", session_id=self.s.session_id)
            self._save()

    # ---- lifecycle ----------------------------------------------------------------------------------------------------------------------
    def active(self) -> bool:
        return self.s.active

    def is_silenced(self) -> bool:
        return self.s.active and self.s.silenced

    def start(self, controller: str, *, takeover: bool = False, world_epoch: int | None = None) -> dict[str, Any]:
        controller = (controller or "").strip().lower()
        if controller not in CONTROLLERS:
            raise PossessionError("bad_controller", f"controller must be one of {sorted(CONTROLLERS)}")
        if self.s.active:
            if not takeover:
                raise PossessionError("already_possessed", f"already possessed by {self.s.controller}")
            self.note("takeover", previous=self.s.controller, controller=controller)
        stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime(self.clock()))
        self.s = _State(active=True, controller=controller, session_id=f"{stamp}-{secrets.token_hex(3)}", lease=secrets.token_urlsafe(24),
                        started_at=self.clock(), world_epoch=world_epoch)
        # A previous session's HTTP action may still be awaiting the body after STOP.
        # Keep the global body-action gate closed until that request's finally block releases it.
        self.events.clear()
        self._posted_keys.clear()
        self._last_owner_mono = self.mono()
        self._ingest_mono.clear()
        self.latencies.clear()
        self.sheet, self.sheet_meta, self.sheet_mono = None, {}, None
        self.policy, self.policy_mono, self.shots = None, None, []
        self.seq = 0
        self._save()
        self.note("started", controller=controller, takeover=takeover, world_epoch=world_epoch, external_assistance=True,
                  contract="persona_speaks_agent_controls_body")
        return {"session_id": self.s.session_id, "lease": self.s.lease, "controller": controller, "command_version": self.s.command_version,
                "world_epoch": world_epoch}

    async def stop(self, reason: str = "owner") -> dict[str, Any]:
        """Idempotent. Cancels the pending, releases the control, never restarts."""
        if not self.s.active:
            return {"stopped": False, "reason": "not_active"}
        self.s.command_version += 1  # everything still in flight is now stale
        self.s.active = False
        self.s.silenced = False
        self.s.ended_reason = reason
        self.sheet, self.sheet_meta, self.sheet_mono = None, {}, None  # a dead session's facts must not reach the persona
        self.note("ended", reason=reason, controller=self.s.controller, latency_ms=self.latency_stats())
        self.s.lease = ""
        self._save()
        for hook in list(self.on_stop):
            try:
                await hook(reason)
            except Exception as exc:  # a failing side effect never keeps the possession alive
                self.note("stop_hook_failed", error=f"{type(exc).__name__}: {exc}"[:200])
        self._poke()
        return {"stopped": True, "reason": reason, "session_id": self.s.session_id}

    # ---- authorisation ------------------------------------------------------------------------------------------------------------------
    def check(self, lease: str | None) -> None:
        if not self.s.active:
            raise PossessionError("not_active", "the possession is over; do not act")
        if not lease or not secrets.compare_digest(str(lease), self.s.lease):
            raise PossessionError("bad_lease", "another controller holds this possession")

    def authorize_act(self, lease: str | None, *, cmd: int | None) -> None:
        """An action planned under an older ``command_version`` (before a stop/cancel or the agent's own replacement) is refused."""
        self.check(lease)
        if cmd is not None and cmd < self.s.command_version:
            raise PossessionError("stale", f"the command was cancelled (command_version {self.s.command_version}); re-plan from `next`")

    def begin_action(self, lease: str | None) -> None:
        self.check(lease)
        if self._action_in_flight:
            raise PossessionError("action_in_flight", "one body action is already executing")
        self._action_in_flight = True

    def finish_action(self) -> None:
        self._action_in_flight = False

    def supersede(self, lease: str | None, reason: str = "new_plan") -> int:
        """The agent replaces its OWN earlier plan: older commands become stale. Owner interjections never do this."""
        self.check(lease)
        self.s.command_version += 1
        self.note("supersede", reason=reason, command_version=self.s.command_version)
        return self.s.command_version

    def note_local_owner_command(self, *, utterance_id: str, intent: str) -> int:
        """Invalidate plans prepared before a locally dispatched, authenticated owner body command."""
        self.s.command_version += 1
        self.note("owner_local_command", utterance_id=str(utterance_id)[:80], intent=str(intent)[:48],
                  command_version=self.s.command_version)
        return self.s.command_version

    def note_world_epoch(self, epoch: int | None) -> None:
        if epoch is not None and epoch != self.s.world_epoch:
            previous = self.s.world_epoch
            self.note("world_epoch", previous=previous, epoch=epoch)
            self.s.world_epoch = epoch
            self.s.command_version += 1  # any plan prepared against the old world is stale immediately
            self._save()
            if previous is not None and self.sheet:
                from .possession_sheet import VOLATILE

                dropped = [name for name in list(self.sheet) if name in VOLATILE]
                for name in dropped:
                    self.sheet.pop(name, None)  # facts about NOW belong to the old world: they do not survive into the new one
                self.sheet_meta["world_epoch"] = epoch
                if dropped:
                    self.note("sheet_invalidated", reason="world_epoch_changed", dropped=sorted(dropped))

    def note_policy(self, policy: dict[str, Any] | None) -> None:
        if isinstance(policy, dict):
            self.policy, self.policy_mono = policy, self.mono()

    def note_shot(self, sha256: str | None) -> None:
        if sha256:
            self.shots = (self.shots + [(str(sha256), self.mono())])[-6:]

    def _recent_shot(self, evidence: str, max_age_s: float = 20.0) -> bool:
        ref = evidence.lower().removeprefix("shot:").strip()
        return len(ref) >= 6 and any(sha.startswith(ref) and self.mono() - at <= max_age_s for sha, at in self.shots)

    def mark_replied(self, reply_to: int | None) -> float | None:
        """Latency: owner utterance heard -> first action of the agent for it (NOT speech: the agent has no voice)."""
        started = self._ingest_mono.pop(reply_to, None) if reply_to is not None else None
        if started is None:
            return None
        ms = round((self.mono() - started) * 1000.0, 1)
        self.latencies.append(ms)
        return ms

    def claim_reply(self, reply_to: int | None = None) -> float | None:
        """Latency without trusting the agent to pass ``reply_to``: the first body action after it READ owner speech answers the earliest unanswered one."""
        seqs = self._delivered
        self._delivered = []
        if reply_to is None and not seqs:
            return None
        target = reply_to if reply_to is not None else min(seqs)
        measured = self.mark_replied(target)
        for other in seqs:
            self._ingest_mono.pop(other, None)  # the rest of that burst is answered by the same action
        return measured

    def latency_stats(self) -> dict[str, Any]:
        return {"n": len(self.latencies), "p50_ms": _percentile(self.latencies, 0.5), "p95_ms": _percentile(self.latencies, 0.95),
                "max_ms": max(self.latencies) if self.latencies else None}

    # ---- the owner's voice --------------------------------------------------------------------------------------------------------------
    def ingest(self, text: str, *, source: str, authenticated_owner: bool, self_echo: bool, utterance_id: str | None = None,
               turn_id: str | None = None) -> Verdict:
        """MIRROR of the owner's speech to the body agent. It consumes only the deterministic controls; everything else keeps going to the persona."""
        if not self.s.active:
            return Verdict(False)
        text = (text or "").strip()[:MAX_TEXT]
        if not text:
            return Verdict(False, "ignored", "empty")
        if not authenticated_owner or self_echo or source not in {"microphone", "browser_text"}:
            reason = "self_echo" if self_echo else ("not_the_authenticated_owner" if not authenticated_owner else "unknown_source")
            self.note("ignored", reason=reason, text=text[:120], source=source)  # chat, game text and her own voice are never orders for the body
            return Verdict(False, "ignored", reason)
        kind = classify(text)
        self.seq += 1
        seq = self.seq
        if kind == "end":
            self.note("owner_end", text=text, source=source, utterance_id=utterance_id or "")
            return Verdict(True, "end", "end_phrase", seq, self.s.command_version)
        if kind in {"stop", "hold"}:
            self.s.command_version += 1  # a stop / "fica parada" kills every pending body command (a hold also binds the agent until the release)
        elif kind == "silence":
            self.s.silenced = True
        elif kind == "speech":
            self.s.silenced = False
        self._last_owner_mono = self.mono()
        if kind in {"speech", "stop", "hold", "release", "wiggle", "jump", "sneak", "stand"}:
            self._ingest_mono[seq] = self.mono()
            if len(self._ingest_mono) > 200:
                self._ingest_mono.pop(next(iter(self._ingest_mono)))
        event_type = {"speech": "owner_speech", "stop": "owner_stop", "silence": "owner_silence", "backchannel": "owner_backchannel", "hold": "owner_hold",
                      "release": "owner_release", "wiggle": "owner_wiggle", "jump": "owner_jump", "sneak": "owner_sneak", "stand": "owner_stand"}[kind]
        event = {"seq": seq, "t": round(self.clock(), 3), "_m": self.mono(), "type": event_type, "text": text, "source": source,
                 "command_version": self.s.command_version, "utterance_id": utterance_id or "", "turn_id": turn_id or ""}
        self.events.append(event)
        self.note(event_type, seq=seq, text=text, command_version=self.s.command_version, source=source, utterance_id=utterance_id or "", turn_id=turn_id or "")
        self._poke()
        # silence is consumed (the persona must not answer a request to be quiet); STOP keeps flowing so the series' deterministic emergency stop still runs
        return Verdict(kind == "silence", kind, "", seq, self.s.command_version)

    def post_event(self, kind: str, /, dedupe_key: str | None = None, **fields: Any) -> int:
        """Internal observations (game events, action results) the agent should know about. Never an order."""
        if dedupe_key and dedupe_key in self._posted_keys:
            return self._posted_keys[dedupe_key]
        self.seq += 1
        event = {**fields, "seq": self.seq, "t": round(self.clock(), 3), "_m": self.mono(), "type": kind, "command_version": self.s.command_version}
        self.events.append(event)
        if dedupe_key:
            self._posted_keys[dedupe_key] = self.seq
            if len(self._posted_keys) > MAX_EVENTS * 4:
                self._posted_keys.pop(next(iter(self._posted_keys)))
        self.note(kind, seq=self.seq, **fields)
        self._poke()
        return self.seq

    # ---- long poll ----------------------------------------------------------------------------------------------------------------------
    def _poke(self) -> None:
        if self._wake is not None:
            self._wake.set()

    def _since(self, since: int) -> list[dict[str, Any]]:
        return [dict(e) for e in self.events if e["seq"] > since]

    async def next_events(self, lease: str | None, since: int, wait: float, coalesce_ms: float = COALESCE_MS) -> dict[str, Any]:
        self.check(lease)
        if self._wake is None:
            self._wake = asyncio.Event()
        deadline = self.mono() + max(0.0, min(float(wait), 25.0))
        while True:
            # Clear first, then inspect the queue. Clearing after the inspection can
            # erase a wake posted in between and strand a waiter until its timeout.
            self._wake.clear()
            found = self._since(since)
            if found or not self.s.active:
                break
            remaining = deadline - self.mono()
            if remaining <= 0:
                break
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=remaining)
            except asyncio.TimeoutError:
                break
        urgent = any(e["type"] in {"owner_stop", "owner_silence", "owner_hold"} for e in found)
        if found and coalesce_ms > 0 and not urgent and any(e["type"] == "owner_speech" for e in found):
            # a thought said in fragments: wait until the owner has been quiet for a moment (a controls/stop never waits)
            hard = self.mono() + COALESCE_MAX_MS / 1000.0
            for _ in range(int(COALESCE_MAX_MS / 20) + 2):  # bounded by iterations too: a frozen test clock must never spin forever
                if not self.s.active:
                    break
                newest = max((e["_m"] for e in self.events if e["seq"] > since), default=self.mono())
                if self.mono() - newest >= coalesce_ms / 1000.0 or self.mono() >= hard:
                    break
                await asyncio.sleep(0.02)
            found = self._since(since)
        self._delivered.extend(e["seq"] for e in found if e["type"] == "owner_speech" and e["seq"] not in self._delivered)
        events = merge_fragments(found)
        for event in events:
            event.pop("_m", None)
        idle = None if self._last_owner_mono is None else round(self.mono() - self._last_owner_mono, 1)
        quest = self.s.quest
        # the agent sees its OWN open step on every poll: an empty poll with a pending step is the cue to take the initiative
        reply = {"active": self.s.active, "command_version": self.s.command_version, "world_epoch": self.s.world_epoch, "silenced": self.s.silenced,
                 "events": events, "last_seq": self.seq, "ended_reason": None if self.s.active else self.s.ended_reason,
                 "owner_quiet_s": idle, "quest": {"miniquest": quest["miniquest"], "next": quest["next"]}, "sheet": self.sheet_status(), "policy": self.policy}
        if self.s.active and not events and quest["next"] and "progression" in ((self.policy or {}).get("initiative_levels") or ["progression"]):
            reply["initiative_cue"] = f"no new words from the owner; open step: {quest['next']}. Do ONE useful action toward it, check the result, then update quest.next and the sheet."
        return reply

    # ---- the compact sheet --------------------------------------------------------------------------------------------------------------
    def set_sheet(self, lease: str | None, raw: dict[str, Any]) -> dict[str, Any]:
        """Atomic replacement: the new sheet is the whole truth; the old one is gone (nothing is stacked)."""
        from .possession_sheet import normalize

        self.check(lease)
        clean = normalize(raw if isinstance(raw, dict) else {})
        now, previous = self.mono(), (self.sheet or {})
        downgraded = False
        framing = clean.get("framing")
        if isinstance(framing, dict) and framing.get("status") == "confirmed" and not self._recent_shot(framing.get("evidence", "")):
            clean["framing"] = {"status": "unknown", "evidence": ""}  # 'confirmed' needs an identifiable, recent capture of the owner's screen
            downgraded = True
        for name, entry in clean.items():
            if not isinstance(entry, dict) or ("v" not in entry and name != "framing"):
                continue
            old = previous.get(name) if isinstance(previous.get(name), dict) else None
            same = old is not None and all(old.get(k) == entry.get(k) for k in (("v", "src") if name != "framing" else ("status", "evidence")))
            # an UNCHANGED field keeps its original observation time: re-sending an old sheet never renews it; a changed one is as old as the agent says
            entry["_t"] = old.get("_t", now) if same else now - float(entry.get("age_s", 0.0))
        self.sheet_version += 1
        self.sheet, self.sheet_mono = clean, now
        self.sheet_meta = {"session_id": self.s.session_id, "world_epoch": self.s.world_epoch, "version": self.sheet_version}
        fields = sorted(k for k in clean if k != "scene_version")
        self.note("sheet", version=self.sheet_version, fields=fields, chars=len(self.sheet_block() or ""), block=self.sheet_block(), framing_downgraded=downgraded)
        return {"version": self.sheet_version, "fields": fields, "chars": len(self.sheet_block() or ""), "framing_downgraded": downgraded}

    def sheet_block(self) -> str:
        """The block for the persona's prompt: empty when no possession, no sheet, or nothing valid is left."""
        from .possession_sheet import render

        if not self.s.active or not self.sheet or self.sheet_mono is None:
            return ""
        meta = dict(self.sheet_meta)
        meta["policy"] = self.policy
        meta["policy_age_s"] = None if self.policy_mono is None else self.mono() - self.policy_mono
        return render(self.sheet, age_s=self.mono() - self.sheet_mono, meta=meta, now=self.mono())

    def sheet_status(self) -> dict[str, Any]:
        age = None if self.sheet_mono is None else round(self.mono() - self.sheet_mono, 1)
        return {"version": self.sheet_version, "age_s": age, "valid_chars": len(self.sheet_block() or "")}

    # ---- the simple session state -------------------------------------------------------------------------------------------------------
    def set_quest(self, lease: str | None, **fields: Any) -> dict[str, Any]:
        self.check(lease)
        quest = self.s.quest
        for key in ("objective", "miniquest", "next"):
            if isinstance(fields.get(key), str):
                quest[key] = fields[key].strip()[:240]
        for key in ("pending", "obstacles"):
            if isinstance(fields.get(key), list):
                quest[key] = [str(v).strip()[:160] for v in fields[key] if str(v).strip()][:8]
        self._save()
        self.note("quest", **quest)
        return dict(quest)

    def public_state(self) -> dict[str, Any]:
        s = self.s
        return {"active": s.active, "controller": s.controller, "session_id": s.session_id, "command_version": s.command_version, "world_epoch": s.world_epoch,
                "policy": self.policy,
                "silenced": s.silenced, "quest": dict(s.quest), "latency": self.latency_stats(), "last_seq": self.seq, "ended_reason": s.ended_reason or None,
                "sheet": self.sheet_status(), "external_assistance": True}


_instance: Possession | None = None


def get_possession() -> Possession:
    """The one possession of this process (state under var/possession)."""
    global _instance
    if _instance is None:
        from .raizes import VAR

        _instance = Possession(Path(VAR) / "possession")
    return _instance


def is_possessed() -> bool:
    """Fast, exception-safe: is an external agent the owner of the body right now?"""
    try:
        return get_possession().active()
    except Exception:
        return False


def is_silenced() -> bool:
    """The owner asked for silence during a possession: the persona's own initiative must stay quiet until he speaks again."""
    try:
        return get_possession().is_silenced()
    except Exception:
        return False


def sheet_prompt_block() -> str:
    """The single, replaced block the persona's prompt gets each turn (empty = nothing valid)."""
    try:
        return get_possession().sheet_block()
    except Exception:
        return ""
