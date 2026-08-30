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

import asyncio

import pytest

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import SkillContent, build_instruction_skill_package
from powercontext.builtin.artifacts.skill.distribution import (
    RemoteSkillLifecycleError,
    RemoteSkillObservation,
    RemoteSkillOperation,
    RemoteSkillReceipt,
    RemoteSkillReceiptOutcome,
    RemoteTargetAuthenticationError,
    RemoteTargetEnrollmentError,
    RemoteTargetStateError,
)
from powercontext.builtin.artifacts.skill.projection import AgentSkillProjectionState
from powercontext.builtin.persistence.agent_skill_targets import RemoteAgentSkillTargetState
from powercontext.builtin.persistence.artifact_governance import ArtifactLifecycleState
from powercontext.builtin.persistence.skill_publications import SkillPublicationDesiredState
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts


def _package(instruction: str = "Run the release verification."):
    return build_instruction_skill_package(
        SkillContent(
            name="release-check",
            description="Verify a release before publishing it.",
            instructions=instruction,
            validation=("The report passes.",),
        )
    )


async def _approved_package(contexts, scope_id: str, *, target: ArtifactRef | None = None):
    package = _package(
        "Run the updated release verification." if target is not None else "Run the release verification."
    )
    candidate = await contexts.upload_skill_package(scope_id, package.archive_bytes, None, target)
    approved = await contexts.review(scope_id).approve(candidate.candidate_id, candidate.version)
    assert approved.result_artifact is not None
    return approved.result_artifact, package


async def _active_target(service, scope_id: str, agent_kind: str = "codex"):
    enrollment = await service.create_target(scope_id, agent_kind, f"{agent_kind} test machine")
    credential = await service.enroll(
        enrollment.enrollment_code.get_secret_value(),
        f"workspace-{agent_kind}",
        "0.1.0",
        "e" * 64,
        "test-host",
        "powercontext",
    )
    return enrollment, credential


def test_remote_target_has_readable_environment_identity_and_can_be_renamed() -> None:
    async def exercise() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = contexts.remote_skill_distribution()
            _enrollment, credential = await _active_target(service, "project:one")
            status = (await service.list_targets("project:one", target_id=credential.target_id))[0]

            assert status.target.display_name == "codex test machine"
            assert status.target.machine_hostname == "test-host"
            assert status.target.workspace_name == "powercontext"
            renamed = await service.rename_target(
                "project:one",
                credential.target_id,
                status.target.generation,
                "  Hangzhou build machine  ",
            )
            assert renamed.display_name == "Hangzhou build machine"
            assert renamed.target_id == credential.target_id
            assert renamed.credential_verifier == status.target.credential_verifier

            with pytest.raises(RemoteTargetStateError):
                await service.rename_target(
                    "project:one",
                    credential.target_id,
                    status.target.generation,
                    "Stale rename",
                )

    asyncio.run(exercise())


def test_remote_enrollment_is_one_time_and_revocation_invalidates_the_credential() -> None:
    async def exercise() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            service = contexts.remote_skill_distribution()
            enrollment, credential = await _active_target(service, "project:one")

            with pytest.raises(RemoteTargetEnrollmentError):
                await service.enroll(
                    enrollment.enrollment_code.get_secret_value(),
                    "workspace-replay",
                    "0.1.0",
                    None,
                )

            revoked = await service.revoke_target(
                "project:one",
                credential.target_id,
                enrollment.target.generation + 1,
            )
            assert revoked.state is RemoteAgentSkillTargetState.REVOKED
            assert revoked.credential_verifier is None
            with pytest.raises(RemoteTargetAuthenticationError):
                await service.reconcile(
                    credential.credential.get_secret_value(),
                    (),
                    "0.1.0",
                    None,
                )

    asyncio.run(exercise())


