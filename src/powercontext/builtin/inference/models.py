# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Framework-neutral immutable values for model inference."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Generic, Literal, TypeAlias, TypeVar

from pydantic import BaseModel, Field

OutputT = TypeVar("OutputT", covariant=True)
EmbeddingVector: TypeAlias = tuple[float, ...]
MAX_INPUT_ENCODINGS = 128


@dataclass(frozen=True, slots=True)
class InputEncodingReport:
    """Local preparation estimates, separate from provider-reported usage.

    Contains no content, references, paths, or content hashes. Reports belong to
    one capability call and are not persisted by the model usage recorder.
    """

    mode: Literal["off", "shadow", "active"]
    stage: str
    codec: Literal["none", "shared-json-v1", "shared-json-v2"]
    reason: str
    original_bytes: int
    original_estimated_tokens: int
    candidate_bytes: int | None
    candidate_estimated_tokens: int | None
    selected_bytes: int
    selected_estimated_tokens: int
    estimator_id: str
    estimator_version: str
    replacements: int = 0
    shared_values: int = 0


class InferenceUsage(BaseModel):
    """Portable usage fields reported by one capability call."""

    requests: int
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    target_model: str | None = None
    incomplete: bool = False
    input_encodings: tuple[InputEncodingReport, ...] = Field(default=(), max_length=MAX_INPUT_ENCODINGS)


def combine_inference_usage(left: InferenceUsage, right: InferenceUsage) -> InferenceUsage:
    """Combine disjoint attempts without treating an unknown count as zero."""

    def total(name: str) -> int | None:
        a, b = getattr(left, name), getattr(right, name)
        return None if a is None or b is None else a + b

    encodings = (left.input_encodings + right.input_encodings)[:MAX_INPUT_ENCODINGS]
    if left.requests == 0:
        return right.model_copy(
            update={"incomplete": left.incomplete or right.incomplete, "input_encodings": encodings}
        )
    if right.requests == 0:
        return left.model_copy(update={"incomplete": left.incomplete or right.incomplete, "input_encodings": encodings})
    return InferenceUsage(
        requests=left.requests + right.requests,
        input_tokens=total("input_tokens"),
        output_tokens=total("output_tokens"),
        cache_read_tokens=total("cache_read_tokens"),
        cache_write_tokens=total("cache_write_tokens"),
        reasoning_tokens=total("reasoning_tokens"),
        target_model=right.target_model if left.target_model in (None, right.target_model) else None,
        incomplete=left.incomplete or right.incomplete,
        input_encodings=encodings,
    )


class GenerationResult(BaseModel, Generic[OutputT]):
    """A validated structured value and its portable usage metadata."""

    output: OutputT
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))


class EmbeddingResult(BaseModel):
    """Ordered vectors and portable usage metadata for one text batch."""

    vectors: tuple[EmbeddingVector, ...]
    usage: InferenceUsage = Field(default_factory=lambda: InferenceUsage(requests=0))


@dataclass(frozen=True)
class ConversationTurn(Generic[OutputT]):
    """One message appended to a text conversation, and the rule its reply must satisfy.

    ``history`` is the opaque value a previous reply returned. A turn that repeats the instructions and history of an
    earlier one sends the same request prefix, which a provider's prefix cache can serve.
    ``check`` parses the reply text. Its ``InvalidInferenceOutputError`` detail is returned to the model for a
    bounded correction, so it must name the broken rule and never repeat content.
    """

    instructions: str
    message: str
    check: Callable[[str], OutputT]
    history: tuple[object, ...] = ()
    metadata: Mapping[str, str | int] | None = None


@dataclass(frozen=True)
class ConversationReply(Generic[OutputT]):
    """A checked reply and the conversation to continue from.

    ``transcript`` is the text of every message in ``history``, in order, so a caller can estimate the input of the
    next turn from what that turn will actually send.
    """

    output: OutputT
    history: tuple[object, ...]
    transcript: tuple[str, ...] = ()
