"""Shared silence clock and the initiative gate: no obsolete turns, no talking over anyone, bounded chatter."""
import pytest

from celia.core.conversation_initiative import InitiativeFacts, InitiativePolicy, decide, still_valid_before_playback
from celia.core.shared_silence import SharedSilenceClock


class T:
    def __init__(self):
        self.t = 100.0

    def __call__(self):
        return self.t


def clock():
    t = T()
    return t, SharedSilenceClock(t)


# ------------------------------------------------------------------ the clock -------------------------------------------------------------------
def test_silence_counts_from_the_end_of_the_last_voice_not_from_its_start():
    t, c = clock()
    c.reset()
    c.human_started()
    t.t += 10
    assert c.silent_for() == 0.0, "10 s of the owner talking is not silence"
    c.human_ended()
    t.t += 2.9
    assert c.silent_for() == pytest.approx(2.9)


def test_the_start_of_any_voice_resets_the_silence():
    t, c = clock()
    c.reset()
    t.t += 5
    assert c.silent_for() == pytest.approx(5)
    c.human_started()
    assert c.silent_for() == 0.0
    c.human_ended()
    t.t += 1
    assert c.silent_for() == pytest.approx(1)


def test_celia_playback_and_generation_are_not_silence():
    t, c = clock()
    c.reset()
    c.generating_started()
    t.t += 30
    assert c.silent_for() == 0.0 and c.active() == ["celia_generating"]
    c.generating_ended()
    c.playback_started()
    t.t += 30
    assert c.silent_for() == 0.0
    c.playback_ended()
    t.t += 3
    assert c.silent_for() == pytest.approx(3)


def test_overlapping_voices_wait_for_all_of_them():
    t, c = clock()
    c.reset()
    c.human_started()
    c.playback_started()
    t.t += 5
    c.playback_ended()
    t.t += 5
    assert c.silent_for() == 0.0, "the owner is still talking"
    c.human_ended()
    t.t += 1
    assert c.silent_for() == pytest.approx(1)


def test_a_lost_end_event_cannot_freeze_the_clock_forever():
    t, c = clock()
    c.reset()
    c.human_started()  # its "ended" never arrives
    t.t += 200
    assert c.active() == [] and c.silent_for() == pytest.approx(80, abs=1)


def test_duplicate_and_out_of_order_events_are_harmless():
    t, c = clock()
    c.reset()
    c.human_ended()  # a final transcript with no matching start
    c.human_ended()
    c.playback_ended()
    t.t += 4
    assert c.silent_for() == pytest.approx(4)
    c.human_started()
    c.human_started()
    c.human_ended()
    t.t += 1
    assert c.silent_for() == pytest.approx(1)


def test_seconds_until_a_threshold_is_exact_and_infinite_while_anyone_talks():
    t, c = clock()
    c.reset()
    t.t += 1
    assert c.seconds_until(3.0) == pytest.approx(2.0)
    c.human_started()
    assert c.seconds_until(3.0) == float("inf")


def test_a_never_started_clock_is_not_silence():
    assert SharedSilenceClock(T()).silent_for() == 0.0


# ------------------------------------------------------------------ the initiative gate -----------------------------------------------------------
POLICY = InitiativePolicy(silence_seconds=3.0, cooldown_seconds=15.0, max_per_10m=4, max_per_hour=10)


def facts(**kw):
    base = dict(silent_for=3.5, since_last_spontaneous=None, spontaneous_10m=0, spontaneous_hour=0, human_active=False, celia_busy=False, has_context=True)
    base.update(kw)
    return InitiativeFacts(**base)


def test_an_opportunity_opens_after_three_seconds_of_real_silence_with_context():
    assert decide(POLICY, facts()) == (True, "opportunity_open")


@pytest.mark.parametrize("kw,reason", [
    ({"human_active": True}, "human_speaking"), ({"celia_busy": True}, "celia_busy"), ({"silent_for": 2.99}, "silence_too_short"),
    ({"has_context": False}, "no_fresh_context"), ({"concentrating": True}, "owner_concentrating"),
    ({"spontaneous_10m": 4}, "budget_10m"), ({"spontaneous_hour": 10}, "budget_hour"), ({"since_last_spontaneous": 14.9}, "cooldown"),
])
def test_every_reason_to_stay_quiet(kw, reason):
    assert decide(POLICY, facts(**kw)) == (False, reason)


def test_three_seconds_is_a_chance_not_an_obligation_and_the_cooldown_spaces_the_lines():
    assert decide(POLICY, facts(since_last_spontaneous=15.0))[0] is True
    assert decide(POLICY, facts(since_last_spontaneous=2.0))[0] is False


def test_zero_budget_disables_initiative_entirely():
    assert decide(InitiativePolicy(max_per_10m=0), facts()) == (False, "initiative_disabled")
    assert decide(InitiativePolicy(max_per_hour=0), facts()) == (False, "initiative_disabled")


def test_concentration_can_be_switched_off_by_the_owner():
    assert decide(InitiativePolicy(respect_concentration=False), facts(concentrating=True))[0] is True


def test_a_line_prepared_in_a_pause_that_criador_just_ended_is_dropped_before_playback():
    ok, why = still_valid_before_playback(POLICY, facts(human_active=True), generated_at_revision=7, current_revision=7)
    assert (ok, why) == (False, "human_started_speaking")
    ok, why = still_valid_before_playback(POLICY, facts(), generated_at_revision=7, current_revision=8)
    assert (ok, why) == (False, "conversation_moved_on")
    assert still_valid_before_playback(POLICY, facts(), generated_at_revision=7, current_revision=7) == (True, "ok")


def test_a_line_is_also_dropped_if_the_silence_condition_no_longer_holds_at_playback_time():
    assert still_valid_before_playback(POLICY, facts(silent_for=0.4), generated_at_revision=1, current_revision=1) == (False, "silence_too_short")


def test_the_recheck_before_playback_ignores_the_generation_of_the_line_itself_but_never_the_human():
    t, c = clock()
    c.reset()
    c.human_started()
    c.human_ended()
    c.generating_started()  # the persona is writing the line
    t.t += 3.2
    assert c.silent_for() == 0.0, "while generating, the strict clock says it is not silent"
    assert c.silent_for(ignore=("celia_generating",)) == pytest.approx(3.2)
    c.human_started()
    assert c.silent_for(ignore=("celia_generating",)) == 0.0, "the owner talking always counts"
