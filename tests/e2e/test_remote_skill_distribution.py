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
import base64
import logging
from pathlib import Path
from typing import cast

import httpx
import pytest
from pydantic import SecretStr

from powercontext.builtin.artifacts.skill import SkillContent, build_instruction_skill_package
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.client import PowerContextClient, RemoteSkillReceiver, RemoteSkillReceiverConfig, ServerResponseError
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    CreateRemoteSkillTargetRequest,
    DownloadRemoteSkillPackageRequest,
    EnrollRemoteSkillTargetRequest,
    ListRemoteSkillTargetsRequest,
    ProposeSkillPackageRequest,
    PublishRemoteSkillRequest,
    ReconcileRemoteSkillsRequest,
    RemoteAgentKind,
    RenameRemoteSkillTargetRequest,
    RevokeRemoteSkillTargetRequest,
    UnpublishRemoteSkillRequest,
)
from powercontext.server.app import ServerApplication, create_app


def test_https_remote_receiver_http_vertical_slice_is_exact_isolated_and_reversible(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    async def scenario() -> None:
        package = build_instruction_skill_package(
            SkillContent(
                name="release-check",
                description="Verify a release before publishing it.",
                instructions="Run the release verification.",
                validation=("The report passes.",),
            )
        )
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="https://testserver",
            ) as transport:
                admin = PowerContextClient("https://testserver", http_client=transport)
                candidate = await admin.propose_skill_package(
                    ProposeSkillPackageRequest(
                        scope_id="project:one",
                        archive_base64=base64.b64encode(package.archive_bytes).decode("ascii"),
                    )
                )
                approved = await admin.approve_artifact_candidate(
                    ApproveArtifactCandidateRequest(
                        scope_id="project:one",
                        candidate_id=candidate.candidate_id,
                        expected_version=candidate.version,
                    )
                )
                assert approved.result_artifact is not None

                enrollment = await admin.create_remote_skill_target(
                    CreateRemoteSkillTargetRequest(
                        scope_id="project:one",
                        agent_kind=RemoteAgentKind.CODEX,
                        display_name="Primary build machine",
                    )
                )
                activated = await admin.enroll_remote_skill_target(
                    EnrollRemoteSkillTargetRequest(
                        enrollment_code=enrollment.enrollment_code,
                        installation_id="workspace-primary",
                        receiver_version="0.1.0",
                        machine_hostname="build-host-01",
                        workspace_name="powercontext",
                    )
                )
                activated_status = await admin.list_remote_skill_targets(
                    ListRemoteSkillTargetsRequest(scope_id="project:one", target_id=activated.target_id)
                )
                renamed = await admin.rename_remote_skill_target(
                    RenameRemoteSkillTargetRequest(
                        scope_id="project:one",
                        target_id=activated.target_id,
                        display_name="Hangzhou build machine",
                        expected_generation=activated_status.targets[0].target.generation,
                    )
                )
                assert renamed.display_name == "Hangzhou build machine"
                assert renamed.machine_hostname == "build-host-01"
                assert renamed.workspace_name == "powercontext"
                assert renamed.target_id == activated.target_id
                publication = await admin.publish_remote_skill(
                    PublishRemoteSkillRequest(
                        scope_id="project:one",
                        target_id=activated.target_id,
                        artifact=approved.result_artifact,
                        expected_generation=None,
                    )
                )
                assert publication.generation == 0
                status = await admin.list_remote_skill_targets(
                    ListRemoteSkillTargetsRequest(scope_id="project:one", target_id=activated.target_id)
                )
                assert len(status.targets) == 1
                assert status.targets[0].target.target_id == activated.target_id
                assert status.targets[0].target.display_name == "Hangzhou build machine"
                assert status.targets[0].publications == [publication]

                other_enrollment = await admin.create_remote_skill_target(
                    CreateRemoteSkillTargetRequest(
                        scope_id="project:one",
                        agent_kind=RemoteAgentKind.CLAUDE_CODE,
                        display_name="Other test machine",
                    )
                )
                other = await admin.enroll_remote_skill_target(
                    EnrollRemoteSkillTargetRequest(
                        enrollment_code=other_enrollment.enrollment_code,
                        installation_id="workspace-other",
                        receiver_version="0.1.0",
                    )
                )
                target_client = PowerContextClient(
                    "https://testserver",
                    token=activated.credential,
                    http_client=transport,
                )
                other_client = PowerContextClient(
                    "https://testserver",
                    token=other.credential,
                    http_client=transport,
                )
                pending = await target_client.reconcile_remote_skills(
                    ReconcileRemoteSkillsRequest(observations=[], receiver_version="0.1.0")
                )
                install = pending.actions[0]
                assert install.package is not None
                with pytest.raises(ServerResponseError) as denied:
                    await other_client.download_remote_skill_package(
                        DownloadRemoteSkillPackageRequest(
                            generation=install.generation,
                            artifact=install.artifact,
                            package=install.package,
                        )
                    )
                assert denied.value.status_code == 401

                receiver = RemoteSkillReceiver(
                    RemoteSkillReceiverConfig(
                        server_url="https://testserver",
                        target_id=activated.target_id,
                        credential=SecretStr(activated.credential),
                        agent_kind="codex",
                        workspace=tmp_path,
                    ),
                    client=target_client,
                )
                installed = await receiver.sync()
                assert installed.succeeded == 1
                assert (tmp_path / ".agents/skills/release-check/SKILL.md").is_file()
                assert (await receiver.sync()).requested == 0

                desired_absence = await admin.unpublish_remote_skill(
                    UnpublishRemoteSkillRequest(
                        scope_id="project:one",
                        target_id=activated.target_id,
                        artifact_id=approved.result_artifact.artifact_id,
                        expected_generation=publication.generation,
                    )
                )
                assert desired_absence.generation == 1
                removed = await receiver.sync()
                assert removed.succeeded == 1
                assert not (tmp_path / ".agents/skills/release-check").exists()

                current = await admin.list_remote_skill_targets(
                    ListRemoteSkillTargetsRequest(scope_id="project:one", target_id=activated.target_id)
                )
                await admin.revoke_remote_skill_target(
                    RevokeRemoteSkillTargetRequest(
                        scope_id="project:one",
                        target_id=activated.target_id,
                        expected_generation=current.targets[0].target.generation,
                    )
                )
                caplog.clear()
                with (
                    caplog.at_level(logging.WARNING, logger="powercontext.server.app"),
                    pytest.raises(ServerResponseError) as rejected,
                ):
                    await target_client.reconcile_remote_skills(
                        ReconcileRemoteSkillsRequest(observations=[], receiver_version="0.1.0")
                    )

                record = next(
                    record
                    for record in caplog.records
                    if getattr(record, "operation", None) == "reconcile_remote_skills"
                )
                assert rejected.value.status_code == 401
                assert getattr(record, "error_code", None) == "invalid_target_credential"
                assert record.exc_info is None
                assert "Traceback" not in caplog.text

    asyncio.run(scenario())


