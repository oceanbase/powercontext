"""Framework-neutral capability ports shared by Artifact Families."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, TypeVar

from powercontext.builtin.inference.models import EmbeddingResult, GenerationResult

if TYPE_CHECKING:
    from powercontext.builtin.artifacts.memory.models import EmbeddingProfile

InputT = TypeVar("InputT", contravariant=True)
OutputT = TypeVar("OutputT", covariant=True)


class StructuredGenerator(Protocol[InputT, OutputT]):
    """Generate one schema-bound result from one schema-bound input."""

    async def generate(self, value: InputT, /) -> GenerationResult[OutputT]:
        """Return validated structured output without exposing provider types."""

        ...


class EmbeddingModel(Protocol):
    """Embed an ordered text batch with one deployment-fixed profile."""

    profile: EmbeddingProfile

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        """Return exactly one ordered vector for each input text."""

        ...
