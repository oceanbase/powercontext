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

"""Shared admission rules for Sources used as generation evidence."""

from __future__ import annotations

from dataclasses import dataclass

from powercontext.builtin.sources import ContentSource
from powercontext.sources import Source, SourceRef


@dataclass(frozen=True)
class ArtifactLineageTarget:
    """Exact Artifact revision being committed."""

    scope_id: str
    family: str
    artifact_id: str
    revision: int


class SourceNotEligibleError(ValueError):
    """Report a Source whose server-owned purpose forbids the requested use."""

    def __init__(self, source: SourceRef) -> None:
        self.source = source
        super().__init__("source_not_eligible")


def is_generation_eligible(source: Source, /) -> bool:
    """Return whether a hydrated Source may enter a model or Candidate."""

    return not isinstance(source, ContentSource) or source.internal is None


def require_source_eligible(
    source_ref: SourceRef,
    source: Source,
    /,
    *,
    target: ArtifactLineageTarget | None = None,
) -> None:
    """Allow ordinary evidence, or a lineage-only Source at its exact bound target."""

    if not isinstance(source, ContentSource) or source.internal is None:
        return
    bound = source.internal.target
    if target is not None and (
        bound.scope_id,
        bound.family,
        bound.artifact_id,
        bound.revision,
    ) == (target.scope_id, target.family, target.artifact_id, target.revision):
        return
    raise SourceNotEligibleError(source_ref)