def test_remote_enrollment_rejects_non_loopback_cleartext_http() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            app = create_app(application=cast(ServerApplication, runtime))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport:
                client = PowerContextClient(
                    "http://testserver",
                    http_client=transport,
                    trust_transport_security=True,
                )
                enrollment = await client.create_remote_skill_target(
                    CreateRemoteSkillTargetRequest(
                        scope_id="project:one",
                        agent_kind=RemoteAgentKind.CODEX,
                        display_name="Rejected HTTP machine",
                    )
                )
                with pytest.raises(ServerResponseError) as denied:
                    await client.enroll_remote_skill_target(
                        EnrollRemoteSkillTargetRequest(
                            enrollment_code=enrollment.enrollment_code,
                            installation_id="workspace-primary",
                            receiver_version="0.1.0",
                        )
                    )
                assert denied.value.status_code == 422

    asyncio.run(scenario())


def test_remote_enrollment_allows_non_loopback_cleartext_http_only_after_server_opt_in() -> None:
    async def scenario() -> None:
        async with open_builtin_runtime(BuiltinConfig(database=SQLiteConfig())) as runtime:
            app = create_app(
                application=cast(ServerApplication, runtime),
                allow_insecure_remote_http=True,
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport:
                client = PowerContextClient(
                    "http://testserver",
                    http_client=transport,
                    trust_transport_security=True,
                )
                enrollment = await client.create_remote_skill_target(
                    CreateRemoteSkillTargetRequest(
                        scope_id="project:one",
                        agent_kind=RemoteAgentKind.CODEX,
                        display_name="Private HTTP machine",
                    )
                )

                enrolled = await client.enroll_remote_skill_target(
                    EnrollRemoteSkillTargetRequest(
                        enrollment_code=enrollment.enrollment_code,
                        installation_id="workspace-primary",
                        receiver_version="0.1.0",
                    )
                )

                assert enrolled.target_id == enrollment.target.target_id

    asyncio.run(scenario())
