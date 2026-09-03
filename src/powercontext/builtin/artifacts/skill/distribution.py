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

"""Credential-bound desired-state reconciliation for remote Agent Skill targets."""

# Domain failures keep bounded contextual detail at the call site while exposing
# stable error codes to HTTP adapters.
# ruff: noqa: TRY003

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hmac import compare_digest
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill.external import AgentKind
from powercontext.builtin.artifacts.skill.models import Skill, SkillPackageRef
from powercontext.builtin.artifacts.skill.package import SkillPackageSnapshot
from powercontext.builtin.artifacts.skill.projection import AgentSkillProjectionState
from powercontext.builtin.persistence.agent_skill_targets import (
    RemoteAgentSkillTarget,
    RemoteAgentSkillTargetRepository,
    RemoteAgentSkillTargetState,
)
from powercontext.builtin.persistence.artifact_governance import (
    ArtifactGovernanceRepository,
    ArtifactLifecycleState,
)
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError, StoredPayloadConflictError
from powercontext.builtin.persistence.skill_packages import SkillPackageRepository
from powercontext.builtin.persistence.skill_publications import (
    SkillPublication,
    SkillPublicationDesiredState,
    SkillPublicationRepository,
)
from powercontext.builtin.sources import validate_scope_id
from powercontext.errors import PowerContextError

Clock = Callable[[], datetime]
ValueFactory = Callable[[], str]
_ENROLLMENT_LIFETIME = timedelta(minutes=10)


class RemoteSkillDistributionError(PowerContextError):
    """Base error with a stable, non-secret code suitable for an HTTP response."""

    code = "remote_skill_distribution_error"

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


class RemoteTargetAuthenticationError(RemoteSkillDistributionError):
    code = "invalid_target_credential"


class RemoteTargetEnrollmentError(RemoteSkillDistributionError):
    code = "invalid_enrollment"


class RemoteTargetStateError(RemoteSkillDistributionError):
    code = "invalid_target_state"


class RemotePublicationGenerationError(RemoteSkillDistributionError):
    code = "publication_generation_conflict"


class RemoteSkillLifecycleError(RemoteSkillDistributionError):
    code = "invalid_skill_lifecycle"


class RemoteTargetEnrollment(BaseModel):
    """Pending target plus the enrollment code returned exactly once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: RemoteAgentSkillTarget
    enrollment_code: SecretStr


class RemoteTargetCredential(BaseModel):
    """Activated target plus its credential returned exactly once."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    target_id: str
    agent_kind: AgentKind
    credential: SecretStr


class RemoteSkillOperation(StrEnum):
    INSTALL = "install"
    UNPUBLISH = "unpublish"


class RemoteSkillReceiptOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class RemoteSkillObservation(BaseModel):
    """Credential-bound ownership checkpoint plus the target's actual tree observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    artifact: ArtifactRef
    tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual_tree_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    skill_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    applied_generation: int = Field(ge=0)


class RemoteSkillAction(BaseModel):
    """One idempotent desired-state action; it never carries a path or executable command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: RemoteSkillOperation
    generation: int = Field(ge=0)
    artifact: ArtifactRef
    tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    skill_name: str = Field(min_length=1, max_length=64, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
    package: SkillPackageRef | None = None
    expected_local: RemoteSkillObservation | None = None
    blocked_error_code: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def validate_operation_payload(self) -> RemoteSkillAction:
        if self.operation is RemoteSkillOperation.INSTALL and self.package is None:
            raise ValueError("install action requires an exact package")
        if self.operation is RemoteSkillOperation.UNPUBLISH and self.package is not None:
            raise ValueError("unpublish action cannot carry a package")
        return self


class RemoteSkillReconcileResult(BaseModel):
    """Latest target intent, with obsolete generations collapsed before delivery."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope_id: str
    target_id: str
    actions: tuple[RemoteSkillAction, ...]


class RemoteSkillReceipt(BaseModel):
    """Bounded evidence for one exact action generation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    operation: RemoteSkillOperation
    generation: int = Field(ge=0)
    artifact: ArtifactRef
    expected_tree_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    observed_tree_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    outcome: RemoteSkillReceiptOutcome
    failure_state: AgentSkillProjectionState | None = None
    error_code: str | None = Field(default=None, min_length=1, max_length=128)
    receiver_version: str = Field(min_length=1, max_length=64)
    environment_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_outcome_payload(self) -> RemoteSkillReceipt:
        failure_states = {
            AgentSkillProjectionState.DELIVERY_FAILED,
            AgentSkillProjectionState.CONFLICT,
            AgentSkillProjectionState.DRIFTED,
            AgentSkillProjectionState.INCOMPATIBLE,
        }
        if self.outcome is RemoteSkillReceiptOutcome.SUCCEEDED:
            if self.failure_state is not None or self.error_code is not None:
                raise ValueError("successful Receipt cannot carry failure details")
            if self.operation is RemoteSkillOperation.INSTALL and self.observed_tree_digest is None:
                raise ValueError("successful install Receipt requires an observed tree digest")
            if self.operation is RemoteSkillOperation.UNPUBLISH and self.observed_tree_digest is not None:
                raise ValueError("successful unpublish Receipt must observe absence")
        elif self.failure_state not in failure_states or self.error_code is None:
            raise ValueError("failed Receipt requires a bounded delivery failure state and code")
        return self


class RemoteSkillReceiptResult(BaseModel):
    """Whether a Receipt changed or already matched the authoritative observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    accepted: bool
    stale: bool
    publication: SkillPublication


class RemoteSkillTargetStatus(BaseModel):
    """Credential-free administrative view of one target and its publications."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    target: RemoteAgentSkillTarget
    publications: tuple[SkillPublication, ...]


class RemoteSkillDistributionService:
    """Own remote target identity, desired publication state, exact package access, and Receipts."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        targets: RemoteAgentSkillTargetRepository,
        artifacts: ArtifactRepository,
        governance: ArtifactGovernanceRepository,
        packages: SkillPackageRepository,
        publications: SkillPublicationRepository,
        clock: Clock | None = None,
        id_factory: ValueFactory | None = None,
        secret_factory: ValueFactory | None = None,
    ) -> None:
        self._database = database
        self._targets = targets
        self._artifacts = artifacts
        self._governance = governance
        self._packages = packages
        self._publications = publications
        self._clock = _utc_now if clock is None else clock
        self._id_factory = _random_id if id_factory is None else id_factory
        self._secret_factory = _random_secret if secret_factory is None else secret_factory

    async def list_targets(
        self,
        scope_id: str,
        /,
        *,
        target_id: str | None = None,
        limit: int = 100,
    ) -> tuple[RemoteSkillTargetStatus, ...]:
        """Return bounded target status without enrollment or installation credentials."""

        scope = validate_scope_id(scope_id)
        async with self._database.transaction() as connection:
            if target_id is None:
                targets = await self._targets.list_for_scope(connection, scope, limit=limit)
            else:
                target = await self._targets.find(connection, scope, target_id)
                targets = () if target is None else (target,)
            statuses: list[RemoteSkillTargetStatus] = []
            for target in targets:
                statuses.append(
                    RemoteSkillTargetStatus(
                        target=target,
                        publications=await self._publications.list_for_target(
                            connection,
                            target.scope_id,
                            target.target_id,
                        ),
                    )
                )
            return tuple(statuses)

    async def create_target(
        self,
        scope_id: str,
        agent_kind: AgentKind,
        display_name: str,
        /,
    ) -> RemoteTargetEnrollment:
        """Create a pending project target and return its one-time enrollment code."""

        scope = validate_scope_id(scope_id)
        now = _as_utc(self._clock())
        target_id = f"{agent_kind.replace('_', '-')}-{self._id_factory()[:12]}"
        enrollment_code = f"pce_{self._secret_factory()}"
        target = RemoteAgentSkillTarget(
            scope_id=scope,
            target_id=target_id,
            display_name=_normalized_target_display_name(display_name),
            agent_kind=agent_kind,
            state=RemoteAgentSkillTargetState.PENDING,
            enrollment_token_digest=_digest(enrollment_code),
            enrollment_expires_at=now + _ENROLLMENT_LIFETIME,
            generation=0,
            created_at=now,
            updated_at=now,
        )
        async with self._database.transaction() as connection:
            await self._targets.create(connection, target)
        return RemoteTargetEnrollment(target=target, enrollment_code=SecretStr(enrollment_code))

    async def enroll(
        self,
        enrollment_code: str,
        installation_id: str,
        receiver_version: str,
        environment_fingerprint: str | None,
        machine_hostname: str | None = None,
        workspace_name: str | None = None,
        /,
    ) -> RemoteTargetCredential:
        """Consume one pending code and bind a unique Receiver installation."""

        now = _as_utc(self._clock())
        async with self._database.transaction() as connection:
            target = await self._targets.find_by_enrollment_token(connection, _digest(enrollment_code))
            if (
                target is None
                or target.state is not RemoteAgentSkillTargetState.PENDING
                or target.generation != 0
                or _as_utc(target.enrollment_expires_at) <= now
            ):
                raise RemoteTargetEnrollmentError("the enrollment code is invalid, expired, or already consumed")
            subject = f"installation-{self._id_factory()}"
            credential = f"pct_{subject}.{self._secret_factory()}"
            active_payload = target.model_copy(
                update={
                    "installation_id": installation_id,
                    "state": RemoteAgentSkillTargetState.ACTIVE,
                    "enrollment_token_digest": None,
                    "enrollment_expires_at": None,
                    "credential_subject": subject,
                    "credential_verifier": _digest(credential),
                    "receiver_version": receiver_version,
                    "environment_fingerprint": environment_fingerprint,
                    "machine_hostname": _normalized_optional_label(machine_hostname, max_length=255),
                    "workspace_name": _normalized_optional_label(workspace_name, max_length=128),
                    "last_seen_at": now,
                }
            )
            try:
                active = await self._targets.replace(connection, active_payload, target.generation)
            except StoredPayloadConflictError as error:
                raise RemoteTargetEnrollmentError("the enrollment code or installation is already bound") from error
        return RemoteTargetCredential(
            scope_id=active.scope_id,
            target_id=active.target_id,
            agent_kind=active.agent_kind,
            credential=SecretStr(credential),
        )

    async def rename_target(
        self,
        scope_id: str,
        target_id: str,
        expected_generation: int,
        display_name: str,
        /,
    ) -> RemoteAgentSkillTarget:
        """Change only the human-readable target name using generation CAS."""

        scope = validate_scope_id(scope_id)
        normalized_name = _normalized_target_display_name(display_name)
        async with self._database.transaction() as connection:
            target = await self._require_target(connection, scope, target_id)
            if target.generation != expected_generation:
                raise RemoteTargetStateError("remote target generation changed")
            if target.display_name == normalized_name:
                return target
            return await self._targets.replace(
                connection,
                target.model_copy(update={"display_name": normalized_name}),
                expected_generation,
            )

    async def revoke_target(
        self,
        scope_id: str,
        target_id: str,
        expected_generation: int,
        /,
    ) -> RemoteAgentSkillTarget:
        """Revoke future target calls while retaining durable identity and publications."""

        scope = validate_scope_id(scope_id)
        async with self._database.transaction() as connection:
            target = await self._require_target(connection, scope, target_id)
            if target.generation != expected_generation:
                raise RemoteTargetStateError("remote target generation changed")
            if target.state is RemoteAgentSkillTargetState.REVOKED:
                return target
            revoked = target.model_copy(
                update={
                    "state": RemoteAgentSkillTargetState.REVOKED,
                    "enrollment_token_digest": None,
                    "enrollment_expires_at": None,
                    "credential_verifier": None,
                    "last_seen_at": target.last_seen_at,
                }
            )
            return await self._targets.replace(connection, revoked, expected_generation)

    async def publish(
        self,
        scope_id: str,
        target_id: str,
        artifact: ArtifactRef,
        expected_generation: int | None,
        /,
        *,
        allow_deprecated: bool = False,
    ) -> SkillPublication:
        """Set one exact approved package as the latest remote desired state."""

        scope = validate_scope_id(scope_id)
        now = _as_utc(self._clock())
        async with self._database.transaction() as connection:
            await self._require_active_target(connection, scope, target_id)
            skill, package = await self._load_package_backed_skill(connection, scope, artifact)
            governance = await self._governance.get(connection, scope, Skill.family, artifact.artifact_id)
            if governance.lifecycle_state is ArtifactLifecycleState.RETIRED:
                raise RemoteSkillLifecycleError("retired managed Skills cannot be published")
            if governance.lifecycle_state is ArtifactLifecycleState.DEPRECATED and not allow_deprecated:
                raise RemoteSkillLifecycleError("deprecated managed Skills require an explicit publication override")
            current = await self._publications.find(connection, scope, target_id, artifact.artifact_id)
            if current is None:
                if expected_generation is not None:
                    raise RemotePublicationGenerationError("remote publication does not exist")
                publication = SkillPublication(
                    scope_id=scope,
                    target_id=target_id,
                    artifact_id=artifact.artifact_id,
                    desired_state=SkillPublicationDesiredState.PUBLISHED,
                    desired_revision=artifact.revision,
                    desired_tree_digest=package.reference.tree_digest,
                    state=AgentSkillProjectionState.PENDING,
                    generation=0,
                    updated_at=now,
                )
                return await self._publications.create(connection, publication)
            self._require_publication_generation(current, expected_generation)
            desired = current.model_copy(
                update={
                    "desired_state": SkillPublicationDesiredState.PUBLISHED,
                    "desired_revision": skill.revision,
                    "desired_tree_digest": package.reference.tree_digest,
                    "destination": None,
                    "state": AgentSkillProjectionState.PENDING,
                    "last_error_code": None,
                }
            )
            if _same_publication_payload(desired, current):
                return current
            return await self._publications.replace(connection, desired, current.generation)

    async def unpublish(
        self,
        scope_id: str,
        target_id: str,
        artifact_id: str,
        expected_generation: int,
        /,
    ) -> SkillPublication:
        """Set desired absence without claiming that the remote filesystem already changed."""

        scope = validate_scope_id(scope_id)
        async with self._database.transaction() as connection:
            await self._require_active_target(connection, scope, target_id)
            current = await self._publications.find(connection, scope, target_id, artifact_id)
            if current is None:
                raise RemoteTargetStateError("remote publication does not exist")
            self._require_publication_generation(current, expected_generation)
            if current.desired_state is SkillPublicationDesiredState.UNPUBLISHED:
                return current
            desired = current.model_copy(
                update={
                    "desired_state": SkillPublicationDesiredState.UNPUBLISHED,
                    "destination": None,
                    "state": AgentSkillProjectionState.PENDING,
                    "last_error_code": None,
                }
            )
            return await self._publications.replace(connection, desired, current.generation)

    async def reconcile(
        self,
        credential: str,
        observations: tuple[RemoteSkillObservation, ...],
        receiver_version: str,
        environment_fingerprint: str | None,
        /,
    ) -> RemoteSkillReconcileResult:
        """Return only latest-generation idempotent actions for the authenticated target."""

        by_artifact = {observation.artifact.artifact_id: observation for observation in observations}
        if len(by_artifact) != len(observations):
            raise RemoteTargetStateError("reconcile observations contain duplicate artifact identities")
        now = _as_utc(self._clock())
        async with self._database.transaction() as connection:
            target = await self._authenticate(connection, credential)
            await self._targets.observe(
                connection,
                target,
                receiver_version=receiver_version,
                environment_fingerprint=environment_fingerprint,
                observed_at=now,
            )
            publications = await self._publications.list_for_target(connection, target.scope_id, target.target_id)
            actions: list[RemoteSkillAction] = []
            for publication in publications:
                observation = by_artifact.get(publication.artifact_id)
                verified, observation_error = await self._verify_observation(
                    connection,
                    target.scope_id,
                    publication.artifact_id,
                    observation,
                )
                if observation_error is not None:
                    publication = await self._record_observation_error(
                        connection,
                        publication,
                        observation_error,
                        environment_fingerprint,
                        now,
                    )
                action = await self._reconcile_publication(
                    connection,
                    target.scope_id,
                    publication,
                    verified,
                    observation_error,
                )
                if action is not None:
                    actions.append(action)
        return RemoteSkillReconcileResult(
            scope_id=target.scope_id,
            target_id=target.target_id,
            actions=tuple(actions),
        )

    async def download(
        self,
        credential: str,
        generation: int,
        artifact: ArtifactRef,
        package: SkillPackageRef,
        /,
    ) -> SkillPackageSnapshot:
        """Read only the exact package currently desired by the authenticated target."""

        async with self._database.transaction() as connection:
            target = await self._authenticate(connection, credential)
            publication = await self._publications.find(
                connection,
                target.scope_id,
                target.target_id,
                artifact.artifact_id,
            )
            if (
                publication is None
                or publication.desired_state is not SkillPublicationDesiredState.PUBLISHED
                or publication.generation != generation
                or publication.desired_revision != artifact.revision
                or publication.desired_tree_digest != package.tree_digest
            ):
                raise RemoteTargetAuthenticationError("the target is not authorized for this package")
            skill, stored = await self._load_package_backed_skill(connection, target.scope_id, artifact)
            if skill.content.package != package or stored.reference != package:
                raise RemoteTargetAuthenticationError("the target is not authorized for this package")
            return stored

    async def receipt(self, credential: str, receipt: RemoteSkillReceipt, /) -> RemoteSkillReceiptResult:
        """Apply a generation-bound Receipt with stale rejection and success precedence."""

        now = _as_utc(self._clock())
        async with self._database.transaction() as connection:
            target = await self._authenticate(connection, credential)
            await self._targets.observe(
                connection,
                target,
                receiver_version=receipt.receiver_version,
                environment_fingerprint=receipt.environment_fingerprint,
                observed_at=now,
            )
            publication = await self._publications.find(
                connection,
                target.scope_id,
                target.target_id,
                receipt.artifact.artifact_id,
            )
            if publication is None:
                raise RemoteTargetStateError("remote publication does not exist")
            if receipt.generation < publication.generation:
                return RemoteSkillReceiptResult(accepted=False, stale=True, publication=publication)
            if receipt.generation > publication.generation:
                raise RemotePublicationGenerationError("Receipt generation is newer than desired state")
            self._validate_receipt_identity(publication, receipt)
            revised = self._receipt_observation(publication, receipt, now)
            if _same_publication_payload(revised, publication):
                return RemoteSkillReceiptResult(accepted=True, stale=False, publication=publication)
            stored = await self._publications.observe(
                connection,
                revised,
                publication.generation,
                preserve_success=receipt.outcome is RemoteSkillReceiptOutcome.FAILED,
            )
            return RemoteSkillReceiptResult(accepted=True, stale=False, publication=stored)

    async def _authenticate(self, connection, credential: str) -> RemoteAgentSkillTarget:
        verifier = _digest(credential)
        target = await self._targets.find_by_credential(connection, verifier)
        if (
            target is None
            or target.state is not RemoteAgentSkillTargetState.ACTIVE
            or target.credential_verifier is None
            or not compare_digest(target.credential_verifier, verifier)
        ):
            raise RemoteTargetAuthenticationError("the target credential is invalid or revoked")
        return target

    async def _require_target(self, connection, scope_id: str, target_id: str) -> RemoteAgentSkillTarget:
        target = await self._targets.find(connection, scope_id, target_id)
        if target is None:
            raise RemoteTargetStateError("remote target does not exist")
        return target

    async def _require_active_target(self, connection, scope_id: str, target_id: str) -> RemoteAgentSkillTarget:
        target = await self._require_target(connection, scope_id, target_id)
        if target.state is not RemoteAgentSkillTargetState.ACTIVE:
            raise RemoteTargetStateError("remote target is not active")
        return target

    async def _load_package_backed_skill(
        self,
        connection,
        scope_id: str,
        artifact: ArtifactRef,
    ) -> tuple[Skill, SkillPackageSnapshot]:
        value = await self._artifacts.get(connection, scope_id, artifact)
        if not isinstance(value, Skill) or value.content.package is None:
            raise RemoteTargetStateError("remote publication requires a package-backed Skill Revision")
        package = await self._packages.get(connection, scope_id, value.content.package)
        return value, package

    async def _verify_observation(
        self,
        connection,
        scope_id: str,
        artifact_id: str,
        observation: RemoteSkillObservation | None,
    ) -> tuple[RemoteSkillObservation | None, str | None]:
        if observation is None:
            return None, None
        if observation.artifact.family != Skill.family or observation.artifact.artifact_id != artifact_id:
            return None, "invalid_checkpoint_identity"
        try:
            skill, package = await self._load_package_backed_skill(connection, scope_id, observation.artifact)
        except (RepositoryNotFoundError, RemoteTargetStateError):
            return None, "unknown_checkpoint_package"
        if package.reference.tree_digest != observation.tree_digest or skill.content.name != observation.skill_name:
            return None, "invalid_checkpoint_digest"
        if observation.actual_tree_digest != observation.tree_digest:
            return None, "drifted"
        return observation, None

    async def _record_observation_error(
        self,
        connection,
        publication: SkillPublication,
        error_code: str,
        environment_fingerprint: str | None,
        observed_at: datetime,
    ) -> SkillPublication:
        state = AgentSkillProjectionState.DRIFTED if error_code == "drifted" else AgentSkillProjectionState.CONFLICT
        observed = publication.model_copy(
            update={
                "observed_generation": publication.generation,
                "state": state,
                "last_error_code": error_code,
                "environment_fingerprint": environment_fingerprint,
                "observed_at": observed_at,
            }
        )
        return await self._publications.observe(
            connection,
            observed,
            publication.generation,
            preserve_success=False,
        )

    async def _reconcile_publication(
        self,
        connection,
        scope_id: str,
        publication: SkillPublication,
        observation: RemoteSkillObservation | None,
        observation_error: str | None,
    ) -> RemoteSkillAction | None:
        artifact = ArtifactRef(
            family=Skill.family,
            artifact_id=publication.artifact_id,
            revision=publication.desired_revision,
        )
        skill, package = await self._load_package_backed_skill(connection, scope_id, artifact)
        if publication.desired_state is SkillPublicationDesiredState.PUBLISHED:
            if (
                observation is not None
                and observation.artifact == artifact
                and observation.tree_digest == publication.desired_tree_digest
                and publication.observed_generation == publication.generation
                and publication.observed_revision == artifact.revision
                and publication.observed_tree_digest == publication.desired_tree_digest
                and publication.state is AgentSkillProjectionState.CURRENT
            ):
                return None
            return RemoteSkillAction(
                operation=RemoteSkillOperation.INSTALL,
                generation=publication.generation,
                artifact=artifact,
                tree_digest=publication.desired_tree_digest,
                skill_name=skill.content.name,
                package=package.reference,
                expected_local=observation,
                blocked_error_code=observation_error,
            )
        if (
            observation is None
            and observation_error is None
            and publication.observed_generation == publication.generation
            and publication.state is AgentSkillProjectionState.UNPUBLISHED
        ):
            return None
        return RemoteSkillAction(
            operation=RemoteSkillOperation.UNPUBLISH,
            generation=publication.generation,
            artifact=artifact,
            tree_digest=publication.desired_tree_digest,
            skill_name=skill.content.name,
            expected_local=observation,
            blocked_error_code=observation_error,
        )

    @staticmethod
    def _require_publication_generation(
        publication: SkillPublication,
        expected_generation: int | None,
    ) -> None:
        if expected_generation is None or publication.generation != expected_generation:
            raise RemotePublicationGenerationError("remote publication generation changed")

    @staticmethod
    def _validate_receipt_identity(publication: SkillPublication, receipt: RemoteSkillReceipt) -> None:
        expected_operation = (
            RemoteSkillOperation.INSTALL
            if publication.desired_state is SkillPublicationDesiredState.PUBLISHED
            else RemoteSkillOperation.UNPUBLISH
        )
        if (
            receipt.operation is not expected_operation
            or receipt.artifact.family != Skill.family
            or receipt.artifact.artifact_id != publication.artifact_id
            or receipt.artifact.revision != publication.desired_revision
            or receipt.expected_tree_digest != publication.desired_tree_digest
        ):
            raise RemoteTargetStateError("Receipt does not match the latest desired action")
        if (
            receipt.outcome is RemoteSkillReceiptOutcome.SUCCEEDED
            and receipt.operation is RemoteSkillOperation.INSTALL
            and receipt.observed_tree_digest != publication.desired_tree_digest
        ):
            raise RemoteTargetStateError("successful install Receipt digest does not match desired package")

    @staticmethod
    def _receipt_observation(
        publication: SkillPublication,
        receipt: RemoteSkillReceipt,
        observed_at: datetime,
    ) -> SkillPublication:
        if receipt.outcome is RemoteSkillReceiptOutcome.FAILED:
            return publication.model_copy(
                update={
                    "observed_generation": publication.generation,
                    "state": receipt.failure_state,
                    "last_error_code": receipt.error_code,
                    "observed_at": observed_at,
                    "environment_fingerprint": receipt.environment_fingerprint,
                }
            )
        if receipt.operation is RemoteSkillOperation.INSTALL:
            return publication.model_copy(
                update={
                    "observed_revision": receipt.artifact.revision,
                    "observed_tree_digest": receipt.observed_tree_digest,
                    "observed_generation": publication.generation,
                    "state": AgentSkillProjectionState.CURRENT,
                    "last_error_code": None,
                    "observed_at": observed_at,
                    "environment_fingerprint": receipt.environment_fingerprint,
                }
            )
        return publication.model_copy(
            update={
                "observed_revision": None,
                "observed_tree_digest": None,
                "observed_generation": publication.generation,
                "state": AgentSkillProjectionState.UNPUBLISHED,
                "last_error_code": None,
                "observed_at": observed_at,
                "environment_fingerprint": receipt.environment_fingerprint,
            }
        )


def _same_publication_payload(left: SkillPublication, right: SkillPublication) -> bool:
    return left.model_dump(exclude={"generation", "updated_at", "observed_at"}) == right.model_dump(
        exclude={"generation", "updated_at", "observed_at"}
    )


def _normalized_target_display_name(value: str) -> str:
    normalized = value.strip()
    if not normalized or len(normalized) > 128:
        raise ValueError("remote target display name must contain 1 to 128 characters")
    return normalized


def _normalized_optional_label(value: str | None, *, max_length: int) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > max_length:
        raise ValueError(f"remote target environment label must contain 1 to {max_length} characters")
    return normalized


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime | None) -> datetime:
    if value is None:
        return datetime.min.replace(tzinfo=UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _random_id() -> str:
    return uuid4().hex


def _random_secret() -> str:
    return secrets.token_urlsafe(32)


__all__ = [
    "RemotePublicationGenerationError",
    "RemoteSkillAction",
    "RemoteSkillDistributionError",
    "RemoteSkillDistributionService",
    "RemoteSkillLifecycleError",
    "RemoteSkillObservation",
    "RemoteSkillOperation",
    "RemoteSkillReceipt",
    "RemoteSkillReceiptOutcome",
    "RemoteSkillReceiptResult",
    "RemoteSkillReconcileResult",
    "RemoteSkillTargetStatus",
    "RemoteTargetAuthenticationError",
    "RemoteTargetCredential",
    "RemoteTargetEnrollment",
    "RemoteTargetEnrollmentError",
    "RemoteTargetStateError",
]
