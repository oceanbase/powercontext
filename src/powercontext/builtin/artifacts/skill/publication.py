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

"""Safe standard-package publication to configured host-local Agent targets."""

from __future__ import annotations

import asyncio
import shutil
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill.compatibility import (
    SkillCompatibilityState,
    assess_skill_compatibility,
    target_environment_fingerprint,
)
from powercontext.builtin.artifacts.skill.external import AgentSkillTarget
from powercontext.builtin.artifacts.skill.models import Skill
from powercontext.builtin.artifacts.skill.package import (
    SkillPackageError,
    SkillPackageSnapshot,
    capture_skill_directory,
    materialize_skill_package,
)
from powercontext.builtin.artifacts.skill.projection import (
    AgentSkillProjectionConflictError,
    AgentSkillProjectionState,
    AgentSkillProjectionStatus,
    validate_skill_projection_target,
)
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernance,
    ArtifactGovernanceRepository,
    ArtifactLifecycleState,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.skill_packages import SkillPackageRepository
from powercontext.builtin.persistence.skill_publications import (
    SkillPublication,
    SkillPublicationDesiredState,
    SkillPublicationRepository,
)


@dataclass(frozen=True)
class ManagedSkillPublicationStatus:
    """Database-backed publication status exposed to the Runtime and UI."""

    state: AgentSkillProjectionState
    destination: Path
    published_destination: Path | None = None
    published_artifact: ArtifactRef | None = None
    published_tree_digest: str | None = None
    reason: str | None = None
    generation: int | None = None


