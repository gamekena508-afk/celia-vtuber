"""Missão EP1, R14: com a série gravando (e depois do "tchau"), o co-host autônomo não fala sozinho; o rótulo interno não vaza mais."""
from pathlib import Path

import pytest

import asyncio

import celia.core.cognitive_loop as cl
import celia.core.minecraft_series as ms


class FakeCenter:
    def __init__(self, state):
        self.state = state

    async def series_state(self):
        return self.state


def muted():
    return asyncio.run(cl._series_mutes_cohost())


@pytest.mark.parametrize("state, expected", [(None, False), ("NO_WORLD", False), ("INTRO_READY", True), ("GAMEPLAY", True), ("TALK_PAUSE", True),
                                          ("EPISODE_END", True)])
def test_cohost_is_muted_while_the_series_records_and_after_the_goodbye(monkeypatch, state, expected):
    monkeypatch.delenv("CELIA_SERIES_COHOST", raising=False)
    monkeypatch.setattr(ms, "_center", FakeCenter(state))
    assert muted() is expected


def test_daemon_offline_means_not_muted(monkeypatch):
    monkeypatch.setattr(ms, "_center", FakeCenter(None))
    assert muted() is False


def test_env_switch_turns_the_cohost_back_on(monkeypatch):
    monkeypatch.setattr(ms, "_center", FakeCenter("GAMEPLAY"))
    monkeypatch.setenv("CELIA_SERIES_COHOST", "1")
    assert muted() is False


def test_internal_label_that_leaked_into_speech_is_gone():
    src = Path(cl.__file__).read_text(encoding="utf-8")
    assert "APROVADA PELO ÁRBITRO" not in src and "CONTRIBUIÇÃO DE CO-HOST" not in src
