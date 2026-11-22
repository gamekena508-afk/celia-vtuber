from celia.core.speech_activity import SpeechActivity


def test_finish_requires_current_speech_connection_and_player():
    activity = SpeechActivity()
    assert activity.start("speech-1", "ws-a", "player-1") is True
    assert activity.finish("speech-1", "ws-a", "player-old") is False
    assert activity.finish("speech-1", "ws-b", "player-1") is False
    assert activity.active == ("speech-1", "ws-a", "player-1")
    assert activity.finish("speech-1", "ws-a", "player-1") is True
    assert activity.active is None


def test_late_finish_cannot_close_new_playback():
    activity = SpeechActivity()
    assert activity.start("speech-old", "ws-a", "player-1") is True
    assert activity.start("speech-new", "ws-a", "player-2") is True
    assert activity.finish("speech-old", "ws-a", "player-1") is False
    assert activity.active == ("speech-new", "ws-a", "player-2")
    assert activity.finish("speech-new", "ws-a", "player-2") is True


def test_duplicate_or_retired_speech_cannot_restart():
    activity = SpeechActivity()
    assert activity.start("speech-1", "ws-a", "player-1") is True
    assert activity.start("speech-1", "ws-a", "player-2") is False
    assert activity.finish("speech-1", "ws-a", "player-1") is True
    assert activity.start("speech-1", "ws-a", "player-3") is False
