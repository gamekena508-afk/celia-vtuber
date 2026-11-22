import asyncio

import celia.core.live_chat_watcher as watcher_module
from celia.core.live_chat_watcher import LiveChatWatcher, LiveChatWatcherConfig


class _DeadChat:
    def __init__(self):
        self.terminated = 0

    def is_alive(self):
        return False

    def terminate(self):
        self.terminated += 1


def _run_without_executor_threads(watcher, monkeypatch):
    async def immediate(function, *args, **kwargs):
        return function(*args, **kwargs)

    monkeypatch.setattr(watcher_module.asyncio, "to_thread", immediate)
    asyncio.run(watcher._run())


def test_dead_chat_stops_simple_watcher_and_terminates_client_once(monkeypatch):
    chat = _DeadChat()
    watcher = LiveChatWatcher(
        singleton=object(),
        config=LiveChatWatcherConfig(
            enabled=True,
            video_id="abcdefghijk",
            curation_enabled=False,
        ),
        chat_factory=lambda **_kwargs: chat,
    )

    _run_without_executor_threads(watcher, monkeypatch)

    assert chat.terminated == 1
    assert watcher._chat is None


def test_dead_chat_stops_curation_watcher_and_terminates_client_once(monkeypatch):
    chat = _DeadChat()
    watcher = LiveChatWatcher(
        singleton=object(),
        config=LiveChatWatcherConfig(
            enabled=True,
            video_id="abcdefghijk",
            curation_enabled=True,
        ),
        chat_factory=lambda **_kwargs: chat,
    )

    _run_without_executor_threads(watcher, monkeypatch)

    assert chat.terminated == 1
    assert watcher._chat is None
