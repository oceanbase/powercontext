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
