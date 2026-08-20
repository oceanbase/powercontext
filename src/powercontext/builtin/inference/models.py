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
