import asyncio

from celia.celia_singleton import CeliaSingleton


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(text)


def test_duplicate_final_transcription_is_broadcast_once():
    singleton = CeliaSingleton()
    websocket = FakeWebSocket()
    singleton._connections = {"browser": (websocket, "browser")}

    async def exercise():
        await singleton.broadcast({
            "type": "user-input-transcription",
            "source": "microphone",
            "utterance_id": "u1",
            "text": "Célia, vamos gravar.",
        })
        # The second path (VoiceListener) has no browser utterance id.
        await singleton.broadcast({
            "type": "user-input-transcription",
            "source": "microphone",
            "text": "Célia, vamos gravar.",
        })

    asyncio.run(exercise())

    assert len(websocket.messages) == 1


def test_partial_asr_is_not_deduplicated_as_a_final_transcription():
    singleton = CeliaSingleton()
    websocket = FakeWebSocket()
    singleton._connections = {"browser": (websocket, "browser")}

    async def exercise():
        await singleton.broadcast({
            "type": "asr-partial",
            "utterance_id": "u1",
            "revision": 1,
            "text": "Célia, vamos",
        })
        await singleton.broadcast({
            "type": "user-input-transcription",
            "source": "microphone",
            "utterance_id": "u1",
            "text": "Célia, vamos gravar.",
        })

    asyncio.run(exercise())

    assert len(websocket.messages) == 2


def test_unattributed_chat_transcriptions_are_not_collapsed():
    singleton = CeliaSingleton()
    websocket = FakeWebSocket()
    singleton._connections = {"browser": (websocket, "browser")}

    async def exercise():
        await singleton.broadcast({
            "type": "user-input-transcription",
            "text": "mesma mensagem",
        })
        await singleton.broadcast({
            "type": "user-input-transcription",
            "text": "mesma mensagem",
        })

    asyncio.run(exercise())

    assert len(websocket.messages) == 2
