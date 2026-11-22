from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from ..models import ImageRequest, ImageResult, ProviderHealth


class ImageProvider(ABC):
    name: str
    max_references: int = 1

    @abstractmethod
    async def health(self) -> ProviderHealth:
        raise NotImplementedError

    @abstractmethod
    async def generate(self, request: ImageRequest, references: list[Path], prompt_final: str) -> ImageResult:
        raise NotImplementedError
