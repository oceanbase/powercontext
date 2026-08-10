"""Framework-neutral immutable values for model inference."""

from __future__ import annotations

from typing import Generic, TypeAlias, TypeVar

from pydantic import BaseModel, Field

OutputT = TypeVar("OutputT", covariant=True)
EmbeddingVector: TypeAlias = tuple[float, ...]


class InferenceUsage(BaseModel):
    """Portable usage fields reported by one capability call."""

    requests: int
    input_tokens: int | None = None
    output_tokens: int | None = None


class GenerationResult(BaseModel, Generic[OutputT]):
    """A validated structured value and its portable usage metadata."""

    output: OutputT
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))


class EmbeddingResult(BaseModel):
    """Ordered vectors and portable usage metadata for one text batch."""

    vectors: tuple[EmbeddingVector, ...]
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))
