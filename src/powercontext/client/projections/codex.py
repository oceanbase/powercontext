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

"""Client adapters for host-local Codex Skill projections."""

from __future__ import annotations

from pathlib import Path

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill.external import AgentSkillTarget
from powercontext.builtin.artifacts.skill.models import SkillContent
from powercontext.builtin.artifacts.skill.projection import (
    PROJECTION_SCHEMA,
    CodexSkillProjectionConflictError,
    CodexSkillProjectionState,
    CodexSkillProjectionStatus,
)
from powercontext.builtin.artifacts.skill.projection import inspect_skill_projection as _inspect_skill_projection
from powercontext.builtin.artifacts.skill.projection import project_skill as _project_skill
from powercontext.builtin.artifacts.skill.projection import publish_skill_projection as _publish_skill_projection
from powercontext.http import SkillProposal


def project_skill(artifact: ArtifactRef, content: SkillProposal, destination: Path, /) -> Path:
    """Create a new host-local Codex projection without replacing existing content."""

    return _project_skill(artifact, _runtime_content(content), _codex_target(destination.parent))


def inspect_skill_projection(
    artifact: ArtifactRef,
    content: SkillProposal,
    root: Path,
    /,
) -> CodexSkillProjectionStatus:
    """Inspect an exact managed Skill projection without changing local files."""

    return _inspect_skill_projection(artifact, _runtime_content(content), _codex_target(root))


def publish_skill_projection(
    artifact: ArtifactRef,
    content: SkillProposal,
    root: Path,
    /,
    *,
    expected: CodexSkillProjectionStatus | None = None,
) -> CodexSkillProjectionStatus:
    """Publish or safely update one exact managed Skill in a configured Codex root."""

    return _publish_skill_projection(artifact, _runtime_content(content), _codex_target(root), expected=expected)


def _codex_target(root: Path) -> AgentSkillTarget:
    return AgentSkillTarget(
        target_id="client",
        agent_kind="codex",
        installation_scope="project",
        path=root,
        allow_managed_publish=True,
    )


def _runtime_content(content: SkillProposal) -> SkillContent:
    return SkillContent(
        name=content.name,
        description=content.description,
        instructions=content.instructions,
        validation=tuple(item.root for item in content.validation),
    )


__all__ = [
    "PROJECTION_SCHEMA",
    "CodexSkillProjectionConflictError",
    "CodexSkillProjectionState",
    "CodexSkillProjectionStatus",
    "inspect_skill_projection",
    "project_skill",
    "publish_skill_projection",
]
