"""Possession core v2: the persona keeps the conversation, the external agent owns the BODY. One controller, mirrored owner speech, controls decided in the
central, separate versions (command / world), fragments merged, a replaced compact sheet, no auto restart."""
import asyncio
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from celia.core.possession import Possession, PossessionError, classify, merge_fragments  # noqa: E402


def make(tmp_path):
    t = [1000.0]
    p = Possession(tmp_path / "poss", clock=lambda: t[0], mono=lambda: t[0])
    return p, t


def owner(p, text, **kw):
    return p.ingest(text, source=kw.pop("source", "microphone"), authenticated_owner=kw.pop("auth", True), self_echo=kw.pop("echo", False), **kw)


def run(coro):
    return asyncio.run(coro)


def journal(tmp_path, p):
    return [json.loads(l) for l in (tmp_path / "poss/sessions" / f"{p.s.session_id}.jsonl").read_text().splitlines()]


# ---------------------------------------------------------------- phrase classification (positive AND negative controls) ------------------------
@pytest.mark.parametrize("text,kind", [
    ("Acabou o vídeo.", "end"), ("Célia, acabou o vídeo", "end"), ("Encerrado o vídeo.", "end"), ("Pode encerrar a possessão.", "end"),
    ("então, acabou o vídeo, gente", "end"), ("Acabou a gravação.", "end"), ("Célia, acabou a gravação", "end"), ("Encerrada a gravação.", "end"), ("fim da gravação", "end"),
    ("Para tudo agora", "stop"), ("Célia, para", "stop"), ("cancela tudo", "stop"),
    ("Pula", "jump"), ("Célia, dá um pulo", "jump"), ("Agacha aí", "sneak"), ("Fica agachada", "sneak"),
    ("Levanta", "stand"), ("Fica de pé", "stand"),
    ("Silêncio", "silence"), ("Célia, fica quieta", "silence"), ("cala a boca", "silence"), ("para de falar", "silence"),
    ("hum", "backchannel"), ("Tá.", "backchannel"), ("é, né", "backchannel"), ("ahn", "backchannel"),
    ("Vem aqui do meu lado pra aparecer", "speech"), ("vou pegar madeira", "speech"), ("tá bom, vem aqui", "speech"),
])
def test_classification(text, kind):
    assert classify(text) == kind


@pytest.mark.parametrize("text", [
    "quando eu disser acabou o vídeo você encerra", "não acabou o vídeo ainda", "ele disse acabou o vídeo", "falta pouco pra acabou o vídeo",
    "não acabou a gravação", "quando eu disser acabou a gravação você encerra", "a gravação acabou de começar",
    "não para tudo", "se eu pedir silêncio você cala", "a palavra silêncio é bonita", "acabou o vídeo do Mario e eu quero outro",
    "não pula", "não agacha", "eu disse 'pula'", "quando eu falar levanta, você levanta",
])
def test_quotes_negations_and_mentions_never_end_stop_or_silence(text):
    assert classify(text) not in {"end", "stop", "silence"}, text


# ---------------------------------------------------------------- single controller ---------------------------------------------------------
def test_only_one_controller_at_a_time_and_takeover_is_explicit(tmp_path):
    p, _ = make(tmp_path)
    first = p.start("claude")
    for other in ("codex", "claude"):
        with pytest.raises(PossessionError) as e:
            p.start(other)
        assert e.value.code == "already_possessed"
    taken = p.start("codex", takeover=True)
    assert taken["lease"] != first["lease"] and p.s.controller == "codex"
    with pytest.raises(PossessionError) as old:
        p.authorize_act(first["lease"], cmd=None)
    assert old.value.code == "bad_lease"
    p.authorize_act(taken["lease"], cmd=None)


def test_unknown_controller_is_refused(tmp_path):
    p, _ = make(tmp_path)
    with pytest.raises(PossessionError) as e:
        p.start("hermes")
    assert e.value.code == "bad_controller" and not p.active()


# ---------------------------------------------------------------- the owner's voice is MIRRORED, not taken ------------------------------------
def test_inactive_possession_passes_everything_through(tmp_path):
    p, _ = make(tmp_path)
    assert owner(p, "vem aqui").consumed is False