def test_remote_publish_reconcile_download_receipt_and_safe_unpublish_converge() -> None:
    async def exercise() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            scope_id = "project:one"
            artifact, package = await _approved_package(contexts, scope_id)
            service = contexts.remote_skill_distribution()
            _enrollment, target = await _active_target(service, scope_id)
            credential = target.credential.get_secret_value()

            publication = await service.publish(scope_id, target.target_id, artifact, None)
            assert publication.generation == 0
            assert publication.state is AgentSkillProjectionState.PENDING
            statuses = await service.list_targets(scope_id, target_id=target.target_id)
            assert len(statuses) == 1
            assert statuses[0].target.target_id == target.target_id
            assert len(statuses[0].publications) == 1
            assert statuses[0].publications[0].artifact_id == publication.artifact_id
            assert statuses[0].publications[0].generation == publication.generation
            assert await service.list_targets(scope_id, target_id="codex-missing") == ()

            reconcile = await service.reconcile(credential, (), "0.1.0", "e" * 64)
            assert len(reconcile.actions) == 1
            install = reconcile.actions[0]
            assert install.operation is RemoteSkillOperation.INSTALL
            assert install.package == package.reference
            assert (await service.download(credential, install.generation, artifact, package.reference)).reference == (
                package.reference
            )

            installed = await service.receipt(
                credential,
                RemoteSkillReceipt(
                    operation=RemoteSkillOperation.INSTALL,
                    generation=install.generation,
                    artifact=artifact,
                    expected_tree_digest=package.reference.tree_digest,
                    observed_tree_digest=package.reference.tree_digest,
                    outcome=RemoteSkillReceiptOutcome.SUCCEEDED,
                    receiver_version="0.1.0",
                    environment_fingerprint="e" * 64,
                ),
            )
            assert installed.publication.generation == 0
            assert installed.publication.observed_generation == 0
            assert installed.publication.state is AgentSkillProjectionState.CURRENT

            observation = RemoteSkillObservation(
                artifact=artifact,
                tree_digest=package.reference.tree_digest,
                actual_tree_digest=package.reference.tree_digest,
                skill_name="release-check",
                applied_generation=0,
            )
            assert (await service.reconcile(credential, (observation,), "0.1.0", "e" * 64)).actions == ()

            unpublished = await service.unpublish(scope_id, target.target_id, artifact.artifact_id, 0)
            assert unpublished.generation == 1
            assert unpublished.desired_state is SkillPublicationDesiredState.UNPUBLISHED
            remove = (await service.reconcile(credential, (observation,), "0.1.0", "e" * 64)).actions[0]
            assert remove.operation is RemoteSkillOperation.UNPUBLISH
            assert remove.expected_local == observation

            removed = await service.receipt(
                credential,
                RemoteSkillReceipt(
                    operation=RemoteSkillOperation.UNPUBLISH,
                    generation=remove.generation,
                    artifact=artifact,
                    expected_tree_digest=package.reference.tree_digest,
                    outcome=RemoteSkillReceiptOutcome.SUCCEEDED,
                    receiver_version="0.1.0",
                    environment_fingerprint="e" * 64,
                ),
            )
            assert removed.publication.state is AgentSkillProjectionState.UNPUBLISHED
            assert removed.publication.observed_revision is None
            assert (await service.reconcile(credential, (), "0.1.0", "e" * 64)).actions == ()
            with pytest.raises(RemoteTargetAuthenticationError):
                await service.download(credential, remove.generation, artifact, package.reference)

    asyncio.run(exercise())


def test_remote_publish_distinguishes_skill_lifecycle_from_target_state() -> None:
    async def exercise() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            scope_id = "project:one"
            artifact, _package_snapshot = await _approved_package(contexts, scope_id)
            service = contexts.remote_skill_distribution()
            _enrollment, target = await _active_target(service, scope_id)
            await contexts.update_skill_lifecycle(
                scope_id,
                artifact.artifact_id,
                0,
                ArtifactLifecycleState.DEPRECATED,
                None,
            )

            with pytest.raises(RemoteSkillLifecycleError):
                await service.publish(scope_id, target.target_id, artifact, None)

            publication = await service.publish(
                scope_id,
                target.target_id,
                artifact,
                None,
                allow_deprecated=True,
            )
            await contexts.update_skill_lifecycle(
                scope_id,
                artifact.artifact_id,
                1,
                ArtifactLifecycleState.RETIRED,
                None,
            )

            with pytest.raises(RemoteSkillLifecycleError):
                await service.publish(
                    scope_id,
                    target.target_id,
                    artifact,
                    publication.generation,
                    allow_deprecated=True,
                )

    asyncio.run(exercise())


