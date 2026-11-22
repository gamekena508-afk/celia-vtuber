"""Shared local-only vision policy for the application and stdio MCP."""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

MODEL = "celia-lfm2-vl-1.6b"
GEMMA_E2B_MODEL = "celia-gemma4-e2b"
DEFAULT_URL = "http://127.0.0.1:8031/v1/chat/completions"
DEFAULT_TIMEOUT = 7.5
UNAVAILABLE_MESSAGE = "A visão local está indisponível agora. Não consegui analisar a imagem."


class VisionUnavailable(RuntimeError):
    def __init__(self, code: str, *, status: int | None = None):
        super().__init__(UNAVAILABLE_MESSAGE)
        self.code, self.status = code, status

    def as_dict(self) -> dict:
        return {"ok": False, "backend": "local", "description": None,
                "error": {"code": self.code, "message": UNAVAILABLE_MESSAGE,
                          "status": self.status, "retryable": self.code != "invalid_configuration"}}


@dataclass(frozen=True)
class VisionPolicy:
    backend: str
    url: str
    model: str
    timeout: float
    enabled: bool

    def require_local(self) -> None:
        parts = urlsplit(self.url)
        allow_gemma = (self.model == GEMMA_E2B_MODEL and
                       os.getenv("CELIA_GEMMA_E2B_ENABLED", "0").strip().lower() in {"1", "true", "on", "yes"})
        if (self.backend != "local" or not (self.model == MODEL or allow_gemma)
                or parts.scheme != "http" or parts.hostname not in {"127.0.0.1", "localhost", "::1"}
                or parts.username or parts.password or parts.query or parts.fragment):
            raise VisionUnavailable("invalid_configuration")
        if not self.enabled:
            raise VisionUnavailable("disabled")


def vision_policy() -> VisionPolicy:
    url = os.getenv("CELIA_LOCAL_VISION_URL", DEFAULT_URL).strip().rstrip("/")
    if url and not url.endswith("/chat/completions"):
        url += "/chat/completions"
    # Canonical timeout wins; keep the old MCP/application spellings as aliases.
    raw = next((os.environ[k] for k in ("CELIA_LOCAL_VISION_TIMEOUT", "CELIA_LOCAL_VISION_TIMEOUT_SEC", "CELIA_VISION_TIMEOUT")
                if k in os.environ), str(DEFAULT_TIMEOUT))
    try:
        timeout = float(raw)
        if not 0.5 <= timeout <= 120:
            raise ValueError
    except ValueError:
        timeout = DEFAULT_TIMEOUT
    return VisionPolicy(
        backend=os.getenv("CELIA_VISION_BACKEND", "local").strip().lower(),
        url=url,
        model=os.getenv("CELIA_LOCAL_VISION_MODEL", MODEL).strip(),
        timeout=timeout,
        enabled=os.getenv("CELIA_LOCAL_VISION_ENABLED", "1").strip().lower() not in {"0", "false", "no"},
    )
