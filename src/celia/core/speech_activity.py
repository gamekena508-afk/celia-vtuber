"""Playback identity is independent of persistent character affect."""
from collections import deque


class SpeechActivity:
    def __init__(self):
        self.active = None
        self.retired = deque(maxlen=512)

    def start(self, speech_id, connection_id, player_id=""):
        speech_id = str(speech_id or "").strip()
        player_id = str(player_id or "").strip()
        if not speech_id or speech_id in self.retired:
            return False
        if self.active and self.active[0] == speech_id:
            return False
        if self.active:
            self.retired.append(self.active[0])
        self.active = (speech_id, connection_id, player_id)
        return True

    def finish(self, speech_id, connection_id, player_id=""):
        identity = (str(speech_id or "").strip(), connection_id, str(player_id or "").strip())
        if self.active != identity:
            return False
        self.retired.append(identity[0])
        self.active = None
        return True
