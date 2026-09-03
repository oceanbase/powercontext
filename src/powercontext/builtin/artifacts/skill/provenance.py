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

"""User-visible provenance for one managed Skill lineage."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from powercontext.builtin.artifacts.skill.external import ExternalSkillRegistration
from powercontext.sources import SourceRef


class SkillOriginKind(StrEnum):
    """The creation boundary that can be proven from immutable Skill lineage."""

    POWERCONTEXT = "powercontext"
    EXTERNAL_IMPORT = "external_import"
    EXTERNAL_FORK = "external_fork"


class SkillOrigin(BaseModel):
    """A compact Skill origin projection backed by exact persisted evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: SkillOriginKind
    registration: ExternalSkillRegistration | None = None
    source: SourceRef | None = None

    @model_validator(mode="after")
    def require_external_evidence(self) -> SkillOrigin:
        external = self.kind in {SkillOriginKind.EXTERNAL_IMPORT, SkillOriginKind.EXTERNAL_FORK}
        if external != (self.registration is not None and self.source is not None):
            raise ValueError("external Skill origins require registration and Source evidence")  # noqa: TRY003
        return self


__all__ = ["SkillOrigin", "SkillOriginKind"]
