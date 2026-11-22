"""Possession routes (loopback only), v2: the PERSONA speaks; the external agent owns the BODY and keeps the persona informed with a compact sheet.

  POST /bridge/possession/start    take the lease and the body (one controller at a time)
  POST /bridge/possession/stop     end it (no lease needed: the owner or the terminal can always stop)
  GET  /bridge/possession/next     long poll: the owner's words (fragments merged), stop/silence controls, game events, the open step, the sheet status
  POST /bridge/possession/act      ONE typed body action (daemon re-validates the allowlist) or a named shortcut; carries the command version and world epoch
  POST /bridge/possession/sheet    publish the compact body sheet (REPLACES the previous one; the persona reads it next turn)
  GET  /bridge/possession/state    compact facts: possession + quest + body perception
  POST /bridge/possession/shot     the OWNER's screen, as a small JPEG
  POST /bridge/possession/quest    the simple session state
  POST /bridge/possession/log      a decision note for the journal
  GET  /bridge/possession/persona  the persona text (for context only: the agent never speaks as her)
  GET  /bridge/possession/stats    latency and counts

There is NO say endpoint: the agent has no authority over Célia's final speech. The lease travels in ``X-Possession-Lease``; the Jev daemon credential never
leaves the central.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Body, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .core import possession_io as io
from .core.possession import Possession, PossessionError, get_possession
from .core.possession_trace import PossessionTrace, observed_effect

LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}
SHORTCUTS = {"come_here", "face_owner", "stop"}
_trace: PossessionTrace | None = None


def tracer() -> PossessionTrace:
    global _trace
    poss = get_possession()
    if _trace is None or _trace.root.parent != poss.root:
        _trace = PossessionTrace(poss.root)
    return _trace


def _loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host.lower() not in LOOPBACK:
        raise HTTPException(status_code=403, detail="possession is local only")


def _fail(exc: PossessionError) -> HTTPException:
    status = {"bad_lease": 403, "not_active": 409, "already_possessed": 409, "stale": 409,
              "action_in_flight": 409}.get(exc.code, 400)
    return HTTPException(status_code=status, detail={"code": exc.code, "detail": exc.detail})


def _action_outcome_class(result: dict[str, Any], effect: dict[str, Any]) -> str:
    """Classify the executor's report without upgrading acceptance into a physical success."""
    status = str(result.get("status") or "").lower()
    error = str(result.get("error") or "").lower()
    if status in {"cancelled", "canceled"} or "cancelled" in error or "canceled" in error:
        return "cancelled"
    if status == "uncertain":
        return "infrastructure_uncertain"
    if (error.startswith("unavailable:") or error in {"credential_unavailable", "bad_answer"}
            or error in {"body_unavailable", "audit_unavailable", "result_audit_unavailable"}
            or error.startswith("http_5")):
        return "infrastructure_error"
    if status == "blocked":
        return "blocked"
    if (error.startswith(("http_400", "http_403", "http_409"))
            or error in {"body_held_by_owner", "body_possessed_by_external_controller", "no_fresh_state", "owner_not_visible"}):
        return "precondition_refused"
    if not result.get("ok"):
        return "executor_failed" if status in {"failed", "error"} else "refused_or_unclassified"
    if effect.get("confirmed") is True:
        return "effect_observed"
    return "accepted_unverified"


class StartBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    controller: str = Field(max_length=12)
    takeover: bool = False


class ActBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: dict[str, Any] | None = None
    shortcut: str | None = Field(default=None, max_length=16)
    cmd: int | None = None  # the command_version the plan was made under
    world_epoch: int | None = None
    supersede: bool = False  # this action REPLACES the agent's earlier plan (older in-flight commands become stale)
    reply_to: int | None = None
    utterance_id: str | None = Field(default=None, max_length=64)


class QuestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    objective: str | None = None
    miniquest: str | None = None
    next: str | None = None
    pending: list[str] | None = None
    obstacles: list[str] | None = None


class LogBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=600)
    kind: str = Field(default="agent_note", pattern=r"^[a-z_]{3,24}$")


class ShotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active_window: bool = False