def test_remote_receipt_loss_and_failure_retry_do_not_advance_or_regress_desired_generation() -> None:
    async def exercise() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            scope_id = "project:one"
            artifact, package = await _approved_package(contexts, scope_id)
            service = contexts.remote_skill_distribution()
            _enrollment, target = await _active_target(service, scope_id)
            credential = target.credential.get_secret_value()
            publication = await service.publish(scope_id, target.target_id, artifact, None)

            observation = RemoteSkillObservation(
                artifact=artifact,
                tree_digest=package.reference.tree_digest,
                actual_tree_digest=package.reference.tree_digest,
                skill_name="release-check",
                applied_generation=publication.generation,
            )
            lost_receipt_retry = await service.reconcile(credential, (observation,), "0.1.0", None)
            assert lost_receipt_retry.actions[0].operation is RemoteSkillOperation.INSTALL

            failed_receipt = RemoteSkillReceipt(
                operation=RemoteSkillOperation.INSTALL,
                generation=publication.generation,
                artifact=artifact,
                expected_tree_digest=package.reference.tree_digest,
                outcome=RemoteSkillReceiptOutcome.FAILED,
                failure_state=AgentSkillProjectionState.DELIVERY_FAILED,
                error_code="network_interrupted",
                receiver_version="0.1.0",
            )
            failed = await service.receipt(credential, failed_receipt)
            assert failed.publication.generation == publication.generation
            assert failed.publication.state is AgentSkillProjectionState.DELIVERY_FAILED

            success_receipt = failed_receipt.model_copy(
                update={
                    "outcome": RemoteSkillReceiptOutcome.SUCCEEDED,
                    "failure_state": None,
                    "error_code": None,
                    "observed_tree_digest": package.reference.tree_digest,
                }
            )
            succeeded = await service.receipt(credential, success_receipt)
            assert succeeded.publication.state is AgentSkillProjectionState.CURRENT
            assert succeeded.publication.generation == publication.generation
            late_failure = await service.receipt(credential, failed_receipt)
            assert late_failure.publication.state is AgentSkillProjectionState.CURRENT

            revised_artifact, revised_package = await _approved_package(contexts, scope_id, target=artifact)
            revised = await service.publish(
                scope_id,
                target.target_id,
                revised_artifact,
                publication.generation,
            )
            assert revised.generation == publication.generation + 1
            stale = await service.receipt(credential, success_receipt)
            assert stale.stale is True
            assert stale.accepted is False
            assert stale.publication.desired_tree_digest == revised_package.reference.tree_digest

    asyncio.run(exercise())


def test_reconcile_persists_authenticated_drift_after_a_successful_receipt() -> None:
    async def exercise() -> None:
        async with open_builtin_contexts(BuiltinConfig(database=SQLiteConfig())) as contexts:
            scope_id = "project:one"
            artifact, package = await _approved_package(contexts, scope_id)
            service = contexts.remote_skill_distribution()
            _enrollment, target = await _active_target(service, scope_id)
            credential = target.credential.get_secret_value()
            publication = await service.publish(scope_id, target.target_id, artifact, None)
            succeeded = await service.receipt(
                credential,
                RemoteSkillReceipt(
                    operation=RemoteSkillOperation.INSTALL,
                    generation=publication.generation,
                    artifact=artifact,
                    expected_tree_digest=package.reference.tree_digest,
                    observed_tree_digest=package.reference.tree_digest,
                    outcome=RemoteSkillReceiptOutcome.SUCCEEDED,
                    receiver_version="0.1.0",
                ),
            )
            assert succeeded.publication.state is AgentSkillProjectionState.CURRENT
            drifted_observation = RemoteSkillObservation(
                artifact=artifact,
                tree_digest=package.reference.tree_digest,
                actual_tree_digest="f" * 64,
                skill_name="release-check",
                applied_generation=publication.generation,
            )

            reconcile = await service.reconcile(credential, (drifted_observation,), "0.1.0", None)

            assert reconcile.actions[0].blocked_error_code == "drifted"
            statuses = await service.list_targets(scope_id, target_id=target.target_id)
            observed = statuses[0].publications[0]
            assert observed.state is AgentSkillProjectionState.DRIFTED
            assert observed.last_error_code == "drifted"
            receipt = await service.receipt(
                credential,
                RemoteSkillReceipt(
                    operation=RemoteSkillOperation.INSTALL,
                    generation=publication.generation,
                    artifact=artifact,
                    expected_tree_digest=package.reference.tree_digest,
                    outcome=RemoteSkillReceiptOutcome.FAILED,
                    failure_state=AgentSkillProjectionState.DRIFTED,
                    error_code="drifted",
                    receiver_version="0.1.0",
                ),
            )
            assert receipt.publication.state is AgentSkillProjectionState.DRIFTED

    asyncio.run(exercise())