def test_ordinary_speech_keeps_going_to_the_persona_and_is_mirrored_to_the_agent(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    verdict = owner(p, "Célia, vem aqui do meu lado pra aparecer", utterance_id="u1", turn_id="t1")
    assert verdict.consumed is False and verdict.kind == "speech", "the persona must still receive the owner's words"
    got = run(p.next_events(lease, 0, 0, coalesce_ms=0))
    event = got["events"][0]
    assert event["type"] == "owner_speech" and event["utterance_id"] == "u1" and event["turn_id"] == "t1"
    assert not any(k.startswith("_") for k in event), "internal fields never leak"


@pytest.mark.parametrize("text,event_type", [("Pula", "owner_jump"), ("Agacha", "owner_sneak"), ("Levanta", "owner_stand")])
def test_simple_body_imperatives_are_mirrored_as_typed_owner_events_without_swallowing_persona_speech(tmp_path, text, event_type):
    p, _ = make(tmp_path)
    lease = p.start("codex")["lease"]
    verdict = owner(p, text, utterance_id="body-command")
    assert verdict.kind in {"jump", "sneak", "stand"} and verdict.consumed is False
    event = run(p.next_events(lease, 0, 0, coalesce_ms=0))["events"][0]
    assert event["type"] == event_type and event["utterance_id"] == "body-command"


def test_chat_game_text_and_her_own_voice_never_reach_the_agent_and_are_not_swallowed(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    for kw in ({"auth": False}, {"echo": True}, {"source": "chat"}, {"source": "game"}):
        verdict = owner(p, "vem aqui", **kw)
        assert verdict.kind == "ignored" and verdict.consumed is False, kw  # the persona's own guards decide what to do with it
    assert run(p.next_events(lease, 0, 0, coalesce_ms=0))["events"] == []
    assert [r["kind"] for r in journal(tmp_path, p)].count("ignored") == 4


def test_the_journal_labels_the_session_as_external_assistance_with_the_contract(tmp_path):
    p, _ = make(tmp_path)
    p.start("codex")
    first = journal(tmp_path, p)[0]
    assert first["kind"] == "started" and first["external_assistance"] is True and first["controller"] == "codex" and first["contract"] == "persona_speaks_agent_controls_body"


# ---------------------------------------------------------------- deterministic controls --------------------------------------------------------
def test_end_phrase_is_decided_by_the_central_and_stop_releases_everything_without_restart(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    cmd = p.s.command_version
    calls = []

    async def hook(reason):
        calls.append(reason)
    p.on_stop.append(hook)
    verdict = owner(p, "Acabou a gravação.")
    assert verdict.consumed and verdict.kind == "end"
    assert run(p.stop("owner_end_phrase"))["stopped"] and calls == ["owner_end_phrase"] and not p.active()
    with pytest.raises(PossessionError) as e:
        p.authorize_act(lease, cmd=cmd)
    assert e.value.code == "not_active", "after the end no action is possible"
    assert run(p.stop("again")) == {"stopped": False, "reason": "not_active"} and calls == ["owner_end_phrase"]
    assert owner(p, "vem aqui").consumed is False


def test_a_failing_stop_hook_never_keeps_the_possession_alive(tmp_path):
    p, _ = make(tmp_path)
    p.start("claude")

    async def boom(reason):
        raise RuntimeError("daemon down")
    p.on_stop.append(boom)
    run(p.stop("owner"))
    assert not p.active() and any(r["kind"] == "stop_hook_failed" for r in journal(tmp_path, p))


def test_central_restart_ends_a_half_alive_possession_and_never_restarts_it(tmp_path):
    p, _ = make(tmp_path)
    p.start("claude")
    again = Possession(tmp_path / "poss")
    assert not again.active() and again.s.ended_reason == "central_restart"


def test_silence_is_consumed_so_the_persona_stays_quiet_and_the_next_words_lift_it(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    verdict = owner(p, "Célia, fica quieta")
    assert verdict.kind == "silence" and verdict.consumed and p.is_silenced()
    p.authorize_act(lease, cmd=p.s.command_version)  # silence is not a stop: the body keeps going
    owner(p, "ok, vem aqui")
    assert not p.is_silenced()


def test_stop_kills_pending_body_commands_but_still_reaches_the_series_emergency_stop(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    planned = p.s.command_version
    verdict = owner(p, "Célia, para tudo agora")
    assert verdict.kind == "stop" and verdict.consumed is False, "stays on the normal path so the deterministic series stop still runs"
    with pytest.raises(PossessionError) as e:
        p.authorize_act(lease, cmd=planned)
    assert e.value.code == "stale"
    p.authorize_act(lease, cmd=p.s.command_version)


# ---------------------------------------------------------------- versions that do NOT mix -----------------------------------------------------
def test_an_interjection_or_a_new_sentence_does_not_cancel_a_valid_body_task(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    planned = p.s.command_version
    for text in ("hum", "tá", "vai pegar madeira ali", "ahn", "e aí, tudo bem?"):
        owner(p, text)
    assert p.s.command_version == planned
    p.authorize_act(lease, cmd=planned)


def test_the_agent_replacing_its_own_plan_makes_the_older_commands_stale(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    old = p.s.command_version
    new = p.supersede(lease)
    assert new == old + 1
    with pytest.raises(PossessionError) as e:
        p.authorize_act(lease, cmd=old)
    assert e.value.code == "stale"
    p.authorize_act(lease, cmd=new)
    p.authorize_act(lease, cmd=None)


def test_locally_dispatched_owner_body_command_invalidates_prepared_agent_actions(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")['lease']
    old = p.s.command_version
    new = p.note_local_owner_command(utterance_id="u-42", intent="companion.jump")
    assert new == old + 1
    with pytest.raises(PossessionError) as exc:
        p.authorize_act(lease, cmd=old)
    assert exc.value.code == "stale"
    p.authorize_act(lease, cmd=new)


def test_world_epoch_invalidates_commands_from_the_previous_world(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude", world_epoch=12)["lease"]
    cmd = p.s.command_version
    p.note_world_epoch(13)
    assert p.s.world_epoch == 13 and p.s.command_version == cmd + 1
    assert any(r["kind"] == "world_epoch" and r["previous"] == 12 and r["epoch"] == 13 for r in journal(tmp_path, p))
    with pytest.raises(PossessionError) as exc:
        p.authorize_act(lease, cmd=cmd)
    assert exc.value.code == "stale"
    p.authorize_act(lease, cmd=p.s.command_version)


# ---------------------------------------------------------------- fragments ------------------------------------------------------------------------
def test_fragments_of_one_thought_reach_the_agent_as_one_event():
    base = {"t": 1.0, "source": "microphone", "command_version": 0}
    events = [{**base, "seq": 1, "type": "owner_speech", "text": "vem aqui", "utterance_id": "a"},
              {**base, "seq": 2, "type": "owner_speech", "text": "do meu lado", "utterance_id": "b"},
              {**base, "seq": 3, "type": "owner_backchannel", "text": "hum"},
              {**base, "seq": 4, "type": "owner_speech", "text": "pra aparecer", "utterance_id": "c"}]
    merged = merge_fragments(events)
    assert [e["type"] for e in merged] == ["owner_speech", "owner_backchannel", "owner_speech"]
    assert merged[0]["text"] == "vem aqui do meu lado" and merged[0]["fragments"] == 2 and merged[0]["seq"] == 2 and merged[0]["utterance_id"] == "b"
    assert merged[2]["fragments"] == 1


def test_the_poll_waits_a_moment_for_the_rest_of_a_thought_but_never_for_a_stop(tmp_path):
    async def go():
        p = Possession(tmp_path / "poss")  # REAL clock: the coalescing window is real time
        lease = p.start("claude")["lease"]

        async def talk():
            owner(p, "vem aqui")
            await asyncio.sleep(0.08)
            owner(p, "do meu lado")
        asyncio.create_task(talk())
        await asyncio.sleep(0.01)
        got = await p.next_events(lease, 0, 2, coalesce_ms=200)
        assert [e["text"] for e in got["events"]] == ["vem aqui do meu lado"] and got["events"][0]["fragments"] == 2
        started = asyncio.get_running_loop().time()
        owner(p, "Célia, para tudo agora")
        urgent = await p.next_events(lease, got["last_seq"], 2, coalesce_ms=400)
        assert urgent["events"][0]["type"] == "owner_stop" and asyncio.get_running_loop().time() - started < 0.15, "a stop is never delayed by the window"
    run(go())


def test_a_frozen_clock_can_never_hang_the_poll(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    owner(p, "oi")
    assert run(asyncio.wait_for(p.next_events(lease, 0, 1, coalesce_ms=250), 3))["events"]


# ---------------------------------------------------------------- latency, long poll, cue ------------------------------------------------------
def test_latency_is_owner_utterance_to_the_agents_first_action(tmp_path):
    p, t = make(tmp_path)
    p.start("claude")
    seq = owner(p, "vem aqui").seq
    t[0] += 1.25
    assert p.mark_replied(seq) == 1250.0 and p.mark_replied(seq) is None
    assert p.latency_stats() == {"n": 1, "p50_ms": 1250.0, "p95_ms": 1250.0, "max_ms": 1250.0}


def test_next_wakes_when_something_arrives_and_when_the_possession_ends(tmp_path):
    async def go():
        p = Possession(tmp_path / "poss")
        lease = p.start("claude")["lease"]
        waiter = asyncio.create_task(p.next_events(lease, 0, 5, coalesce_ms=0))
        await asyncio.sleep(0.05)
        assert not waiter.done()
        owner(p, "oi Célia")
        got = await asyncio.wait_for(waiter, 1.0)
        assert [e["text"] for e in got["events"]] == ["oi Célia"]
        ender = asyncio.create_task(p.next_events(lease, got["last_seq"], 5, coalesce_ms=0))
        await asyncio.sleep(0.05)
        await p.stop("owner")
        ended = await asyncio.wait_for(ender, 1.0)
        assert ended["active"] is False and ended["ended_reason"] == "owner"
    run(go())


def test_an_empty_poll_carries_the_open_step_as_the_cue_for_initiative_and_a_new_word_removes_it(tmp_path):
    async def go():
        p, t = make(tmp_path)
        lease = p.start("claude")["lease"]
        quiet = await p.next_events(lease, 0, 0, coalesce_ms=0)
        assert "initiative_cue" not in quiet and quiet["quest"] == {"miniquest": "", "next": ""}
        p.set_quest(lease, miniquest="juntar madeira", next="pegar 6 troncos")
        t[0] += 30
        cued = await p.next_events(lease, 0, 0, coalesce_ms=0)
        assert "pegar 6 troncos" in cued["initiative_cue"] and cued["owner_quiet_s"] == 30.0
        owner(p, "espera")
        assert "initiative_cue" not in await p.next_events(lease, 0, 0, coalesce_ms=0)
        p.set_quest(lease, next="")
        t[0] += 5
        assert "initiative_cue" not in await p.next_events(lease, 99, 0, coalesce_ms=0)
    run(go())


# ---------------------------------------------------------------- the compact sheet ------------------------------------------------------------
def test_a_new_sheet_replaces_the_old_one_atomically_and_dies_with_the_session(tmp_path):
    p, t = make(tmp_path)
    lease = p.start("claude", world_epoch=12)["lease"]
    assert p.sheet_block() == ""
    first = p.set_sheet(lease, {"objective": "abrigo", "task": "juntando madeira", "body_state": {"v": "ao lado do criador", "src": "measured", "conf": 0.9}})
    assert first["version"] == 1 and "juntando madeira" in p.sheet_block()
    second = p.set_sheet(lease, {"objective": "abrigo", "last_result": "madeira coletada"})
    assert second["version"] == 2
    block = p.sheet_block()
    assert "madeira coletada" in block and "juntando madeira" not in block, "nothing of the old sheet survives: it is REPLACED, not stacked"
    assert block.count("[FICHA DO CORPO") == 1 and "época 12" in block
    run(p.stop("owner"))
    assert p.sheet_block() == "", "a dead session's facts never reach the persona"


def test_sheet_fields_expire_on_their_own_clock(tmp_path):
    p, t = make(tmp_path)
    lease = p.start("claude")["lease"]
    p.set_sheet(lease, {"objective": "abrigo", "body_state": "parada ao lado", "risk": "zumbi perto", "last_result": "ok"})
    t[0] += 10  # position/danger go stale in seconds
    block = p.sheet_block()
    assert "abrigo" in block and "zumbi perto" not in block and "parada ao lado" not in block
    assert "vencidos" in block and "risk" in block, "stale facts are announced as expired, never kept as present"
    t[0] += 60
    assert "último resultado: ok" not in p.sheet_block()


def test_only_the_lease_holder_can_publish_a_sheet(tmp_path):
    p, _ = make(tmp_path)
    p.start("claude")
    with pytest.raises(PossessionError) as e:
        p.set_sheet("wrong", {"objective": "x"})
    assert e.value.code == "bad_lease"


def test_quest_state_is_kept_bounded_and_saved_and_the_lease_never_touches_the_disk(tmp_path):
    p, _ = make(tmp_path)
    lease = p.start("claude")["lease"]
    q = p.set_quest(lease, objective="abrigo", miniquest="juntar madeira", next="pegar 6 troncos", pending=["x"] * 20, obstacles=["noite"])
    assert q["objective"] == "abrigo" and len(q["pending"]) == 8
    saved = json.loads((tmp_path / "poss/state.json").read_text())
    assert saved["quest"]["miniquest"] == "juntar madeira" and "lease" not in saved


def test_latency_is_claimed_by_the_first_action_after_reading_even_if_the_agent_never_passes_reply_to(tmp_path):
    async def go():
        p, t = make(tmp_path)
        lease = p.start("claude")["lease"]
        owner(p, "vem aqui")
        t[0] += 0.5
        owner(p, "do meu lado")
        t[0] += 0.5
        await p.next_events(lease, 0, 0, coalesce_ms=0)
        t[0] += 0.75
        assert p.claim_reply() == 1750.0, "measured from the EARLIEST unanswered fragment to the first action"
        assert p.claim_reply() is None and p.latency_stats()["n"] == 1, "one action answers a burst once"
    run(go())


# ------------------------------------------------------------------ 2026-10-21 evidence: resend never renews, framing needs a shot, world change drops NOW-facts, hold kills pending ----
def _lease(p):
    return p.start("claude")["lease"]


def test_resending_an_unchanged_sheet_does_not_renew_the_observation_age(tmp_path):
    p, t = make(tmp_path)
    lease = _lease(p)
    sheet = {"body_state": {"v": "parada perto", "src": "measured", "conf": 0.9, "evidence": "pos"}}
    p.set_sheet(lease, sheet)
    assert "corpo:" in p.sheet_block()
    t[0] += 30
    p.set_sheet(lease, sheet)  # re-posting the very same old fact
    assert "corpo:" not in (p.sheet_block() or ""), "30 s old > 8 s ttl: resend did not make it fresh"
    p.set_sheet(lease, {"body_state": {"v": "andando", "src": "measured", "conf": 0.9, "evidence": "pos"}})
    assert "corpo: andando" in p.sheet_block(), "a CHANGED field is a new observation"


def test_framing_confirmed_requires_a_recent_registered_capture(tmp_path):
    p, t = make(tmp_path)
    lease = _lease(p)
    out = p.set_sheet(lease, {"framing": {"status": "confirmed", "evidence": "shot:abcdef123456"}})
    assert out["framing_downgraded"] is True and p.sheet["framing"]["status"] == "unknown", "no capture registered: not confirmed"
    p.note_shot("abcdef123456789")
    out = p.set_sheet(lease, {"framing": {"status": "confirmed", "evidence": "shot:abcdef123456"}})
    assert out["framing_downgraded"] is False and p.sheet["framing"]["status"] == "confirmed"
    t[0] += 60
    out = p.set_sheet(lease, {"framing": {"status": "confirmed", "evidence": "shot:abcdef123456"}})
    assert out["framing_downgraded"] is True, "an old capture is not evidence for NOW"


def test_a_world_change_drops_the_facts_about_now_and_keeps_the_rest(tmp_path):
    p, t = make(tmp_path)
    lease = _lease(p)
    p.note_world_epoch(3)
    p.set_sheet(lease, {"objective": "construir abrigo", "body_state": {"v": "perto"}, "task": "minerar", "risk": "nenhum"})
    p.note_world_epoch(4)
    assert set(p.sheet) == {"objective"}, "NOW-facts belong to the old world"
    assert any(e.get("kind") == "sheet_invalidated" for e in journal(tmp_path, p))


def test_hold_is_urgent_and_invalidates_pending_commands_like_stop(tmp_path):
    p, t = make(tmp_path)
    lease = _lease(p)
    version = p.s.command_version
    owner(p, "fica parada")
    assert p.s.command_version == version + 1
    with pytest.raises(PossessionError):
        p.authorize_act(lease, cmd=version)  # a command prepared before the hold does not start after it
    owner(p, "se mexe um pouquinho")
    events = run(p.next_events(lease, 0, 0.1, coalesce_ms=0))["events"]
    assert [e["type"] for e in events][-2:] == ["owner_hold", "owner_wiggle"]