def wire_side_effects(poss: Possession) -> None:
    """Hooks that make "para tudo agora", "silêncio" and the end of the session real: they touch the body and the voice, not a model."""

    async def urgent(kind: str) -> None:
        if kind == "stop":  # stop the body AND cut what she is saying
            await asyncio.to_thread(io.daemon_call, "POST", "/possession/halt", {}, 4.0)
        speech_id = io.current_speech_id()  # "silêncio" cuts the voice only: the body keeps following
        if speech_id:
            await asyncio.to_thread(io.cancel_playback, speech_id)

    async def release(reason: str) -> None:
        await urgent("stop")
        await asyncio.to_thread(io.daemon_call, "POST", "/possession/hold", {"on": False}, 6.0)

    if urgent not in poss.on_urgent:
        poss.on_urgent.append(urgent)
    if release not in poss.on_stop:
        poss.on_stop.append(release)


async def mirror_owner_text(text: str, *, source: str, authenticated_owner: bool, self_echo: bool, utterance_id: str | None = None,
                            turn_id: str | None = None) -> bool:
    """Called by the voice and typed-text routes. The text ALWAYS keeps going to the persona, except the deterministic controls (end / stop / silence),
    which the central handles itself. Returns True when the utterance was consumed as a control."""
    poss = get_possession()
    if not poss.active():
        return False
    verdict = poss.ingest(text, source=source, authenticated_owner=authenticated_owner, self_echo=self_echo, utterance_id=utterance_id, turn_id=turn_id)
    if verdict.kind not in {"pass", "ignored"}:
        tracer().mark(poss.s.session_id, "owner_final", utterance_id=utterance_id, turn_id=turn_id, kind=verdict.kind, seq=verdict.seq)
    if verdict.kind == "end":
        asyncio.create_task(poss.stop("owner_end_phrase"))
    elif verdict.kind in {"stop", "silence"}:
        for hook in list(poss.on_urgent):
            asyncio.create_task(hook(verdict.kind))
    return verdict.consumed


async def dispatch_local_owner_body_command(text: str, *, source: str, authenticated_owner: bool, self_echo: bool,
                                            utterance_id: str | None, persona_submit: Any) -> bool:
    """Run one short, explicit owner body command locally during possession.

    On a successful local dispatch, the possession queue receives only its correlated result,
    never a second executable owner_* imperative. Busy/refused dispatch results are delivered
    as facts so the agent can decide whether and how to continue.
    """
    poss = get_possession()
    if not poss.active() or not authenticated_owner or self_echo or source not in {"microphone", "browser_text"}:
        return False
    from .core.possession import classify

    # Keep STOP, silence, hold/release and episode controls on their established priority path.
    if classify(text) not in {"jump", "sneak", "stand", "wiggle", "release", "speech"}:
        return False
    from .core.series_intent import detect_series_intent
    ingress_at = time.monotonic()

    allowed = {"companion.jump", "companion.crouch", "companion.stand", "companion.wiggle",
               "companion.return", "companion.follow", "companion.release"}
    found = detect_series_intent(text)
    if len(found) != 1 or not found[0].should_act or found[0].intent not in allowed:
        return False
    item = found[0]
    from .core.minecraft_series import get_series_center

    center = get_series_center()
    command_id = utterance_id or f"local-{uuid.uuid4().hex}"
    possession_session = poss.s.session_id
    triage = await center.triage(text, utterance_id=command_id, source=source,
                                 authenticated_owner=authenticated_owner, self_echo=self_echo)
    if not poss.active() or poss.s.session_id != possession_session:
        return True  # the instruction belonged to a possession that ended while triage was reading the phase
    if not triage.consume:
        return False
    if triage.reason in {"duplicate_utterance", "duplicate_command_window"}:
        return True
    command_version = poss.note_local_owner_command(utterance_id=command_id, intent=item.intent)

    async def execute_and_report() -> None:
        try:
            outcome = await center.execute(triage, utterance_id=command_id)
            if not poss.active() or poss.s.session_id != possession_session:
                return
            first = outcome.results[0] if outcome.results else {}
            status = ("completed" if first.get("ok") and first.get("verified", True) else
                      "effect_unconfirmed" if first.get("ok") else "refused_or_failed") if outcome.results else "refused"
            facts = {"intent": item.intent, "params": item.params, "results": outcome.results[:2],
                     "status": status, "duration_ms": round((time.monotonic() - ingress_at) * 1000.0, 1),
                     "original_text": str(text)[:240], "source": source, "command_version": command_version}
            key = f"owner-body:{poss.s.session_id}:{command_id}"
            poss.post_event("owner_body_command_result", dedupe_key=key, source="local_series_executor",
                            event_key=key, utterance_id=command_id, facts=facts)
            try:
                from .core.cognitive_loop import cognitive_loop
                cognitive_loop.note_minecraft_event(key, {"events": ["owner_body_command_result"],
                                                           "action": item.intent, "status": facts["status"],
                                                           "action_id": str((outcome.results[0] if outcome.results else {}).get("request_id") or "")[:64],
                                                           "effect": str((outcome.results[0] if outcome.results else {}).get("code") or "")[:80]})
                cognitive_loop.request_think("owner_body_command_result")
            except Exception:
                pass
            if outcome.brief and persona_submit is not None:
                await persona_submit(outcome.brief)
        except Exception as exc:
            key = f"owner-body:{poss.s.session_id}:{command_id}"
            poss.post_event("owner_body_command_result", dedupe_key=key, source="local_series_executor",
                            event_key=key, utterance_id=command_id, facts={"intent": item.intent,
                            "status": "infrastructure_error", "duration_ms": round((time.monotonic() - ingress_at) * 1000.0, 1),
                            "error": type(exc).__name__, "original_text": str(text)[:240]})

    asyncio.create_task(execute_and_report())
    return True


