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

"""Bounded evidence captured when an Agent integration observes Skill use."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from powercontext.artifacts import ArtifactRef
from powercontext.sources import AdapterSourceDefinition, Source, SourceMaterialization, SourceRef

SKILL_USAGE_SOURCE_NAME = "skill-usage"


class ObservedInvocation(StrEnum):
    """Whether the integration actually observed invocation."""

    TRUE = "true"
    FALSE = "false"
    UNKNOWN = "unknown"


class ObservedValidation(StrEnum):
    """Bounded validation result reported by the owning integration."""

    PASSED = "passed"
    FAILED = "failed"
    UNKNOWN = "unknown"


class ObservedOutcome(StrEnum):
    """Bounded task outcome reported by the owning integration."""

    SUCCESS = "success"
    FAILURE = "failure"
    UNKNOWN = "unknown"


class SkillUsageCapture(BaseModel):
    """Caller-stable, exact usage observation with no prompt or command body."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observation_id: str = Field(min_length=1, max_length=256)
    skill_ref: ArtifactRef
    package_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    target_id: str = Field(min_length=1, max_length=128)
    selected: bool
    invoked: ObservedInvocation = ObservedInvocation.UNKNOWN
    validation: ObservedValidation = ObservedValidation.UNKNOWN
    outcome: ObservedOutcome = ObservedOutcome.UNKNOWN
    task_source: SourceRef | None = None
    environment_fingerprint: str | None = Field(default=None, pattern=r"^sha256:[0-9a-f]{64}$")


class SkillUsageSource(Source):
    """Immutable Source representation of one bounded usage observation."""

    skill_ref: ArtifactRef
    package_digest: str
    target_id: str
    selected: bool
    invoked: ObservedInvocation
    validation: ObservedValidation
    outcome: ObservedOutcome
    task_source: SourceRef | None = None
    environment_fingerprint: str | None = None


class SkillUsageSourceAdapter:
    """Materialize and read bounded Skill usage evidence."""

    input_class = SkillUsageCapture
    name = SKILL_USAGE_SOURCE_NAME
    source_class = SkillUsageSource

    async def resolve(self, value: SkillUsageCapture, /) -> SkillUsageSource:
        return SkillUsageSource(
            name=value.observation_id,
            materialization=SourceMaterialization.CAPTURED,
            description="Bounded Skill usage observed by the owning Agent integration.",
            skill_ref=value.skill_ref,
            package_digest=value.package_digest,
            target_id=value.target_id,
            selected=value.selected,
            invoked=value.invoked,
            validation=value.validation,
            outcome=value.outcome,
            task_source=value.task_source,
            environment_fingerprint=value.environment_fingerprint,
        )

    async def read(self, source: SkillUsageSource, /) -> SkillUsageCapture:
        return SkillUsageCapture(
            observation_id=source.name,
            skill_ref=source.skill_ref,
            package_digest=source.package_digest,
            target_id=source.target_id,
            selected=source.selected,
            invoked=source.invoked,
            validation=source.validation,
            outcome=source.outcome,
            task_source=source.task_source,
            environment_fingerprint=source.environment_fingerprint,
        )


SKILL_USAGE_SOURCE_ADAPTER = SkillUsageSourceAdapter()
SKILL_USAGE_SOURCE_DEFINITION = AdapterSourceDefinition(SKILL_USAGE_SOURCE_ADAPTER)

__all__ = [
    "SKILL_USAGE_SOURCE_ADAPTER",
    "SKILL_USAGE_SOURCE_DEFINITION",
    "SKILL_USAGE_SOURCE_NAME",
    "ObservedInvocation",
    "ObservedOutcome",
    "ObservedValidation",
    "SkillUsageCapture",
    "SkillUsageSource",
    "SkillUsageSourceAdapter",
]
