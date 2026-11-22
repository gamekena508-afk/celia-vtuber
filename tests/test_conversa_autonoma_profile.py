"""Perfil Conversa Autonoma: o mesmo papo rapido do Chat, mais uma iniciativa curta, com silencio compartilhado e sem falar por cima do Criador."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from celia.config_manager.continuous_behavior import ContinuousBehaviorConfig, PROFILE_NAMES
from celia.config_manager.profile_v6 import SocialV6, preset, validated_settings
from celia.core.cognitive_loop import CognitiveLoop
from celia.core.continuous_behavior import ContinuousBehaviorRuntime
from celia.core.profile_switch_intent import detect_profile_switch_intent
from celia.core.shared_silence import SharedSilenceClock

PROFILE = "conversation_autonomous"


class T:
    def __init__(self):
        self.t = 5000.0

    def __call__(self):
        return self.t


@pytest.fixture
def behavior(tmp_path, monkeypatch):
    import celia.core.continuous_behavior as core
    instance = ContinuousBehaviorRuntime(tmp_path / "profile.json")
    monkeypatch.setattr(core, "continuous_behavior", instance)
    instance.clock = T()
    instance.shared_silence = SharedSilenceClock(instance.clock)
    instance.shared_silence.reset()
    return instance


def speak(behavior, text="vamos minerar"):
    behavior.observe("speech.started", source="microphone", speaker="Criador", speaker_role="host", utterance_id="u1")
    behavior.observe("speech.final", source="microphone", speaker="Criador", speaker_role="host", text=text, utterance_id="u1")


# ------------------------------------------------------------------ the profile exists and is in force from the start ------------------------------
def test_the_profile_exists_everywhere_the_five_others_do():
    assert PROFILE in PROFILE_NAMES and PROFILE in ContinuousBehaviorConfig().profiles
    assert preset(PROFILE).social.silence_seconds == 3 and preset(PROFILE).gameplay.ideal_distance == 5


def test_an_old_configuration_that_lists_only_five_profiles_still_loads():
    config = ContinuousBehaviorConfig()
    old = {k: v for k, v in config.profiles.items() if k != PROFILE}
    assert PROFILE in ContinuousBehaviorConfig(profiles=old).profiles


def test_its_settings_are_in_force_without_a_first_save(behavior):
    view = behavior.profile_settings(PROFILE)
    assert view["customized"] is True and view["settings"]["social"]["silence_seconds"] == 3
    behavior.select_profile(PROFILE)
    assert behavior.initiative_policy().silence_seconds == 3.0 and behavior.initiative_policy().cooldown_seconds == 15.0
    assert 0.35 <= behavior.policy["threshold"] <= 0.98 and behavior.policy["proactive"] == "medium"


def test_reset_restores_the_preset_and_keeps_it_in_force(behavior):
    settings = behavior.profile_settings(PROFILE)["settings"]
    settings["social"]["silence_seconds"] = 9
    behavior.update_profile_settings(PROFILE, settings)
    assert behavior.profile_settings(PROFILE)["settings"]["social"]["silence_seconds"] == 9
    behavior.reset_profile_settings(PROFILE)
    again = behavior.profile_settings(PROFILE)
    assert again["settings"]["social"]["silence_seconds"] == 3 and again["customized"] is True


def test_silence_accepts_two_to_one_twenty_and_nothing_outside():
    for ok in (2, 3, 60, 120):
        assert SocialV6(silence_seconds=ok).silence_seconds == ok
    for bad in (0, 1, 121):
        with pytest.raises(ValueError):
            SocialV6(silence_seconds=bad)


def test_older_saved_settings_without_the_new_fields_get_their_defaults():
    old_social = {k: v for k, v in preset("chat").model_dump()["social"].items() if k not in {"spontaneous_cooldown_seconds", "barge_in"}}
    saved = {**preset("chat").model_dump(), "social": old_social}
    loaded = validated_settings(saved)
    assert loaded.social.spontaneous_cooldown_seconds == 15 and loaded.social.barge_in is True


def test_the_profile_persists_across_restarts(tmp_path):
    path = tmp_path / "profile.json"
    first = ContinuousBehaviorRuntime(path)
    first.select_profile(PROFILE)
    again = ContinuousBehaviorRuntime(path)
    assert again.profile == PROFILE and again.initiative_policy() is not None


# ------------------------------------------------------------------ same low-latency conversation as Chat ----------------------------------------
def test_direct_turns_take_the_fast_path_like_chat_and_never_wait_for_arbitration(behavior):
    behavior.select_profile(PROFILE)
    assert behavior.prepare_direct_turn("oi, tudo bem?", "microphone") == {"decision": "SPEAK", "candidate_id": None, "reason": "conversation_autonomous_direct_fast_path"}
    assert behavior.prepare_direct_turn("oi", "browser")["decision"] == "SPEAK"
    assert not behavior.candidates


def test_only_the_owner_paths_are_direct_turns(behavior):
    behavior.select_profile(PROFILE)
    for source in ("chat", "discord", "game", "stream"):
        assert behavior.prepare_direct_turn("oi", source) is None or behavior.direct_turn_allowed("oi", source) is False


def test_chat_keeps_its_own_fast_path_and_cohost_does_not_get_one(behavior):
    behavior.select_profile("chat")
    assert behavior.prepare_direct_turn("oi", "microphone")["reason"] == "chat_direct_fast_path"
    behavior.select_profile("cohost")
    assert behavior.direct_turn_allowed("oi", "microphone") is False


def test_it_keeps_the_light_prediction_and_maximum_latency_priority_of_chat(behavior):
    behavior.select_profile(PROFILE)
    assert behavior.policy["prediction"] == "light" and behavior.policy["latency"] == "maximum" and not behavior.policy["script"]


# ------------------------------------------------------------------ the shared silence is fed by the real events -----------------------------------
def test_events_feed_the_shared_clock_and_silence_counts_after_the_last_voice(behavior):
    behavior.select_profile(PROFILE)
    clock = behavior.clock
    clock.t += 10
    assert behavior.shared_silence.silent_for() == pytest.approx(10)
    behavior.observe("speech.started", source="microphone", speaker="Criador", speaker_role="host", utterance_id="u1")
    assert behavior.shared_silence.silent_for() == 0.0
    clock.t += 20
    assert behavior.shared_silence.silent_for() == 0.0, "20 s of Criador talking is not silence"
    behavior.observe("speech.final", source="microphone", speaker="Criador", speaker_role="host", text="beleza vamos", utterance_id="u1")
    clock.t += 2
    assert behavior.shared_silence.silent_for() == pytest.approx(2)


def test_system_tickets_game_facts_and_chat_never_restart_the_shared_silence(behavior):
    behavior.select_profile(PROFILE)
    behavior.clock.t += 6
    for source, role in [("system", "system"), ("game", "system"), ("youtube_chat", "audience"), ("discord", "participant"), ("untrusted_source", "unknown")]:
        behavior.observe("speech.final", source=source, speaker="Corpo no jogo", speaker_role=role, text="FATO: juntei madeira", utterance_id=f"t-{source}")
    assert behavior.shared_silence.silent_for() == pytest.approx(6), "none of those is a person talking"
    behavior.observe("speech.final", source="browser", speaker="Criador", speaker_role="host", text="e aí Célia", utterance_id="typed")
    assert behavior.shared_silence.silent_for() == pytest.approx(0, abs=0.01), "the owner typing does count"


def test_celia_playback_resets_and_defers_the_silence(behavior):
    behavior.select_profile(PROFILE)
    behavior.observe("celia.tts_started", utterance_id="s1")
    behavior.clock.t += 30
    assert behavior.shared_silence.silent_for() == 0.0
    behavior.observe("celia.tts_finished", utterance_id="s1")
    behavior.clock.t += 3
    assert behavior.shared_silence.silent_for() == pytest.approx(3)


def test_a_profile_change_restarts_the_silence_count(behavior):
    behavior.clock.t += 50
    behavior.select_profile(PROFILE)
    assert behavior.shared_silence.silent_for() == pytest.approx(0, abs=0.01)


# ------------------------------------------------------------------ the initiative gate ----------------------------------------------------------
def test_an_opportunity_opens_after_three_seconds_of_silence_with_context(behavior):
    behavior.select_profile(PROFILE)
    speak(behavior)
    behavior.clock.t += 2.9
    assert behavior.initiative_status(celia_busy=False, has_context=True) == (False, "silence_too_short")
    behavior.clock.t += 0.2
    assert behavior.initiative_status(celia_busy=False, has_context=True) == (True, "opportunity_open")


def test_it_stays_quiet_when_nothing_real_happened_or_she_is_busy_or_criador_is_talking(behavior):
    behavior.select_profile(PROFILE)
    behavior.clock.t += 10
    assert behavior.initiative_status(celia_busy=False, has_context=False) == (False, "no_fresh_context")
    assert behavior.initiative_status(celia_busy=True, has_context=True) == (False, "celia_busy")
    behavior.observe("speech.started", source="microphone", speaker="Criador", speaker_role="host", utterance_id="u9")
    assert behavior.initiative_status(celia_busy=False, has_context=True) == (False, "human_speaking")


def test_other_profiles_have_no_initiative_and_never_ask_the_loop_to_wake(behavior):
    behavior.select_profile("chat")
    assert behavior.initiative_policy() is None
    assert behavior.initiative_status(celia_busy=False, has_context=True) == (False, "profile_has_no_initiative")
    assert behavior.seconds_until_initiative() == float("inf")


def test_the_cooldown_spaces_spontaneous_lines_and_the_budget_caps_them(behavior):
    behavior.select_profile(PROFILE)
    behavior.clock.t += 10
    def candidate():
        return behavior.create_candidate(intent_type="cognitive_contribution", semantic_intent="comentar", confidence=0.9, priority=0.8, urgency=0.6,
                                         novelty=0.7, social_value=0.9, interruption_cost=0.1, ttl_sec=12, draft="comentar")
    first = candidate()
    assert behavior.arbitrate(trigger="spontaneous", candidate_id=first.id)["decision"] == "SPEAK"
    held = behavior.arbitrate(trigger="spontaneous", candidate_id=candidate().id)
    assert held["decision"] == "WAIT" and held["reason"] == "v6_spontaneous_cooldown"
    behavior._last_spontaneous_at -= 20  # the cooldown passed
    assert behavior.arbitrate(trigger="spontaneous", candidate_id=candidate().id)["decision"] == "SPEAK"
    behavior.shared_silence.reset()
    status = behavior.initiative_status(celia_busy=False, has_context=True)
    assert status == (False, "silence_too_short")


def test_a_zero_budget_turns_the_initiative_off(behavior):
    behavior.select_profile(PROFILE)
    settings = behavior.profile_settings(PROFILE)["settings"]
    settings["social"]["max_spontaneous_per_minute"] = 0
    behavior.update_profile_settings(PROFILE, settings)
    behavior.clock.t += 10
    assert behavior.initiative_status(celia_busy=False, has_context=True) == (False, "initiative_disabled")
    assert behavior.participation_opportunity() is None


def _candidate(behavior):
    return behavior.create_candidate(intent_type="cognitive_contribution", semantic_intent="comentar", confidence=0.9, priority=0.8, urgency=0.6,
                                     novelty=0.7, social_value=0.9, interruption_cost=0.1, ttl_sec=12, draft="comentar")


@pytest.mark.parametrize("profile,default", [("conversation_autonomous", 4), ("autonomous", 6)])
def test_the_continuous_profiles_use_a_quota_per_minute_and_the_old_caps_do_not_apply(behavior, profile, default):
    behavior.select_profile(profile)
    assert behavior._per_minute_cap() == default  # saved files without the field get the profile default
    settings = behavior.profile_settings(profile)["settings"]
    settings["social"].update(max_spontaneous_per_minute=2, spontaneous_cooldown_seconds=0, max_spontaneous_per_10m=1, inference_budget_per_hour=1)
    behavior.update_profile_settings(profile, settings)
    behavior.clock.t += 10
    spoken = [behavior.arbitrate(trigger="spontaneous", candidate_id=_candidate(behavior).id) for _ in range(3)]
    assert [d["decision"] for d in spoken][:2] == ["SPEAK", "SPEAK"], "10-minute cap of 1 must be ignored"
    assert spoken[2] == {**spoken[2], "decision": "WAIT", "reason": "v6_spontaneous_budget"}
    behavior._proactive_selected_at = [ts - 61 for ts in behavior._proactive_selected_at]  # the minute window moved on (arbitrate reads the real monotonic clock)
    again = behavior.arbitrate(trigger="spontaneous", candidate_id=_candidate(behavior).id)
    assert again["decision"] == "SPEAK", again


def test_a_continuous_profile_is_not_silenced_by_talking_a_lot(behavior):
    behavior.select_profile("autonomous")
    behavior._celia_talk_ms, behavior._human_talk_ms = 60_000, 1_000  # she has been talking almost all the time
    behavior.clock.t += 10
    assert behavior.arbitrate(trigger="spontaneous", candidate_id=_candidate(behavior).id)["reason"] != "overtalk_budget"
    assert behavior.participation_opportunity() is not None


def test_the_other_profiles_keep_the_ten_minute_and_hourly_caps_and_ignore_the_minute_field(behavior):
    behavior.select_profile("cohost")
    assert behavior._per_minute_cap() is None and behavior.initiative_policy() is None


def test_the_silence_threshold_follows_the_saved_setting(behavior):
    behavior.select_profile(PROFILE)
    settings = behavior.profile_settings(PROFILE)["settings"]
    settings["social"]["silence_seconds"] = 7
    behavior.update_profile_settings(PROFILE, settings)
    behavior.clock.t += 5
    assert behavior.initiative_status(celia_busy=False, has_context=True) == (False, "silence_too_short")
    assert behavior.seconds_until_initiative() == pytest.approx(2.0)
    behavior.clock.t += 2.5
    assert behavior.initiative_status(celia_busy=False, has_context=True)[0] is True


def test_the_participation_placeholder_exists_for_this_profile(behavior):
    behavior.select_profile(PROFILE)
    assert behavior.participation_opportunity() is not None


# ------------------------------------------------------------------ the cognitive loop -----------------------------------------------------------
def loop_with(monkeypatch, thought=None, busy=False):
    import celia.core.cognitive_loop as module
    loop = CognitiveLoop()
    monkeypatch.setattr(module, "orchestrator", SimpleNamespace(context_log=SimpleNamespace(recent_summary=lambda max_chars=700: "Criador: vamos minerar")))
    monkeypatch.setattr(loop, "_refresh_minecraft_context", AsyncMock())
    monkeypatch.setattr(loop, "_celia_busy", staticmethod(lambda: busy))
    semantic = AsyncMock(return_value=thought or {"internal_thought": "comentar o ferro", "should_speak": True, "confidence": 0.8, "urgency": 0.5,
                                                   "social_value": 0.8, "provider": "t"})
    monkeypatch.setattr(loop, "_semantic_think", semantic)
    monkeypatch.setattr(loop, "try_dispatch_pending", AsyncMock(return_value=False))
    return loop, semantic


def test_the_loop_thinks_only_when_the_initiative_is_open(behavior, monkeypatch):
    behavior.select_profile(PROFILE)
    loop, semantic = loop_with(monkeypatch)
    speak(behavior)
    asyncio.run(loop._think_once())
    semantic.assert_not_called()  # no silence yet: no LLM call at all
    behavior.clock.t += 3.3
    asyncio.run(loop._think_once())
    semantic.assert_awaited_once()


def test_the_loop_does_not_think_while_criador_talks_or_celia_is_busy(behavior, monkeypatch):
    behavior.select_profile(PROFILE)
    behavior.clock.t += 10
    loop, semantic = loop_with(monkeypatch, busy=True)
    asyncio.run(loop._think_once())
    semantic.assert_not_called()
    loop, semantic = loop_with(monkeypatch, busy=False)
    behavior.observe("speech.started", source="microphone", speaker="Criador", speaker_role="host", utterance_id="u2")
    asyncio.run(loop._think_once())
    semantic.assert_not_called()


def test_the_loop_respects_the_cooldown_after_a_spontaneous_line(behavior, monkeypatch):
    behavior.select_profile(PROFILE)
    behavior.clock.t += 10
    behavior._last_spontaneous_at = behavior.clock() - 5  # she spoke 5 s ago
    loop, semantic = loop_with(monkeypatch)
    import time as real_time
    behavior._last_spontaneous_at = real_time.monotonic() - 5
    asyncio.run(loop._think_once())
    semantic.assert_not_called()


def test_the_loop_wakes_exactly_when_the_silence_threshold_is_reached(behavior):
    behavior.select_profile(PROFILE)
    loop = CognitiveLoop()
    speak(behavior)
    behavior.clock.t += 1.0
    wait = loop._next_wait()
    assert 1.9 <= wait <= 2.2
    behavior.observe("speech.started", source="microphone", speaker="Criador", speaker_role="host", utterance_id="u3")
    assert loop._next_wait() == loop._interval, "while somebody talks it falls back to the normal interval"
    behavior.select_profile("chat")
    assert loop._next_wait() == loop._interval


def test_this_profile_publishes_candidates_and_chat_does_not(behavior, monkeypatch):
    loop = CognitiveLoop()
    monkeypatch.setattr(loop, "try_dispatch_pending", AsyncMock(return_value=False))
    behavior.select_profile("chat")
    asyncio.run(loop._publish_thought_candidate({"should_speak": True, "internal_thought": "x"}))
    assert not behavior.candidates
    behavior.select_profile(PROFILE)
    asyncio.run(loop._publish_thought_candidate({"should_speak": True, "internal_thought": "x"}))
    assert len(behavior.candidates) == 1


def test_the_prompt_for_this_profile_asks_for_real_things_not_canned_lines(behavior):
    behavior.select_profile(PROFILE)
    prompt = CognitiveLoop()._build_prompt("Criador: oi", "", "achou ferro", "", "state=idle")
    assert "CONVERSA AUTÔNOMA" in prompt and "NÃO use frase pronta" in prompt and "achou ferro" in prompt and "Silêncio também é resposta" in prompt


# ------------------------------------------------------------------ revalidation right before the audio ---------------------------------------------
def fake_singleton(monkeypatch, processing=False):
    import celia.celia_singleton as module
    singleton = SimpleNamespace(context=object(), is_processing=processing, submit_input=AsyncMock(return_value=True))
    monkeypatch.setattr(module.CeliaSingleton, "get_instance", classmethod(lambda cls: singleton))
    return singleton


def prepared(behavior, monkeypatch):
    behavior.select_profile(PROFILE)
    speak(behavior)
    behavior.clock.t += 3.5
    loop = CognitiveLoop()
    candidate = behavior.create_candidate(intent_type="cognitive_contribution", semantic_intent="comentar o ferro", confidence=0.9, priority=0.8,
                                          urgency=0.6, novelty=0.7, social_value=0.9, interruption_cost=0.1, ttl_sec=30, draft="comentar o ferro")
    loop._pending_candidate_id = candidate.id
    return loop


def test_a_prepared_line_is_played_when_the_pause_still_exists(behavior, monkeypatch):
    singleton = fake_singleton(monkeypatch)
    loop = prepared(behavior, monkeypatch)
    assert asyncio.run(CognitiveLoop.try_dispatch_pending(loop)) is True
    singleton.submit_input.assert_awaited_once()
    assert "comentar o ferro" in singleton.submit_input.await_args.args[0]


def test_a_prepared_line_is_dropped_if_criador_started_talking_before_it_could_play(behavior, monkeypatch):
    singleton = fake_singleton(monkeypatch)
    loop = prepared(behavior, monkeypatch)
    behavior.shared_silence.human_started()  # a VAD start that has no transcript yet: the revision has not moved
    assert asyncio.run(CognitiveLoop.try_dispatch_pending(loop)) is False
    singleton.submit_input.assert_not_awaited()
    cancelled = [c for c in behavior.candidates.values() if c.status == "CANCELLED"]
    assert cancelled and cancelled[0].reason.startswith("initiative_")


def test_a_prepared_line_is_dropped_if_the_conversation_moved_on(behavior, monkeypatch):
    singleton = fake_singleton(monkeypatch)
    loop = prepared(behavior, monkeypatch)
    behavior.revision += 1  # Criador finished a new sentence meanwhile
    assert asyncio.run(CognitiveLoop.try_dispatch_pending(loop)) is False
    singleton.submit_input.assert_not_awaited()


def test_a_prepared_line_is_dropped_if_the_silence_was_broken_before_playback(behavior, monkeypatch):
    singleton = fake_singleton(monkeypatch)
    loop = prepared(behavior, monkeypatch)
    behavior.shared_silence.human_started()
    behavior.shared_silence.human_ended()  # a short utterance: silence restarted from zero
    assert asyncio.run(CognitiveLoop.try_dispatch_pending(loop)) is False
    singleton.submit_input.assert_not_awaited()


# ------------------------------------------------------------------ voice switching and the frontend timer ---------------------------------------
@pytest.mark.parametrize("text,target,action", [
    ("Célia, modo conversa autônoma", PROFILE, "switch"), ("ativa o modo conversa autônoma", PROFILE, "switch"),
    ("coloca a Célia na conversa autônoma", PROFILE, "switch"), ("não ativa a conversa autônoma", PROFILE, "mention"),
    ("conversa autônoma", PROFILE, "mention"), ("ativa a conversa", "chat", "switch"), ("Célia, modo autônoma", "autonomous", "switch"),
    ("ativa o modo cohost", "cohost", "switch"),
])
def test_voice_switching_never_mistakes_the_compound_name_for_chat_or_autonomous(text, target, action):
    intent = detect_profile_switch_intent(text)
    assert intent.action == action and intent.target == target


def test_the_frontend_idle_timer_is_ignored_for_this_profile():
    from pathlib import Path
    routes = (Path(__file__).resolve().parents[1] / "src" / "celia" / "routes.py").read_text()
    guard = routes.index('if continuous_behavior.profile == "conversation_autonomous":\n                                    # Conversa Autonoma: UM relogio')
    assert guard < routes.index('logger.debug("ai-speak-signal ignorado: Celia ocupada")')