def init_possession_routes() -> APIRouter:
    # the loopback check is a router dependency: it runs BEFORE any request body is validated, so a stranger learns nothing from a 422
    router = APIRouter(prefix="/bridge/possession", tags=["possession"], dependencies=[Depends(_loopback)])
    poss = get_possession()
    wire_side_effects(poss)

    async def _perceive(timeout: float = 4.0) -> dict[str, Any]:
        seen = await asyncio.to_thread(io.daemon_call, "GET", "/possession/perceive", None, timeout)
        if seen.get("ok"):
            poss.note_policy(seen.get("policy"))
            poss.note_world_epoch(seen.get("epoch"))
        return seen

    @router.post("/start")
    async def start(request: Request, body: StartBody):
        seen = await _perceive(6.0)
        try:
            started = poss.start(body.controller, takeover=body.takeover, world_epoch=seen.get("epoch"))
        except PossessionError as exc:
            raise _fail(exc)
        held = await asyncio.to_thread(io.daemon_call, "POST", "/possession/hold", {"on": True}, 8.0)
        poss.note("body_hold", ok=bool(held.get("ok")), error=held.get("error"), detail=held.get("detail"), series_state=seen.get("series"),
                  world_epoch=seen.get("epoch"), gameplay_started=False)  # starting a possession never releases the world: the series state is untouched
        return {**started, "body": "held" if held.get("ok") else "unavailable", "body_error": held.get("error"), "series_state": seen.get("series"),
                "rules": "the persona speaks; you own the body; only the authenticated owner's voice is an order; stop on 'acabou a gravação'; never auto-restart"}

    @router.post("/stop")
    async def stop(request: Request):
        return await poss.stop("owner_endpoint")

    @router.get("/next")
    async def next_events(request: Request, since: int = 0, wait: float = 20.0, coalesce_ms: float = 150.0, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.check(x_possession_lease)
            if poss.policy_mono is None or poss.mono() - poss.policy_mono > 5.0:
                await _perceive(3.0)  # keep the phase / permissions the agent is told about fresh (one light read per poll at most every 5 s)
            reply = await poss.next_events(x_possession_lease, since, wait, coalesce_ms=max(0.0, min(coalesce_ms, 750.0)))
        except PossessionError as exc:
            raise _fail(exc)
        for event in reply["events"]:
            if event.get("type", "").startswith("owner_"):
                tracer().mark(poss.s.session_id, "agent_polled", seq=event.get("seq"), utterance_id=event.get("utterance_id"), turn_id=event.get("turn_id"))
        return reply

    async def _act(action: dict[str, Any], action_id: str, epoch: int | None) -> dict[str, Any]:
        return await asyncio.to_thread(io.daemon_call, "POST", "/body/act", {"action": action, "action_id": action_id, "world_epoch": epoch}, 45.0)

    async def _run_shortcut(name: str, action_id: str, epoch: int | None) -> tuple[dict[str, Any], dict[str, Any]]:
        if name == "stop":
            return await asyncio.to_thread(io.daemon_call, "POST", "/possession/halt", {}, 5.0), {"type": "stop"}
        seen = await _perceive(5.0)
        perception = seen.get("perception") or {}
        owner = perception.get("owner_position")
        if name == "face_owner":
            if not isinstance(owner, dict):
                return {"ok": False, "status": "failed", "error": "owner_not_visible", "perception": perception}, {"type": "look"}
            action = {"type": "look", "params": {"x": owner["x"], "y": owner["y"] + 1.62, "z": owner["z"]}, "timeout_seconds": 5}
            return await _act(action, action_id, epoch), action
        player = os.getenv("CELIA_MC_PLAYER", "PlayerOne") or "PlayerOne"
        action = {"type": "follow", "params": {"player": player, "distance": 3}, "timeout_seconds": 25}
        return await _act(action, action_id, epoch), action

    @router.post("/act")
    async def act(request: Request, body: ActBody, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.authorize_act(x_possession_lease, cmd=body.cmd)
        except PossessionError as exc:
            poss.note("act_refused", code=exc.code)
            raise _fail(exc)
        if (body.action is None) == (body.shortcut is None):
            raise HTTPException(status_code=400, detail={"code": "give_action_or_shortcut", "detail": "exactly one of action / shortcut"})
        if body.shortcut is not None and body.shortcut not in SHORTCUTS:
            raise HTTPException(status_code=400, detail={"code": "bad_shortcut", "detail": f"one of {sorted(SHORTCUTS)}"})
        if body.supersede:
            await asyncio.to_thread(io.daemon_call, "POST", "/possession/halt", {}, 4.0)  # the new plan replaces the old one: stop what is running first
            poss.supersede(x_possession_lease)
        try:
            poss.authorize_act(x_possession_lease, cmd=body.cmd if not body.supersede else None)  # re-check right before dispatch: a stop that arrived meanwhile wins
        except PossessionError as exc:
            poss.note("act_refused", code=exc.code, late=True)
            raise _fail(exc)
        action_id = f"act-{uuid.uuid4().hex[:10]}"
        epoch = body.world_epoch if body.world_epoch is not None else poss.s.world_epoch
        session = poss.s.session_id
        try:
            poss.begin_action(x_possession_lease)
        except PossessionError as exc:
            raise _fail(exc)
        emitted = time.monotonic()
        tracer().mark(session, "act_emit", action_id=action_id, utterance_id=body.utterance_id, reply_to=body.reply_to)

        async def dispatch_action() -> tuple[dict[str, Any], dict[str, Any]]:
            if body.shortcut is not None:
                return await _run_shortcut(body.shortcut, action_id, epoch)
            described_action = body.action or {}
            return await _act(described_action, action_id, epoch), described_action

        dispatch_task = asyncio.create_task(dispatch_action())
        try:
            result, described = await asyncio.shield(dispatch_task)
        except asyncio.CancelledError:
            # Cancelling the HTTP waiter does not stop asyncio.to_thread's daemon call.
            # Keep the one-body-action gate until that physical request really returns.
            while not dispatch_task.done():
                try:
                    await asyncio.shield(dispatch_task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            raise
        finally:
            poss.finish_action()
        returned = time.monotonic()
        if not poss.active():  # ended during the action: report, do not continue
            raise _fail(PossessionError("not_active", "ended during the action"))
        if poss.s.session_id != session:
            raise HTTPException(status_code=409, detail={"code": "possession_session_changed",
                                                         "detail": "the action belongs to an ended possession; do not reuse its result"})
        if result.get("error") == "http_409" and result.get("detail") == "world_epoch_changed":
            seen = await _perceive(4.0)
            poss.note_world_epoch(seen.get("epoch"))
            raise HTTPException(status_code=409, detail={"code": "world_epoch_changed", "detail": "the world/session changed; re-read state and re-plan",
                                                         "world_epoch": seen.get("epoch")})
        if result.get("error") == "http_409":
            raise HTTPException(status_code=409, detail={"code": str(result.get("detail") or "refused_by_daemon"), "detail": "the daemon refused the body action"})
        if poss.s.world_epoch != epoch:
            raise HTTPException(status_code=409, detail={"code": "world_epoch_changed",
                                                         "detail": "the action result belongs to an earlier world epoch; re-read state",
                                                         "world_epoch": poss.s.world_epoch})
        after = result.get("perception") if isinstance(result, dict) else None
        effect = observed_effect(described, getattr(poss, "_last_perception", None), after)
        poss._last_perception = after
        latency = poss.claim_reply(body.reply_to)
        tracer().mark(session, "act_daemon_returned", action_id=action_id, ok=bool(result.get("ok")), daemon_timing_ms=result.get("timing_ms"),
                      emit_to_return_ms=round((returned - emitted) * 1000.0, 1))
        tracer().mark(session, "effect_checked", action_id=action_id, effect=effect)
        poss.note("act", action=described if isinstance(described, dict) else {}, shortcut=body.shortcut, ok=bool(result.get("ok")), status=result.get("status"),
                  error=result.get("error"), latency_ms=latency, took_ms=round((returned - emitted) * 1000.0, 1), action_id=action_id, cmd=body.cmd,
                  effect=effect, daemon_timing_ms=result.get("timing_ms"))
        observed_at = time.time()
        event_key = f"action:{session}:{action_id}"
        action_type = str(described.get("type") if isinstance(described, dict) else body.shortcut or "unknown")[:32]
        facts = {"action_id": action_id, "action": action_type, "status": str(result.get("status") or "unknown")[:32],
                 "ok": bool(result.get("ok")), "outcome_class": _action_outcome_class(result, effect), "effect": effect}
        poss.post_event("action_observation", dedupe_key=event_key, source="possession_act_result", event_key=event_key,
                        world_epoch=poss.s.world_epoch, observed_at=observed_at, facts=facts)
        try:
            from .core.cognitive_loop import cognitive_loop
            cognitive_loop.note_minecraft_event(event_key, {"action": action_type,
                                                            "action_id": action_id, "status": result.get("status"),
                                                            "effect": effect, "outcome_class": facts["outcome_class"],
                                                            "inventory_delta": effect.get("delta", {})})
            cognitive_loop.request_think("minecraft_action_result")
        except Exception:
            pass  # cognitive participation cannot delay or fail the body result
        return {**result, "action_id": action_id, "effect": effect, "outcome_class": facts["outcome_class"],
                "latency_ms": latency, "command_version": poss.s.command_version}

    @router.post("/sheet")
    async def sheet(request: Request, payload: dict[str, Any] = Body(...), x_possession_lease: str | None = Header(default=None)):
        if len(json.dumps(payload, ensure_ascii=False)) > 4000:
            raise HTTPException(status_code=413, detail={"code": "sheet_too_large", "detail": "keep the sheet compact: facts, not narration"})
        try:
            return poss.set_sheet(x_possession_lease, payload)
        except PossessionError as exc:
            raise _fail(exc)

    @router.get("/sheet")
    async def sheet_view(request: Request, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.check(x_possession_lease)
        except PossessionError as exc:
            raise _fail(exc)
        return {"block": poss.sheet_block(), "status": poss.sheet_status()}

    @router.get("/state")
    async def state(request: Request, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.check(x_possession_lease)
        except PossessionError as exc:
            raise _fail(exc)
        seen = await _perceive(4.0)
        poss.note_world_epoch(seen.get("epoch"))
        poss._last_perception = seen.get("perception")
        return {"possession": poss.public_state(), "perception": seen.get("perception"), "series": seen.get("series"), "world_epoch": seen.get("epoch"),
                "daylight_state": seen.get("daylight_state"), "tick_state": seen.get("tick_state"), "body_error": None if seen.get("ok") else seen.get("error")}

    @router.post("/shot")
    async def shot(request: Request, body: ShotBody, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.check(x_possession_lease)
        except PossessionError as exc:
            raise _fail(exc)
        result = await asyncio.to_thread(io.capture_screen, None, active_window=body.active_window)
        if result.get("ok"):
            poss.note_shot(result.get("sha256"))
        poss.note("shot", ok=bool(result.get("ok")), error=result.get("error"), sha256=result.get("sha256"), bytes=result.get("bytes"))
        return result

    @router.post("/quest")
    async def quest(request: Request, body: QuestBody, x_possession_lease: str | None = Header(default=None)):
        try:
            return poss.set_quest(x_possession_lease, **body.model_dump(exclude_none=True))
        except PossessionError as exc:
            raise _fail(exc)

    @router.post("/log")
    async def log(request: Request, body: LogBody, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.check(x_possession_lease)
        except PossessionError as exc:
            raise _fail(exc)
        poss.note(body.kind, text=body.text)
        return {"ok": True}

    @router.get("/persona")
    async def persona(request: Request, x_possession_lease: str | None = Header(default=None)):
        try:
            poss.check(x_possession_lease)
        except PossessionError as exc:
            raise _fail(exc)
        return io.persona()

    @router.get("/stats")
    async def stats(request: Request):
        return poss.public_state()

    return router
