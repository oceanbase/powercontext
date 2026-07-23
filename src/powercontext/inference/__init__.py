"""Framework-neutral model inference contracts.

Pydantic AI remains optional and is imported only from
``powercontext.inference.pydantic_ai``.
"""

from powercontext.inference.errors import (
    InferenceError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.inference.models import (
    EmbeddingResult,
    EmbeddingVector,
    GenerationResult,
    InferenceUsage,
)
from powercontext.inference.protocols import EmbeddingModel, StructuredGenerator

__all__ = [
    "EmbeddingModel",
    "EmbeddingResult",
    "EmbeddingVector",
    "GenerationResult",
    "InferenceError",
    "InferenceTimeoutError",
    "InferenceUnavailableError",
    "InferenceUsage",
    "InvalidInferenceOutputError",
    "StructuredGenerator",
]
