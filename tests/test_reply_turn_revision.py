"""A reply in progress is not discarded by speech Célia chose not to answer.

INCIDENT, log var/logs/celia-20261002T054255Z.log, 2026-10-02:

    11:35:01  "Célia, tu tá vendo a tela aqui?" -> SUBMITTED_TO_BRAIN
    11:35:17  "Sabe? Oi!"                      -> ROUTING_CONTEXT_ONLY
    11:35:22  Discarding stale agent output     -> 0 chars, Célia silent

routes.py calls observe("speech.final") BEFORE the routing decision, and
speech.final does `revision += 1`. Speech routed as context-only therefore
invalidated the reply it was never meant to replace (3 of 6 discards that day).

Contract (PLANO-CORRECAO-STALE-CONTEXT-ONLY.md):
- `revision` = context revision. Still moves on every speech.final and still
  cancels spontaneous candidates and system-origin (spontaneous) replies.
- `turn_revision` = what makes an OWNER reply and its TTS obsolete: a new
  submitted turn, a real profile switch, load_script, reset_session,
  set_script_position and a real interruption.

`reply_token` falls back to `revision` so the same assertions run against the
pre-fix code: T1 must be RED there, the preservation tests must be GREEN.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import math
import re
import struct
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from loguru import logger

from test_operational_profiles import behavior  # noqa: F401  (fixture)
from celia.core.continuous_behavior import ContinuousBehaviorRuntime


# ── helpers ────────────────────────────────────────────────────────────────

def reply_token(rt):
    return rt.turn_revision if hasattr(rt, "turn_revision") else rt.revision


def context_only_speech(rt, text="Sabe? Oi!", uid="ctx-1"):
    """Exactly what routes.py does for an utterance that ends ROUTING_CONTEXT_ONLY."""
    rt.observe("speech.started", source="microphone", utterance_id=uid)
    rt.observe("speech.final", source="microphone", text=text, utterance_id=uid)


def submitted_owner_turn(rt, text="Célia, esquece isso.", uid="new-1"):
    """What routes.py does for SUBMITTED_TO_BRAIN: speech.final, then the
    reply invalidation right before submit_input. Pre-fix the method does not
    exist and speech.final alone already invalidated."""
    rt.observe("speech.started", source="microphone", utterance_id=uid)
    rt.observe("speech.final", source="microphone", text=text, utterance_id=uid)
    invalidate = getattr(rt, "invalidate_inflight_reply", None)
    if invalidate is not None:
        invalidate("owner_turn_submitted")


def real_interruption(rt):
    rt.interrupt("real_speech")


def _wav(path):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"".join(struct.pack("<h", int(1000 * math.sin(i * 0.1))) for i in range(4800)))
    return path


def run_turn(behavior, monkeypatch, tmp_path, *, during_think=None, during_tts=None, origin="microphone", user_input=None, scripted_reply=None):
    """Drive the real process_single_conversation with a fake agent and TTS engine."""
    import celia.conversations.single_conversation as conversation
    from celia.celia_singleton import CeliaSingleton
    from celia.agent.output_types import SentenceOutput, DisplayText, Actions

    singleton = SimpleNamespace(
        memoria=SimpleNamespace(ativo=lambda _: None, verificar_audio=lambda _: False),
        observar_banco=lambda *a, **k: None,
        registrar_turno_operacional=lambda **k: None,
    )
    monkeypatch.setattr(CeliaSingleton, "get_instance", lambda: singleton)

    class Agent:
        async def chat(self, batch):
            if during_think:
                during_think(behavior)
            yield SentenceOutput(DisplayText("Tô vendo sim."), "Tô vendo sim.", Actions())

    wav = _wav(tmp_path / "reply.wav")

    async def fake_audio(*a, **k):
        if during_tts:
            during_tts(behavior)
        return str(wav)

    engine = SimpleNamespace(async_generate_audio=AsyncMock(side_effect=fake_audio))
    context = SimpleNamespace(
        system_config=SimpleNamespace(audio_output_device=None),
        character_config=SimpleNamespace(human_name="Criador", character_name="Celia", avatar="", conf_uid="test", agent_config=None),
        history_uid=None, live2d_model=None, asr_engine=None, translate_engine=None,
        agent_engine=Agent(), tts_engine=engine,
    )
    sent = []

    async def send(payload):
        sent.append(json.loads(payload))

    async def finalize(tts_manager, **kwargs):
        await asyncio.gather(*tts_manager.task_list)
        await tts_manager.wait_for_payload_delivery()

    monkeypatch.setattr(conversation, "finalize_conversation_turn", finalize)
    result = asyncio.run(conversation.process_single_conversation(
        context, send, "test", user_input or "Célia, tu tá vendo a tela aqui?", metadata={"origin": origin, "scripted_reply": scripted_reply},
    ))
    audio = [p for p in sent if p.get("type") == "audio" and p.get("audio")]
    return result or "", audio, engine


# ── T1: the bug ───────────────────────────────────────────────────────────

def test_t1_context_only_speech_does_not_change_reply_token(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    prepared = reply_token(rt)
    context_only_speech(rt)
    assert reply_token(rt) == prepared


def test_t1_context_only_speech_keeps_reply_in_progress(behavior, monkeypatch, tmp_path):
    before = behavior.revision
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_think=context_only_speech)
    assert behavior.revision > before, "context revision must still move"
    assert result.strip() == "Tô vendo sim."
    engine.async_generate_audio.assert_awaited_once()
    assert len(audio) == 1


# ── T2: a new submitted turn still wins ──────────────────────────────────

def test_t2_submitted_turn_changes_reply_token(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    prepared = reply_token(rt)
    submitted_owner_turn(rt)
    assert reply_token(rt) != prepared


def test_t2_submitted_turn_discards_reply_in_progress(behavior, monkeypatch, tmp_path):
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_think=submitted_owner_turn)
    assert result == ""
    engine.async_generate_audio.assert_not_awaited()
    assert audio == []


def test_t2_every_mic_submission_in_routes_invalidates_the_reply_first():
    """Structural guard: the four SUBMITTED_TO_BRAIN submissions of the mic
    handler each call invalidate_inflight_reply before submit_input."""
    import celia.routes as routes
    source = inspect.getsource(routes)
    blocks = re.findall(
        r"(\n[^\n]*\n[^\n]*\n[^\n]*await singleton\.submit_input\((?:(?!\n\s*\)\n).)*?\n\s*\)\n"
        r"(?:(?!submit_input).){0,600}?\"SUBMITTED_TO_BRAIN\")",
        source, flags=re.S,
    )
    assert len(blocks) >= 4, f"expected the 4 mic submission sites, found {len(blocks)}"
    for block in blocks:
        assert "invalidate_inflight_reply(" in block, block[:300]


# ── T3: every other legitimate invalidation still invalidates ────────────

@pytest.mark.parametrize("event", [
    lambda rt: rt.select_profile("cohost"),
    lambda rt: rt.reset_session(),
    lambda rt: rt.load_script({"id": "s", "version": 1, "beats": [{"id": "b1", "text": "x"}, {"id": "b2", "text": "y"}]}),
    real_interruption,
], ids=["profile_switch", "reset_session", "load_script", "real_interruption"])
def test_t3_legitimate_events_change_reply_token(tmp_path, event):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    rt.select_profile("chat")
    prepared = reply_token(rt)
    event(rt)
    assert reply_token(rt) != prepared


def test_t3_script_position_changes_reply_token(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    rt.select_profile("roteiro")
    rt.load_script({"id": "s", "version": 1, "beats": [{"id": "b1", "text": "x"}, {"id": "b2", "text": "y"}]})
    prepared = reply_token(rt)
    rt.set_script_position("b2")
    assert reply_token(rt) != prepared


@pytest.mark.parametrize("event", [
    lambda rt: rt.select_profile("cohost"),
    lambda rt: rt.reset_session(),
    real_interruption,
], ids=["profile_switch", "reset_session", "real_interruption"])
def test_t3_legitimate_events_discard_reply_in_progress(behavior, monkeypatch, tmp_path, event):
    behavior.select_profile("chat")
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_think=event)
    assert result == ""
    assert audio == []


# ── T4: same-profile switch invalidates nothing ─────────────────────────

def test_t4_same_profile_switch_is_a_no_op(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    rt.select_profile("chat")
    context_before, reply_before = rt.revision, reply_token(rt)
    rt.select_profile("chat")
    assert rt.revision == context_before
    assert reply_token(rt) == reply_before


# ── T5: spontaneous speech keeps the old, stricter semantics ────────────

def test_t5_candidate_is_still_cancelled_by_context_only_speech(tmp_path):
    rt = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    rt.select_profile("cohost")
    candidate = rt.create_candidate(
        intent_type="cognitive_contribution", semantic_intent="comentário", ttl_sec=30,
    )
    assert candidate.status in {"WAITING", "READY"}
    context_only_speech(rt)
    rt._expire(__import__("time").monotonic())
    assert candidate.status == "CANCELLED"
    assert candidate.reason == "context_revision_changed"


def test_t5_system_origin_reply_is_still_discarded_by_context_only_speech(behavior, monkeypatch, tmp_path):
    """A cognitive contribution approved by the arbiter enters the same
    conversation path with origin='system'. It must stay as strict as before."""
    result, audio, engine = run_turn(
        behavior, monkeypatch, tmp_path, during_think=context_only_speech, origin="system",
    )
    assert result == ""
    assert audio == []


# ── T6: the reply's TTS follows the same contract ───────────────────────

def test_t6_reply_audio_survives_context_only_speech_during_synthesis(behavior, monkeypatch, tmp_path):
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_tts=context_only_speech)
    engine.async_generate_audio.assert_awaited_once()
    assert len(audio) == 1


@pytest.mark.parametrize("event", [submitted_owner_turn, real_interruption], ids=["new_turn", "interruption"])
def test_t6_reply_audio_is_dropped_by_new_turn_or_interruption(behavior, monkeypatch, tmp_path, event):
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_tts=event)
    assert audio == []


def test_t6_system_origin_audio_is_still_dropped_by_context_only_speech(behavior, monkeypatch, tmp_path):
    result, audio, engine = run_turn(
        behavior, monkeypatch, tmp_path, during_tts=context_only_speech, origin="system",
    )
    assert audio == []


# ── T7: the discard log carries real numbers ────────────────────────────

def test_t7_discard_log_prints_real_revisions(behavior, monkeypatch, tmp_path):
    lines = []
    sink = logger.add(lambda m: lines.append(str(m)), level="INFO", format="{message}")
    try:
        run_turn(behavior, monkeypatch, tmp_path, during_think=submitted_owner_turn)
    finally:
        logger.remove(sink)
    discard = [l for l in lines if "Discarding stale agent output" in l]
    assert discard, "the discard must be logged"
    assert "%s" not in discard[0]
    assert re.search(r"prepared_turn_revision=\d+ current_turn_revision=\d+", discard[0]), discard[0]


# ── T8: the FULL routes.py path after speech.final (Fatia 1, 2026-10-02) ──
#
# T6 only called observe("speech.final"). routes.py also calls
# speech_coordinator.human_committed() and human_committed_playback() for every
# final utterance, including ROUTING_CONTEXT_ONLY. Measured at 12:33:47: that
# cancelled the owner reply audio while it was being synthesized.

def full_route_context_only(rt, text="Para ela ver o que eu estou falando.", uid="ctx-full"):
    import celia.conversations.tts_manager as tm
    rt.observe("speech.started", source="microphone", utterance_id=uid)
    rt.observe("speech.final", source="microphone", text=text, utterance_id=uid)
    tm.speech_coordinator.human_committed()
    tm.speech_coordinator.human_committed_playback()


def full_route_submitted_turn(rt, text="Célia, esquece isso.", uid="new-full"):
    full_route_context_only(rt, text=text, uid=uid)
    invalidate = getattr(rt, "invalidate_inflight_reply", None)
    if invalidate is not None:
        invalidate("owner_turn_submitted")


def test_t8_routes_calls_human_committed_right_after_speech_final():
    """Guard that the helper above models the real handler, not a guess."""
    import celia.routes as routes
    source = inspect.getsource(routes)
    final = source.index('"speech.final", source="microphone"')
    committed = source.index("speech_coordinator.human_committed()", final)
    playback = source.index("speech_coordinator.human_committed_playback()", final)
    assert committed - final < 600 and playback - final < 800


def test_t8_owner_reply_audio_survives_full_context_only_path(behavior, monkeypatch, tmp_path):
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_tts=full_route_context_only)
    engine.async_generate_audio.assert_awaited_once()
    assert len(audio) == 1


def test_t8_owner_reply_audio_still_dropped_by_full_new_turn_path(behavior, monkeypatch, tmp_path):
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, during_tts=full_route_submitted_turn)
    assert audio == []


def test_t8_system_origin_audio_still_dropped_by_full_context_only_path(behavior, monkeypatch, tmp_path):
    result, audio, engine = run_turn(
        behavior, monkeypatch, tmp_path, during_tts=full_route_context_only, origin="system",
    )
    assert audio == []


def test_verified_inventory_reaches_tts_even_when_minecraft_mode_enables_tools(behavior, monkeypatch, tmp_path):  # noqa: F811 (fixture import)
    from celia.core.minecraft_series import build_brief, facts_for, verified_report_reply
    import celia.agent.bridge_client as bridge
    monkeypatch.setattr(bridge, "needs_tool", lambda text: "minecraft")
    brief = build_brief(facts_for("companion.report", {"what": "inventory"}, {"ok": True, "inventory": {"coal": 2, "cobblestone": 55}}))
    reply = verified_report_reply(brief)
    result, audio, engine = run_turn(behavior, monkeypatch, tmp_path, origin="system", user_input=brief, scripted_reply=reply)
    assert result.strip() == reply["text"]
    assert "55 pedregulho" in result and "2 carvão" in result
    engine.async_generate_audio.assert_awaited_once()
    assert len(audio) == 1
