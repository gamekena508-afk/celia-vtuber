import asyncio
import time
from types import SimpleNamespace

from celia import celia_singleton
from celia.agent.agents.basic_memory_agent import (
    CURRENT_SESSION_DIALOGUE,
    BasicMemoryAgent,
)
from celia.core.continuous_behavior import ContinuousBehaviorRuntime


def _memory_agent_with_generated_assistant(text: str) -> BasicMemoryAgent:
    agent = object.__new__(BasicMemoryAgent)
    agent._interrupt_handled = False
    agent.interrupt_method = "user"
    agent._max_history = 25
    message = {
        "role": "assistant",
        "content": text,
        "origin": CURRENT_SESSION_DIALOGUE,
    }
    agent._memory = [dict(message)]
    agent._current_session_dialogue = [dict(message)]
    return agent


def test_interrupt_memory_keeps_only_assistant_fragment_actually_heard():
    agent = _memory_agent_with_generated_assistant(
        "Eu tinha acabado de afirmar uma coisa bem maior."
    )

    agent.handle_interrupt("Eu tinha acabado")

    assert agent._memory[0]["role"] == "assistant"
    assert agent._memory[0]["content"] == "Eu tinha acabado..."
    assert agent._current_session_dialogue[0]["content"] == "Eu tinha acabado..."
    assert agent._memory[-1]["content"] == "[Interrupted by user]"


def test_interrupt_before_playback_drops_unheard_generated_assistant():
    agent = _memory_agent_with_generated_assistant(
        "Texto gerado mas que nunca começou a tocar."
    )

    agent.handle_interrupt("")

    assert not any(
        item.get("role") == "assistant"
        and "Texto gerado" in item.get("content", "")
        for item in agent._memory
    )
    assert agent._memory[-1]["content"] == "[Interrupted by user]"


def test_new_human_speech_is_never_forwarded_as_assistant_on_interrupt(monkeypatch):
    received = []

    class AgentSpy:
        def handle_interrupt(self, heard_response):
            received.append(heard_response)

    class QueueSpy:
        def clear(self):
            pass

    class SingletonStub:
        def __init__(self, task):
            self._processing = True
            self._current_task = task
            self._event_queue = QueueSpy()
            self.context = SimpleNamespace(agent_engine=AgentSpy())

        async def broadcast(self, payload):
            self.last_broadcast = payload

    monkeypatch.setattr(
        celia_singleton.continuous_behavior,
        "played_speech_text",
        lambda: "Eu falei isso",
    )

    async def scenario():
        task = asyncio.create_task(asyncio.sleep(10))
        stub = SingletonStub(task)
        interrupted = await celia_singleton.CeliaSingleton.interrupt(
            stub,
            "Quando foi que eu disse isso?",
            role="browser",
            force=True,
        )
        return interrupted

    assert asyncio.run(scenario()) is True
    assert received == ["Eu falei isso"]
    assert "Quando foi que eu disse isso?" not in received


def test_played_text_keeps_finished_chunks_and_partial_active_chunk_during_cancel():
    runtime = ContinuousBehaviorRuntime()
    turn_id = runtime.begin_turn("turn-multi")

    first = runtime.create_tts_chunk("Primeiro trecho completo.", turn_id=turn_id)
    runtime.register_speech_duration(first["speech_id"], 1.0)
    runtime.observe("celia.tts_started", utterance_id=first["speech_id"])
    runtime.observe("celia.tts_finished", utterance_id=first["speech_id"])

    second = runtime.create_tts_chunk(
        "Segundo trecho ainda está tocando agora.", turn_id=turn_id
    )
    runtime.register_speech_duration(second["speech_id"], 10.0)
    runtime.observe("celia.tts_started", utterance_id=second["speech_id"])
    runtime._speech_playback_started_at[second["speech_id"]] = time.monotonic() - 5.0

    runtime.request_playback_cancel("human_interruption")
    heard = runtime.played_speech_text()

    assert heard.startswith("Primeiro trecho completo. Segundo trecho ainda")
    assert "tocando agora" not in heard
