"""One microphone switch shared by browser/Pet connections in a server session."""


from .audio_source import active_source


class MicrophoneGate:
    def __init__(self):
        # Conversa com o chat por padrão; o dono liga o ouvido manualmente.
        self.enabled = False
        self._revision = 0
        self._source = active_source()
        self._clients = {}
        self._owner = None

    def register(self, client, surface):
        if surface == "browser":
            self._clients[client] = surface
            self._choose_owner()

    def unregister(self, client):
        self._clients.pop(client, None)
        self._choose_owner()

    def _choose_owner(self):
        # Only the normal control window can capture physical input.
        # Pet and scene are output surfaces, not competing microphone clients.
        owner = next((c for c, s in self._clients.items() if s == "browser"), None)
        if owner != self._owner:
            self._owner = owner
            self._revision += 1

    def client_allowed(self, client):
        return self.window_allowed and client == self._owner

    @property
    def revision(self):
        source = active_source()
        if source != self._source:
            self._source = source
            self._revision += 1
        return self._revision

    @property
    def window_allowed(self):
        _ = self.revision
        return self._source == "window"

    def set_enabled(self, enabled: bool):
        if type(enabled) is not bool:
            raise ValueError("enabled must be boolean")
        if self.enabled != enabled:
            self.enabled = enabled
            self._revision += 1

    def accepts(self, revision, client=None):
        current = self.revision
        return self.enabled and self.window_allowed and revision == current and (client is None or client == self._owner)

    def snapshot(self, client=None):
        return {"type": "microphone-state", "enabled": self.enabled,
                "revision": self.revision,
                "window_allowed": self.window_allowed if client is None else self.client_allowed(client),
                "capture_surface": self._clients.get(self._owner)}
