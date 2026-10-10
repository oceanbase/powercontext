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

"""Public controls and exact results for Atomic Memory Artifact search."""

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator

from powercontext.artifacts.search import ArtifactSearchMatch, ArtifactSearchQuery
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemory
from powercontext.builtin.tags import TagFilter


class AtomicMemoryArtifactSearchFilters(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: str | None = Field(default=None, min_length=1, max_length=128)
    tag_filter: TagFilter | None = None

    @field_validator("*", mode="after")
    @classmethod
    def reject_explicit_null(cls, value):
        if value is None:
            raise ValueError("omit the field instead of sending null")  # noqa: TRY003
        return value


class AtomicMemoryArtifactSearchRequest(ArtifactSearchQuery):
    """Delegate bounded current-content search to the Atomic Memory Family."""

    mode: Literal["auto", "text", "vector", "hybrid"] = "auto"
    limit: StrictInt = Field(default=10, ge=1, le=100)
    filters: AtomicMemoryArtifactSearchFilters = Field(default_factory=AtomicMemoryArtifactSearchFilters)


@dataclass(frozen=True)
class AtomicMemoryArtifactSearchOutcome:
    matches: tuple[ArtifactSearchMatch, ...]
    artifacts: tuple[AtomicMemory, ...]


__all__ = ["AtomicMemoryArtifactSearchOutcome", "AtomicMemoryArtifactSearchRequest"]
