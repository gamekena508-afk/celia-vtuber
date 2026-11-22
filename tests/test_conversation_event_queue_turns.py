import asyncio
import unittest

from celia.core.conversation_event_queue import ConversationEventQueue


class ConversationEventQueueTurnBoundaryTests(unittest.TestCase):
    def test_owner_turns_are_not_merged(self):
        queue = ConversationEventQueue()
        queue.put({"text": "primeira fala", "turn_id": "turn-1"}, source="owner", speaker="Criador")
        queue.put({"text": "segunda fala", "turn_id": "turn-2"}, source="owner", speaker="Criador")

        first = asyncio.run(queue.get_group(5))
        second = asyncio.run(queue.get_group(5))

        self.assertEqual([e.payload["text"] for e in first], ["primeira fala"])
        self.assertEqual([e.payload["text"] for e in second], ["segunda fala"])

    def test_same_owner_turn_can_still_group(self):
        queue = ConversationEventQueue()
        queue.put({"text": "parte um", "turn_id": "turn-1"}, source="owner", speaker="Criador")
        queue.put({"text": "parte dois", "turn_id": "turn-1"}, source="owner", speaker="Criador")

        batch = asyncio.run(queue.get_group(5))

        self.assertEqual(len(batch), 2)


if __name__ == "__main__":
    unittest.main()
