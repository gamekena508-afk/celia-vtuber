import time

from celia.core.speech_coordinator import SpeechCoordinator


def coordinator():
    return SpeechCoordinator()


def test_direct_user_supersedes_pending_game_commentary():
    c = coordinator()
    game = c.request(source="game_commentary", event_key="m1:2:move", ttl_sec=5)
    assert game is not None and c.valid(game.id)

    direct = c.request(source="direct_user", event_key="turn:criador:3", ttl_sec=5)
    assert direct is not None
    assert c.valid(direct.id)
    assert c.valid(game.id) is False


def test_human_start_cancels_only_active_spontaneous_playback():
    c = coordinator()
    game = c.request(source="game_commentary", event_key="m1:3:move", ttl_sec=5)
    assert game is not None
    c.bind_speech(game.id, "speech-game")
    c.mark_playing("speech-game")

    assert c.human_started() == "speech-game"
    assert c.valid(game.id) is False


def test_human_start_does_not_cut_direct_answer_as_a_backchannel_precaution():
    c = coordinator()
    direct = c.request(source="direct_user", event_key="turn:1", ttl_sec=5)
    assert direct is not None
    c.bind_speech(direct.id, "speech-direct")
    c.mark_playing("speech-direct")

    assert c.human_started() is None
    ticket = c.ticket_for_speech("speech-direct")
    assert ticket is not None
    assert ticket["status"] == "playing"


def test_same_event_cannot_generate_two_speeches():
    c = coordinator()
    first = c.request(source="game_commentary", event_key="m1:4:move", ttl_sec=5)
    second = c.request(source="game_commentary", event_key="m1:4:move", ttl_sec=5)

    assert first is not None
    assert second is None


def test_short_lived_comment_expires_instead_of_playing_late():
    c = coordinator()
    ticket = c.request(source="game_commentary", ttl_sec=0.01)
    assert ticket is not None
    time.sleep(0.27)
    assert c.valid(ticket.id) is False


def test_committed_human_turn_invalidates_pending_interruptible_speech():
    c = coordinator()
    stale = c.request(source="game_commentary", event_key="m1:5:move", ttl_sec=5)
    direct = c.request(source="direct_user", event_key="turn:old", ttl_sec=5)
    assert stale is not None
    # Direct-user creation already removes the lower-priority game ticket.
    assert c.valid(stale.id) is False
    assert direct is not None and c.valid(direct.id)

    assert c.human_committed() == 1
    assert c.valid(direct.id) is False


def test_non_interruptible_critical_playback_survives_human_commit():
    c = coordinator()
    result = c.request(
        source="critical_game_event",
        event_key="m1:result",
        ttl_sec=5,
        interruptible=False,
    )
    assert result is not None
    c.bind_speech(result.id, "speech-result")
    c.mark_playing("speech-result")

    assert c.human_committed() == 0
    ticket = c.ticket_for_speech("speech-result")
    assert ticket is not None
    assert ticket["status"] == "playing"


def test_revision_match_and_take_guards_reject_stale_ticket():
    c = coordinator()
    ticket = c.request(
        source="game_commentary",
        ttl_sec=5,
        context_revision=4,
        match_id="m1",
        board_revision=8,
        take_id="take-a",
    )
    assert ticket is not None
    assert c.valid(
        ticket.id,
        context_revision=4,
        match_id="m1",
        board_revision=8,
        take_id="take-a",
    )
    assert c.valid(ticket.id, board_revision=9) is False
