"""Framework-neutral immutable values for model inference."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Generic, TypeAlias, TypeVar

OutputT = TypeVar("OutputT", covariant=True)
EmbeddingVector: TypeAlias = tuple[float, ...]


@dataclass(frozen=True, slots=True)
class InferenceUsage:
    """Portable usage fields reported by one capability call."""

    requests: int
    input_tokens: int | None = None
    output_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class GenerationResult(Generic[OutputT]):
    """A validated structured value and its portable usage metadata."""

    output: OutputT
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Ordered vectors and portable usage metadata for one text batch."""

    vectors: tuple[EmbeddingVector, ...]
    usage: InferenceUsage = field(default_factory=lambda: InferenceUsage(requests=0))
