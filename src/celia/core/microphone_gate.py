"""One microphone switch shared by browser/Pet connections in a server session."""


class MicrophoneGate:
    def __init__(self):
        # Conversa com o chat por padrão; o dono liga o ouvido manualmente.
        self.enabled = False
        self.revision = 0

    def set_enabled(self, enabled: bool):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if self.enabled != enabled:
            self.enabled = enabled
            self.revision += 1

    def accepts(self, revision):
        return self.enabled and revision == self.revision

    def snapshot(self):
        return {"type": "microphone-state", "enabled": self.enabled,
                "revision": self.revision}
