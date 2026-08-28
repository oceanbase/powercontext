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

"""Project exact managed Skill Revisions into host-local Agent targets."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill.external import AgentKind, AgentSkillTarget
from powercontext.builtin.artifacts.skill.models import SkillContent

PROJECTION_SCHEMA = "powercontext.agent-skill-projection.v1"
LEGACY_CODEX_PROJECTION_SCHEMA = "powercontext.codex-skill-projection.v1"
MAX_CODEX_SKILL_NAME_LENGTH = 64
MAX_CODEX_SKILL_DESCRIPTION_LENGTH = 1_024
MAX_CLAUDE_CODE_SKILL_NAME_LENGTH = 64
MAX_CLAUDE_CODE_SKILL_DESCRIPTION_LENGTH = 1_536
_CODEX_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_CLAUDE_CODE_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class AgentSkillProjectionState(StrEnum):
    """Observable state of one managed Skill in a configured Agent target."""

    UNPUBLISHED = "unpublished"
    CURRENT = "current"
    UPDATE_AVAILABLE = "update_available"
    CONFLICT = "conflict"
    DRIFTED = "drifted"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True)
class AgentSkillProjectionStatus:
    """One exact managed Skill projection state derived from local files."""

    state: AgentSkillProjectionState
    destination: Path
    published_artifact: ArtifactRef | None = None
    reason: str | None = None


class AgentSkillProjectionConflictError(RuntimeError):
    """Raised when publication would replace content outside its exact authority."""

    def __init__(self, status: AgentSkillProjectionStatus) -> None:
        super().__init__(status.reason or status.state.value)
        self.status = status


def project_skill(artifact: ArtifactRef, content: SkillContent, target: AgentSkillTarget, /) -> Path:
    """Create a new host-local Agent projection without replacing existing content."""

    if artifact.family != "skill":
        raise ValueError("artifact must identify a managed Skill")  # noqa: TRY003
    destination = target.path.expanduser().resolve(strict=False) / content.name
    return _project_skill_to(artifact, content, target.agent_kind, destination)


def _project_skill_to(
    artifact: ArtifactRef,
    content: SkillContent,
    agent_kind: AgentKind,
    destination: Path,
) -> Path:
    destination = destination.resolve(strict=False)
    _validate_agent_projection(content, destination, agent_kind)
    if destination.exists():
        raise FileExistsError(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    skill_text = _skill_markdown(artifact, content)
    manifest = {
        "schema": PROJECTION_SCHEMA,
        "agent_kind": agent_kind,
        "artifact": artifact.model_dump(mode="json"),
        "skill_sha256": hashlib.sha256(skill_text.encode("utf-8")).hexdigest(),
    }
    with tempfile.TemporaryDirectory(prefix=".powercontext-skill-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "projection"
        staging.mkdir()
        (staging / "SKILL.md").write_text(skill_text, encoding="utf-8")
        (staging / "powercontext.json").write_text(
            f"{json.dumps(manifest, indent=2, sort_keys=True)}\n",
            encoding="utf-8",
        )
        shutil.copytree(staging, destination)
    return destination


def inspect_skill_projection(
    artifact: ArtifactRef,
    content: SkillContent,
    target: AgentSkillTarget,
    /,
) -> AgentSkillProjectionStatus:
    """Inspect an exact managed Skill projection without changing local files."""

    resolved_root = target.path.expanduser().resolve(strict=False)
    destination = resolved_root / content.name
    try:
        _validate_agent_projection(content, destination, target.agent_kind)
    except ValueError as error:
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.INCOMPATIBLE,
            destination=destination,
            reason=str(error),
        )

    projections = _managed_projections(resolved_root, artifact.artifact_id, target.agent_kind)
    if len(projections) > 1:
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=destination,
            reason="multiple managed projections identify this Artifact",
        )
    if not projections:
        if destination.exists() or destination.is_symlink():
            return AgentSkillProjectionStatus(
                state=AgentSkillProjectionState.CONFLICT,
                destination=destination,
                reason="the target Skill directory is already occupied",
            )
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.UNPUBLISHED,
            destination=destination,
        )

    package, published = projections[0]
    if not _projection_is_intact(package, published, target.agent_kind):
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.DRIFTED,
            destination=destination,
            published_artifact=published,
            reason="the published package no longer matches its PowerContext manifest",
        )
    if package != destination and (destination.exists() or destination.is_symlink()):
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=destination,
            published_artifact=published,
            reason="the renamed target Skill directory is already occupied",
        )
    if published.revision > artifact.revision:
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=destination,
            published_artifact=published,
            reason="a newer managed Skill Revision is already published",
        )
    if published.revision == artifact.revision:
        if package == destination:
            return AgentSkillProjectionStatus(
                state=AgentSkillProjectionState.CURRENT,
                destination=destination,
                published_artifact=published,
            )
        return AgentSkillProjectionStatus(
            state=AgentSkillProjectionState.DRIFTED,
            destination=destination,
            published_artifact=published,
            reason="the exact managed Skill Revision is published under a different directory name",
        )
    return AgentSkillProjectionStatus(
        state=AgentSkillProjectionState.UPDATE_AVAILABLE,
        destination=destination,
        published_artifact=published,
    )


def publish_skill_projection(
    artifact: ArtifactRef,
    content: SkillContent,
    target: AgentSkillTarget,
    /,
    *,
    expected: AgentSkillProjectionStatus | None = None,
) -> AgentSkillProjectionStatus:
    """Publish or safely update one exact managed Skill in a configured Agent target."""

    current = inspect_skill_projection(artifact, content, target)
    if expected is not None and current != expected:
        raise AgentSkillProjectionConflictError(current)
    if current.state is AgentSkillProjectionState.CURRENT:
        return current
    if current.state not in {
        AgentSkillProjectionState.UNPUBLISHED,
        AgentSkillProjectionState.UPDATE_AVAILABLE,
    }:
        raise AgentSkillProjectionConflictError(current)

    resolved_root = target.path.expanduser().resolve(strict=False)
    resolved_root.mkdir(parents=True, exist_ok=True)
    destination = resolved_root / content.name
    existing = None
    if current.published_artifact is not None:
        projections = _managed_projections(resolved_root, artifact.artifact_id, target.agent_kind)
        if len(projections) != 1:
            raise AgentSkillProjectionConflictError(inspect_skill_projection(artifact, content, target))
        existing = projections[0][0]

    temporary = Path(tempfile.mkdtemp(prefix=".powercontext-publish-", dir=resolved_root))
    backup = temporary / "previous"
    try:
        staged = _project_skill_to(
            artifact,
            content,
            target.agent_kind,
            temporary / "staged" / content.name,
        )
        if existing is not None:
            existing.rename(backup)
        try:
            staged.rename(destination)
        except Exception:
            if existing is not None and backup.exists() and not existing.exists():
                backup.rename(existing)
            raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)

    published = inspect_skill_projection(artifact, content, target)
    if published.state is not AgentSkillProjectionState.CURRENT:
        raise AgentSkillProjectionConflictError(published)
    return published


def _validate_agent_projection(content: SkillContent, destination: Path, agent_kind: AgentKind) -> None:
    maximum_name_length = MAX_CODEX_SKILL_NAME_LENGTH if agent_kind == "codex" else MAX_CLAUDE_CODE_SKILL_NAME_LENGTH
    name_pattern = _CODEX_SKILL_NAME if agent_kind == "codex" else _CLAUDE_CODE_SKILL_NAME
    if len(content.name) > maximum_name_length or name_pattern.fullmatch(content.name) is None:
        raise ValueError(  # noqa: TRY003
            f"managed Skill name must be at most {maximum_name_length} lowercase letters, digits, "
            f"and single hyphens for {_agent_label(agent_kind)}"
        )
    if destination.name != content.name:
        raise ValueError(f"{_agent_label(agent_kind)} Skill directory name must match the managed Skill name")  # noqa: TRY003
    maximum_description_length = (
        MAX_CODEX_SKILL_DESCRIPTION_LENGTH if agent_kind == "codex" else MAX_CLAUDE_CODE_SKILL_DESCRIPTION_LENGTH
    )
    invalid_codex_description = agent_kind == "codex" and any(value in content.description for value in "<>")
    if len(content.description) > maximum_description_length or invalid_codex_description:
        suffix = " and contain no angle brackets" if agent_kind == "codex" else ""
        raise ValueError(  # noqa: TRY003
            f"managed Skill description must be at most {maximum_description_length} characters{suffix} "
            f"for {_agent_label(agent_kind)}"
        )


def _agent_label(agent_kind: AgentKind) -> str:
    return "Codex" if agent_kind == "codex" else "Claude Code"


def _skill_markdown(artifact: ArtifactRef, content: SkillContent) -> str:
    validation = "\n".join(f"- {item}" for item in content.validation)
    exact_ref = f"artifact:{artifact.family}/{artifact.artifact_id}@{artifact.revision}"
    return (
        "---\n"
        f"name: {json.dumps(content.name, ensure_ascii=False)}\n"
        f"description: {json.dumps(content.description, ensure_ascii=False)}\n"
        "---\n\n"
        f"<!-- Generated from {exact_ref}. The Artifact Revision remains authoritative. -->\n\n"
        f"{content.instructions.rstrip()}\n\n"
        "## Validation\n\n"
        f"{validation}\n"
    )


def _managed_projections(root: Path, artifact_id: str, agent_kind: AgentKind) -> list[tuple[Path, ArtifactRef]]:
    if not root.is_dir():
        return []
    projections: list[tuple[Path, ArtifactRef]] = []
    for package in root.iterdir():
        if not package.is_dir() or package.is_symlink():
            continue
        published = _published_artifact(package, agent_kind)
        if published is not None and published.family == "skill" and published.artifact_id == artifact_id:
            projections.append((package, published))
    return projections


def _published_artifact(package: Path, agent_kind: AgentKind) -> ArtifactRef | None:
    try:
        manifest = json.loads((package / "powercontext.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not _manifest_matches_agent(manifest, agent_kind):
            return None
        return ArtifactRef.model_validate(manifest.get("artifact"))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def _projection_is_intact(package: Path, artifact: ArtifactRef, agent_kind: AgentKind) -> bool:
    try:
        if {item.name for item in package.iterdir()} != {"SKILL.md", "powercontext.json"}:
            return False
        manifest = json.loads((package / "powercontext.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or not _manifest_matches_agent(manifest, agent_kind):
            return False
        if ArtifactRef.model_validate(manifest.get("artifact")) != artifact:
            return False
        skill_text = (package / "SKILL.md").read_text(encoding="utf-8")
        return manifest.get("skill_sha256") == hashlib.sha256(skill_text.encode("utf-8")).hexdigest()
    except (OSError, UnicodeError, ValueError, TypeError):
        return False


def _manifest_matches_agent(manifest: dict[str, object], agent_kind: AgentKind) -> bool:
    schema = manifest.get("schema")
    if schema == PROJECTION_SCHEMA:
        return manifest.get("agent_kind") == agent_kind
    return schema == LEGACY_CODEX_PROJECTION_SCHEMA and agent_kind == "codex"


# Compatibility aliases for callers that imported the original Codex-specific names.
CodexSkillProjectionConflictError = AgentSkillProjectionConflictError
CodexSkillProjectionState = AgentSkillProjectionState
CodexSkillProjectionStatus = AgentSkillProjectionStatus


__all__ = [
    "PROJECTION_SCHEMA",
    "AgentSkillProjectionConflictError",
    "AgentSkillProjectionState",
    "AgentSkillProjectionStatus",
    "CodexSkillProjectionConflictError",
    "CodexSkillProjectionState",
    "CodexSkillProjectionStatus",
    "inspect_skill_projection",
    "project_skill",
    "publish_skill_projection",
]
