from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

ProviderName = Literal["auto", "gpt", "meta", "flow", "grok"]


@dataclass(slots=True)
class ProviderHealth:
    provider: str
    ok: bool
    code: str = "ok"
    message: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ImageRequest:
    prompt: str
    provider: ProviderName = "auto"
    mode: Literal["auto", "generate", "edit"] = "auto"
    identities: list[str] = field(default_factory=list)
    reference_paths: list[str] = field(default_factory=list)
    use_clipboard: bool = False
    aspect_ratio: str = "1:1"
    open_after: bool = True
    output_dir: str | None = None
    allow_auto_fallback: bool = True


@dataclass(slots=True)
class ProviderAttempt:
    provider: str
    ok: bool
    code: str
    message: str = ""
    duration_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ImageResult:
    ok: bool
    provider: str | None
    prompt_final: str
    references: list[str] = field(default_factory=list)
    output_paths: list[str] = field(default_factory=list)
    duration_ms: int = 0
    status: str = "failed"
    error_code: str | None = None
    error: str | None = None
    attempts: list[ProviderAttempt] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)

    @property
    def primary_output(self) -> Path | None:
        return Path(self.output_paths[0]) if self.output_paths else None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["attempts"] = [a.to_dict() for a in self.attempts]
        return data