class ManagedSkillPublicationService:
    """Coordinate package storage, publication CAS, and exact local filesystem state."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        scope_id: str,
        artifacts: ArtifactRepository,
        governance: ArtifactGovernanceRepository,
        packages: SkillPackageRepository,
        publications: SkillPublicationRepository,
        lock: asyncio.Lock,
    ) -> None:
        self._database = database
        self._scope_id = scope_id
        self._artifacts = artifacts
        self._governance = governance
        self._packages = packages
        self._publications = publications
        self._lock = lock

    async def inspect(self, artifact: ArtifactRef, target: AgentSkillTarget, /) -> ManagedSkillPublicationStatus:
        async with self._lock:
            skill, package, publication, _governance = await self._load(artifact, target)
            status = await asyncio.to_thread(_inspect_local, skill, package, target, publication)
            if publication is not None:
                publication = await self._persist_observation(publication, skill, package, target, status)
                status = _with_generation(status, publication.generation)
            return status

    async def publish(
        self,
        artifact: ArtifactRef,
        target: AgentSkillTarget,
        /,
        *,
        allow_deprecated: bool = False,
    ) -> ManagedSkillPublicationStatus:
        if not target.allow_managed_publish:
            raise ValueError("managed publication is not enabled for this Agent target")  # noqa: TRY003
        async with self._lock:
            skill, package, publication, governance = await self._load(artifact, target)
            compatibility = assess_skill_compatibility(skill.content, package, target)
            if compatibility.state is SkillCompatibilityState.INCOMPATIBLE:
                raise ValueError("managed Skill is incompatible with this Agent target")  # noqa: TRY003
            status = await asyncio.to_thread(_inspect_local, skill, package, target, publication)
            if status.state is AgentSkillProjectionState.CURRENT:
                if publication is None:
                    raise AgentSkillProjectionConflictError(_legacy_status(status))
                publication = await self._persist_observation(publication, skill, package, target, status)
                return _with_generation(status, publication.generation)
            if governance.lifecycle_state is ArtifactLifecycleState.RETIRED:
                raise ValueError("retired managed Skills cannot be published or updated")  # noqa: TRY003
            if governance.lifecycle_state is ArtifactLifecycleState.DEPRECATED and not allow_deprecated:
                raise ValueError("deprecated managed Skills require an explicit publication override")  # noqa: TRY003
            if status.state not in {
                AgentSkillProjectionState.UNPUBLISHED,
                AgentSkillProjectionState.UPDATE_AVAILABLE,
            }:
                raise AgentSkillProjectionConflictError(_legacy_status(status))

            publication = await self._record_intent(publication, skill, package, target, status)
            await asyncio.to_thread(_publish_local, skill, package, target, publication)
            observed = publication.model_copy(
                update={
                    "desired_state": SkillPublicationDesiredState.PUBLISHED,
                    "desired_revision": skill.revision,
                    "desired_tree_digest": package.reference.tree_digest,
                    "observed_revision": skill.revision,
                    "observed_tree_digest": package.reference.tree_digest,
                    "destination": str(_destination(skill, target)),
                    "state": AgentSkillProjectionState.CURRENT,
                    "selected_runtime_variant": compatibility.selected_runtime_variant,
                    "environment_fingerprint": target_environment_fingerprint(target),
                    "observed_generation": publication.generation,
                    "observed_at": datetime.now(UTC),
                    "last_error_code": None,
                }
            )
            async with self._database.transaction() as connection:
                publication = await self._publications.observe(
                    connection,
                    observed,
                    publication.generation,
                    preserve_success=False,
                )
            return _with_generation(
                await asyncio.to_thread(_inspect_local, skill, package, target, publication),
                publication.generation,
            )

    async def unpublish(self, artifact: ArtifactRef, target: AgentSkillTarget, /) -> ManagedSkillPublicationStatus:
        if not target.allow_managed_publish:
            raise ValueError("managed publication is not enabled for this Agent target")  # noqa: TRY003
        async with self._lock:
            skill, package, publication, _governance = await self._load(artifact, target)
            if publication is None:
                return await asyncio.to_thread(_inspect_local, skill, package, target, None)
            status = await asyncio.to_thread(_inspect_local, skill, package, target, publication)
            if status.state is AgentSkillProjectionState.UNPUBLISHED:
                return _with_generation(status, publication.generation)
            if status.state not in {
                AgentSkillProjectionState.CURRENT,
                AgentSkillProjectionState.UPDATE_AVAILABLE,
            }:
                raise AgentSkillProjectionConflictError(_legacy_status(status))
            if status.published_destination is None:
                raise AgentSkillProjectionConflictError(_legacy_status(status))

            backup_root, backup = await asyncio.to_thread(_stage_unpublish, status.published_destination, target)
            revised = publication.model_copy(
                update={
                    "desired_state": SkillPublicationDesiredState.UNPUBLISHED,
                    "desired_revision": skill.revision,
                    "desired_tree_digest": package.reference.tree_digest,
                    "observed_revision": None,
                    "observed_tree_digest": None,
                    "destination": str(_destination(skill, target)),
                    "state": AgentSkillProjectionState.UNPUBLISHED,
                    "selected_runtime_variant": _selected_runtime_variant(skill, package, target),
                    "environment_fingerprint": target_environment_fingerprint(target),
                    "observed_generation": publication.generation + 1,
                    "observed_at": datetime.now(UTC),
                    "last_error_code": None,
                }
            )
            try:
                async with self._database.transaction() as connection:
                    publication = await self._publications.replace(connection, revised, publication.generation)
            except BaseException:
                await asyncio.to_thread(_restore_unpublish, backup, status.published_destination, backup_root)
                raise
            await asyncio.to_thread(shutil.rmtree, backup_root, True)
            return ManagedSkillPublicationStatus(
                state=AgentSkillProjectionState.UNPUBLISHED,
                destination=_destination(skill, target),
                generation=publication.generation,
            )

    async def _load(
        self,
        artifact: ArtifactRef,
        target: AgentSkillTarget,
    ) -> tuple[Skill, SkillPackageSnapshot, SkillPublication | None, ArtifactGovernance]:
        async with self._database.transaction() as connection:
            value = await self._artifacts.get(connection, self._scope_id, artifact)
            if not isinstance(value, Skill) or value.content.package is None:
                raise ValueError("managed publication requires a package-backed Skill Revision")  # noqa: TRY003
            package = await self._packages.get(connection, self._scope_id, value.content.package)
            publication = await self._publications.find(
                connection, self._scope_id, target.target_id, artifact.artifact_id
            )
            governance = await self._governance.get(connection, self._scope_id, Skill.family, artifact.artifact_id)
        return value, package, publication, governance

    async def _persist_observation(
        self,
        publication: SkillPublication,
        skill: Skill,
        package: SkillPackageSnapshot,
        target: AgentSkillTarget,
        status: ManagedSkillPublicationStatus,
    ) -> SkillPublication:
        observed = publication.model_copy(
            update={
                "observed_revision": (
                    None if status.published_artifact is None else status.published_artifact.revision
                ),
                "observed_tree_digest": status.published_tree_digest,
                "state": status.state,
                "selected_runtime_variant": _selected_runtime_variant(skill, package, target),
                "environment_fingerprint": target_environment_fingerprint(target),
                "observed_generation": publication.generation,
                "observed_at": datetime.now(UTC),
                "last_error_code": None,
            }
        )
        if observed.model_dump(exclude={"generation", "updated_at"}) == publication.model_dump(
            exclude={"generation", "updated_at"}
        ):
            return publication
        async with self._database.transaction() as connection:
            return await self._publications.observe(
                connection,
                observed,
                publication.generation,
                preserve_success=status.state
                not in {AgentSkillProjectionState.CURRENT, AgentSkillProjectionState.UNPUBLISHED},
            )

    async def _record_intent(
        self,
        publication: SkillPublication | None,
        skill: Skill,
        package: SkillPackageSnapshot,
        target: AgentSkillTarget,
        status: ManagedSkillPublicationStatus,
    ) -> SkillPublication:
        now = datetime.now(UTC)
        if publication is None:
            intent = SkillPublication(
                scope_id=self._scope_id,
                target_id=target.target_id,
                artifact_id=skill.artifact_id,
                desired_state=SkillPublicationDesiredState.PUBLISHED,
                desired_revision=skill.revision,
                desired_tree_digest=package.reference.tree_digest,
                observed_revision=skill.revision,
                observed_tree_digest=package.reference.tree_digest,
                destination=str(_destination(skill, target)),
                state=AgentSkillProjectionState.UNPUBLISHED,
                selected_runtime_variant=_selected_runtime_variant(skill, package, target),
                environment_fingerprint=target_environment_fingerprint(target),
                generation=0,
                updated_at=now,
            )
            async with self._database.transaction() as connection:
                return await self._publications.create(connection, intent)
        intent = publication.model_copy(
            update={
                "desired_state": SkillPublicationDesiredState.PUBLISHED,
                "desired_revision": skill.revision,
                "desired_tree_digest": package.reference.tree_digest,
                "state": status.state,
                "selected_runtime_variant": _selected_runtime_variant(skill, package, target),
                "environment_fingerprint": target_environment_fingerprint(target),
            }
        )
        if intent.model_dump(exclude={"generation", "updated_at"}) == publication.model_dump(
            exclude={"generation", "updated_at"}
        ):
            return publication
        async with self._database.transaction() as connection:
            return await self._publications.replace(connection, intent, publication.generation)


def _selected_runtime_variant(skill: Skill, package: SkillPackageSnapshot, target: AgentSkillTarget) -> str | None:
    return assess_skill_compatibility(skill.content, package, target).selected_runtime_variant


def _inspect_local(  # noqa: C901
    skill: Skill,
    package: SkillPackageSnapshot,
    target: AgentSkillTarget,
    publication: SkillPublication | None,
) -> ManagedSkillPublicationStatus:
    desired = _destination(skill, target)
    try:
        validate_skill_projection_target(skill.content, target)
    except ValueError as error:
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.INCOMPATIBLE,
            destination=desired,
            reason=str(error),
            generation=None if publication is None else publication.generation,
        )
    if publication is None:
        if desired.exists() or desired.is_symlink():
            return ManagedSkillPublicationStatus(
                state=AgentSkillProjectionState.CONFLICT,
                destination=desired,
                reason="the target Skill directory is already occupied",
            )
        return ManagedSkillPublicationStatus(state=AgentSkillProjectionState.UNPUBLISHED, destination=desired)

    if publication.destination is None:
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=desired,
            reason="the stored local publication destination is missing",
            generation=publication.generation,
        )
    published = Path(publication.destination).expanduser().resolve(strict=False)
    root = target.path.expanduser().resolve(strict=False)
    if published.parent != root:
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=desired,
            reason="the stored publication destination is outside the configured Agent target",
            generation=publication.generation,
        )
    if publication.observed_revision is None or publication.observed_tree_digest is None:
        if published.exists() or published.is_symlink():
            return ManagedSkillPublicationStatus(
                state=AgentSkillProjectionState.CONFLICT,
                destination=desired,
                reason="the target Skill directory is occupied outside managed publication authority",
                generation=publication.generation,
            )
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.UNPUBLISHED,
            destination=desired,
            generation=publication.generation,
        )
    if not published.exists() or published.is_symlink():
        state = (
            AgentSkillProjectionState.UNPUBLISHED
            if publication.state is AgentSkillProjectionState.UNPUBLISHED
            else AgentSkillProjectionState.DRIFTED
        )
        return ManagedSkillPublicationStatus(
            state=state,
            destination=desired,
            published_destination=published,
            published_artifact=_observed_artifact(skill, publication),
            published_tree_digest=publication.observed_tree_digest,
            reason=None if state is AgentSkillProjectionState.UNPUBLISHED else "the published package is missing",
            generation=publication.generation,
        )
    try:
        actual = capture_skill_directory(published)
    except (OSError, SkillPackageError):
        return _drifted(skill, desired, published, publication, "the published package is not a valid standard Skill")

    if (
        published == desired
        and actual.reference.tree_digest == publication.desired_tree_digest
        and publication.state in {AgentSkillProjectionState.UNPUBLISHED, AgentSkillProjectionState.UPDATE_AVAILABLE}
    ):
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.CURRENT,
            destination=desired,
            published_destination=published,
            published_artifact=ArtifactRef(
                family="skill", artifact_id=skill.artifact_id, revision=publication.desired_revision
            ),
            published_tree_digest=publication.desired_tree_digest,
            generation=publication.generation,
        )
    if actual.reference.tree_digest != publication.observed_tree_digest:
        return _drifted(skill, desired, published, publication, "the published package was modified locally")
    observed = _observed_artifact(skill, publication)
    if published != desired and (desired.exists() or desired.is_symlink()):
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=desired,
            published_destination=published,
            published_artifact=observed,
            published_tree_digest=publication.observed_tree_digest,
            reason="the renamed target Skill directory is already occupied",
            generation=publication.generation,
        )
    if publication.observed_revision > skill.revision:
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=desired,
            published_destination=published,
            published_artifact=observed,
            published_tree_digest=publication.observed_tree_digest,
            reason="a newer managed Skill Revision is already published",
            generation=publication.generation,
        )
    if publication.observed_revision == skill.revision:
        if published == desired and publication.observed_tree_digest == package.reference.tree_digest:
            return ManagedSkillPublicationStatus(
                state=AgentSkillProjectionState.CURRENT,
                destination=desired,
                published_destination=published,
                published_artifact=observed,
                published_tree_digest=publication.observed_tree_digest,
                generation=publication.generation,
            )
        return ManagedSkillPublicationStatus(
            state=AgentSkillProjectionState.CONFLICT,
            destination=desired,
            published_destination=published,
            published_artifact=observed,
            published_tree_digest=publication.observed_tree_digest,
            reason="the same managed Revision has a different package identity or destination",
            generation=publication.generation,
        )
    return ManagedSkillPublicationStatus(
        state=AgentSkillProjectionState.UPDATE_AVAILABLE,
        destination=desired,
        published_destination=published,
        published_artifact=observed,
        published_tree_digest=publication.observed_tree_digest,
        generation=publication.generation,
    )


def _publish_local(
    skill: Skill,
    package: SkillPackageSnapshot,
    target: AgentSkillTarget,
    publication: SkillPublication,
) -> None:
    current = _inspect_local(skill, package, target, publication)
    if current.state is AgentSkillProjectionState.CURRENT:
        return
    if current.state not in {
        AgentSkillProjectionState.UNPUBLISHED,
        AgentSkillProjectionState.UPDATE_AVAILABLE,
    }:
        raise AgentSkillProjectionConflictError(_legacy_status(current))
    root = target.path.expanduser().resolve(strict=False)
    root.mkdir(parents=True, exist_ok=True)
    desired = _destination(skill, target)
    temporary = Path(tempfile.mkdtemp(prefix=".powercontext-publish-", dir=root))
    backup = temporary / "previous"
    try:
        staged = temporary / "staged" / skill.content.name
        materialize_skill_package(package, staged)
        existing = current.published_destination
        if existing is not None and existing.exists():
            existing.rename(backup)
        try:
            staged.rename(desired)
        except BaseException:
            if existing is not None and backup.exists() and not existing.exists():
                backup.rename(existing)
            raise
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _stage_unpublish(destination: Path, target: AgentSkillTarget) -> tuple[Path, Path]:
    root = target.path.expanduser().resolve(strict=False)
    if destination.parent != root or not destination.is_dir() or destination.is_symlink():
        raise AgentSkillProjectionConflictError(
            AgentSkillProjectionStatus(
                state=AgentSkillProjectionState.DRIFTED,
                destination=destination,
                reason="the managed publication cannot be removed safely",
            )
        )
    temporary = Path(tempfile.mkdtemp(prefix=".powercontext-unpublish-", dir=root))
    backup = temporary / "package"
    destination.rename(backup)
    return temporary, backup


def _restore_unpublish(backup: Path, destination: Path, temporary: Path) -> None:
    try:
        if backup.exists() and not destination.exists():
            backup.rename(destination)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


def _destination(skill: Skill, target: AgentSkillTarget) -> Path:
    return target.path.expanduser().resolve(strict=False) / skill.content.name


def _observed_artifact(skill: Skill, publication: SkillPublication) -> ArtifactRef:
    if publication.observed_revision is None:
        raise ValueError("Skill publication has no observed Revision")  # noqa: TRY003
    return ArtifactRef(family="skill", artifact_id=skill.artifact_id, revision=publication.observed_revision)


def _drifted(
    skill: Skill,
    desired: Path,
    published: Path,
    publication: SkillPublication,
    reason: str,
) -> ManagedSkillPublicationStatus:
    return ManagedSkillPublicationStatus(
        state=AgentSkillProjectionState.DRIFTED,
        destination=desired,
        published_destination=published,
        published_artifact=_observed_artifact(skill, publication),
        published_tree_digest=publication.observed_tree_digest,
        reason=reason,
        generation=publication.generation,
    )


def _with_generation(status: ManagedSkillPublicationStatus, generation: int) -> ManagedSkillPublicationStatus:
    return ManagedSkillPublicationStatus(
        state=status.state,
        destination=status.destination,
        published_destination=status.published_destination,
        published_artifact=status.published_artifact,
        published_tree_digest=status.published_tree_digest,
        reason=status.reason,
        generation=generation,
    )


def _legacy_status(status: ManagedSkillPublicationStatus) -> AgentSkillProjectionStatus:
    return AgentSkillProjectionStatus(
        state=status.state,
        destination=status.destination,
        published_artifact=status.published_artifact,
        reason=status.reason,
    )


__all__ = ["ManagedSkillPublicationService", "ManagedSkillPublicationStatus"]
