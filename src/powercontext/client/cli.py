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

"""Typer commands owned by the remote Client SDK."""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import os
import socket
import tempfile
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Never, TypeAlias
from uuid import uuid4

import typer
from pydantic import SecretStr, ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.skill import (
    AgentSkillTarget,
    SkillContent,
    capture_skill_archive,
    materialize_skill_package,
)
from powercontext.builtin.artifacts.skill.projection import validate_skill_projection_target
from powercontext.client.client import PowerContextClient
from powercontext.client.errors import ClientError, ServerResponseError
from powercontext.client.projections import SkillExportTarget, export_skill
from powercontext.client.receiver_service import (
    ReceiverServiceError,
    ReceiverServiceInstallation,
    install_systemd_user_service,
    uninstall_systemd_user_service,
)
from powercontext.client.settings import ClientSettings
from powercontext.client.skill_receiver import (
    RECEIVER_VERSION,
    ReceiverSyncResult,
    RemoteSkillReceiver,
    RemoteSkillReceiverConfig,
    receiver_environment_fingerprint,
    require_remote_skill_server_url,
)
from powercontext.http import (
    AllScopeSelection,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactReference,
    CandidateFamily,
    CandidateStatus,
    Capabilities,
    CreateRemoteSkillTargetRequest,
    EnrollRemoteSkillTargetRequest,
    ExactScopeSelection,
    ExperienceProposal,
    ExternalSkillImportMode,
    ExternalSkillResolution,
    FamilyCount,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetSkillPackageRequest,
    GetSkillRequest,
    GetStatsRequest,
    HealthResponse,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListRemoteSkillTargetsRequest,
    ListRemoteSkillTargetsResponse,
    ModelUsageValue,
    PublishRemoteSkillRequest,
    ReadinessResponse,
    RejectArtifactCandidateRequest,
    RemoteAgentKind,
    RemoteSkillPublication,
    RemoteSkillTarget,
    RemoteSkillTargetStatus,
    RenameRemoteSkillTargetRequest,
    ResolveExternalSkillRequest,
    ReviseArtifactCandidateRequest,
    RevokeRemoteSkillTargetRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopedStats,
    ScopeId,
    ScopeSelection,
    SkillArtifact,
    SkillGenerationOrigin,
    SkillProposal,
    SkillValidationItem,
    SourceReference,
    StatsPeriod,
    SubtreeScopeSelection,
    UnpublishRemoteSkillRequest,
)

HELP_OPTION_NAMES = ("-h", "--help")
_ClientResponse: TypeAlias = (
    ArtifactCandidate
    | ArtifactCandidatePage
    | Capabilities
    | ExternalSkillResolution
    | GeneratedCandidateResponse
    | HealthResponse
    | ListExternalSkillsResponse
    | ListRemoteSkillTargetsResponse
    | ReadinessResponse
    | RemoteSkillPublication
    | RemoteSkillTarget
    | ScanExternalSkillsResponse
    | SkillArtifact
    | ScopedStats
)
_ClientOperation: TypeAlias = Callable[[PowerContextClient], Awaitable[_ClientResponse]]

candidate_app = typer.Typer(
    name="candidate",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Inspect and review Artifact Candidates.",
    no_args_is_help=True,
)
candidate_revise_app = typer.Typer(
    name="revise",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Append a complete replacement proposal to a Candidate.",
    no_args_is_help=True,
)
experience_app = typer.Typer(
    name="experience",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Generate reviewed Experience Candidates.",
    no_args_is_help=True,
)
skill_app = typer.Typer(
    name="skill",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Read and explicitly export approved managed Skills.",
    no_args_is_help=True,
)
external_skill_app = typer.Typer(
    name="external-skill",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Discover and exactly resolve Agent-native Skills on the Server host.",
    no_args_is_help=True,
)
candidate_app.add_typer(candidate_revise_app, name="revise")


@dataclass(frozen=True, slots=True)
class _ClientOptions:
    server_url: str
    api_token: SecretStr | None
    timeout: float
    json_output: bool


@dataclass(frozen=True, slots=True)
class _ClientOverrides:
    server_url: str | None = None
    timeout: float | None = None
    json_output: bool = False


def configure_client(
    context: typer.Context,
    *,
    server_url: str | None,
    timeout: float | None,
    json_output: bool,
) -> None:
    """Store lazy Server connection overrides for content commands."""

    context.meta["powercontext.client.overrides"] = _ClientOverrides(
        server_url=server_url,
        timeout=timeout,
        json_output=json_output,
    )


def capabilities(context: typer.Context) -> None:
    """Show behavior enabled by the remote Server runtime."""

    asyncio.run(_execute(context, lambda client: client.get_capabilities()))


def stats(
    context: typer.Context,
    scope_id: Annotated[list[str] | None, typer.Option(help="Exact application scope to include.")] = None,
    root_scope_id: Annotated[str | None, typer.Option(help="Organization subtree root to include.")] = None,
    period: Annotated[StatsPeriod, typer.Option(help="Bounded UTC statistics period.")] = StatsPeriod.FIELD_30D,
) -> None:
    """Show current inventory and bounded usage for a Scope selection."""

    if scope_id and root_scope_id is not None:
        raise typer.BadParameter("--scope-id and --root-scope-id are mutually exclusive")  # noqa: TRY003
    if root_scope_id is not None:
        selection = ScopeSelection(root=SubtreeScopeSelection(mode="subtree", root_scope_id=root_scope_id))
    elif scope_id:
        selection = ScopeSelection(
            root=ExactScopeSelection(mode="exact", scope_ids=[ScopeId(value) for value in scope_id])
        )
    else:
        selection = ScopeSelection(root=AllScopeSelection(mode="all"))
    request = GetStatsRequest(selection=selection, period=period)
    asyncio.run(_execute(context, lambda client: client.get_stats(request)))


def live(context: typer.Context) -> None:
    """Check whether the remote API process is alive."""

    asyncio.run(_execute(context, lambda client: client.get_liveness()))


def ready(context: typer.Context) -> None:
    """Check whether remote Server bindings are ready."""

    asyncio.run(_execute(context, lambda client: client.get_readiness()))


@candidate_app.command("list")
def list_candidates(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Review Inbox.")],
    status: Annotated[CandidateStatus, typer.Option(help="Candidate lifecycle state.")] = CandidateStatus.PENDING,
    family: Annotated[CandidateFamily | None, typer.Option(help="Optional Artifact Family filter.")] = None,
    cursor: Annotated[str | None, typer.Option(help="Opaque cursor from the previous page.")] = None,
    limit: Annotated[int, typer.Option(min=1, max=100, help="Maximum Candidate heads to return.")] = 50,
) -> None:
    """List current Candidate heads; pending is the default Inbox view."""

    request = ListArtifactCandidatesRequest(
        scope_id=scope_id,
        status=status,
        family=family,
        cursor=cursor,
        limit=limit,
    )
    asyncio.run(_execute(context, lambda client: client.list_artifact_candidates(request)))


@candidate_app.command("show")
def show_candidate(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Candidate.")],
    candidate_id: Annotated[str, typer.Argument(help="Candidate identity.")],
) -> None:
    """Show the current exact Candidate version and evidence."""

    request = GetArtifactCandidateRequest(scope_id=scope_id, candidate_id=candidate_id)
    asyncio.run(_execute(context, lambda client: client.get_artifact_candidate(request)))


@candidate_app.command("approve")
def approve_candidate(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Candidate.")],
    candidate_id: Annotated[str, typer.Argument(help="Candidate identity.")],
    expected_version: Annotated[int, typer.Option(min=1, help="Exact reviewed Candidate version.")],
) -> None:
    """Approve one exact pending Candidate version."""

    request = ApproveArtifactCandidateRequest(
        scope_id=scope_id,
        candidate_id=candidate_id,
        expected_version=expected_version,
    )
    asyncio.run(_execute(context, lambda client: client.approve_artifact_candidate(request)))


@candidate_app.command("reject")
def reject_candidate(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Candidate.")],
    candidate_id: Annotated[str, typer.Argument(help="Candidate identity.")],
    expected_version: Annotated[int, typer.Option(min=1, help="Exact reviewed Candidate version.")],
    reason: Annotated[str, typer.Option(help="Why the proposal was rejected.")],
) -> None:
    """Reject one exact pending Candidate version."""

    request = RejectArtifactCandidateRequest(
        scope_id=scope_id,
        candidate_id=candidate_id,
        expected_version=expected_version,
        reason=reason,
    )
    asyncio.run(_execute(context, lambda client: client.reject_artifact_candidate(request)))


@candidate_revise_app.command("experience")
def revise_experience_candidate(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Candidate.")],
    candidate_id: Annotated[str, typer.Argument(help="Candidate identity.")],
    expected_version: Annotated[int, typer.Option(min=1, help="Exact reviewed Candidate version.")],
    situation: Annotated[str, typer.Option(help="Situation addressed by the replacement proposal.")],
    action: Annotated[str, typer.Option(help="Action taken in the replacement proposal.")],
    outcome: Annotated[str, typer.Option(help="Observed outcome in the replacement proposal.")],
    lesson: Annotated[str, typer.Option(help="Reusable lesson in the replacement proposal.")],
    source_ref: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Exact Source as TYPE/ID; repeat for more evidence."),
    ] = None,
    artifact_ref: Annotated[
        list[str] | None,
        typer.Option("--artifact-ref", help="Exact Artifact as FAMILY/ID@REVISION; repeat for more evidence."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(help="Exact replacement target as FAMILY/ID@REVISION; automatically included as evidence."),
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Why this replacement proposal is requested.")] = None,
) -> None:
    """Append a complete Experience replacement proposal."""

    try:
        sources, artifacts, target_ref = _evidence_references(source_ref, artifact_ref, target)
        _require_target_family(target_ref, family="experience")
        request = ReviseArtifactCandidateRequest(
            scope_id=scope_id,
            candidate_id=candidate_id,
            expected_version=expected_version,
            proposal=ExperienceProposal(
                situation=situation,
                action=action,
                outcome=outcome,
                lesson=lesson,
            ),
            source_refs=sources,
            artifact_refs=artifacts,
            target=target_ref,
            reason=reason,
        )
    except ValidationError as error:
        _raise_invalid_request("Experience Candidate revision", error)
    asyncio.run(_execute(context, lambda client: client.revise_artifact_candidate(request)))


@candidate_revise_app.command("skill")
def revise_skill_candidate(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Candidate.")],
    candidate_id: Annotated[str, typer.Argument(help="Candidate identity.")],
    expected_version: Annotated[int, typer.Option(min=1, help="Exact reviewed Candidate version.")],
    name: Annotated[str, typer.Option(help="Managed Skill name.")],
    description: Annotated[str, typer.Option(help="Managed Skill discovery description.")],
    instructions: Annotated[
        str | None,
        typer.Option(help="Managed Skill instructions; mutually exclusive with --instructions-file."),
    ] = None,
    instructions_file: Annotated[
        Path | None,
        typer.Option(
            exists=True,
            dir_okay=False,
            readable=True,
            help="UTF-8 file containing managed Skill instructions; mutually exclusive with --instructions.",
        ),
    ] = None,
    validation: Annotated[
        list[str] | None,
        typer.Option("--validation", help="Validation check; repeat for additional checks."),
    ] = None,
    source_ref: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Exact Source as TYPE/ID; repeat for more evidence."),
    ] = None,
    artifact_ref: Annotated[
        list[str] | None,
        typer.Option("--artifact-ref", help="Exact Artifact as FAMILY/ID@REVISION; repeat for more evidence."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(help="Exact replacement target as FAMILY/ID@REVISION; automatically included as evidence."),
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Why this replacement proposal is requested.")] = None,
) -> None:
    """Append a complete managed Skill replacement proposal."""

    try:
        sources, artifacts, target_ref = _evidence_references(source_ref, artifact_ref, target)
        _require_target_family(target_ref, family="skill")
        request = ReviseArtifactCandidateRequest(
            scope_id=scope_id,
            candidate_id=candidate_id,
            expected_version=expected_version,
            proposal=SkillProposal(
                name=name,
                description=description,
                instructions=_instructions(instructions, instructions_file),
                validation=[SkillValidationItem(item) for item in validation or ()],
            ),
            source_refs=sources,
            artifact_refs=artifacts,
            target=target_ref,
            reason=reason,
        )
    except ValidationError as error:
        _raise_invalid_request("managed Skill Candidate revision", error)
    asyncio.run(_execute(context, lambda client: client.revise_artifact_candidate(request)))


@skill_app.command("show")
def show_skill(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the managed Skill.")],
    artifact_id: Annotated[str, typer.Argument(help="Managed Skill Artifact identity.")],
    revision: Annotated[int, typer.Option(min=1, help="Exact managed Skill Revision.")],
) -> None:
    """Read one exact approved managed Skill Revision."""

    request = GetSkillRequest(
        scope_id=scope_id,
        artifact=ArtifactReference(family="skill", artifact_id=artifact_id, revision=revision),
    )
    asyncio.run(_execute(context, lambda client: client.get_skill(request)))


@experience_app.command("generate")
def generate_experience(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope receiving the generated Candidate.")],
    source_ref: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Exact Source as TYPE/ID; repeat for more evidence."),
    ] = None,
    artifact_ref: Annotated[
        list[str] | None,
        typer.Option("--artifact-ref", help="Exact Artifact as FAMILY/ID@REVISION; repeat for more evidence."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(help="Exact replacement target as FAMILY/ID@REVISION; automatically included as evidence."),
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Why this generation is requested.")] = None,
) -> None:
    """Generate at most one pending Experience Candidate."""

    try:
        sources, artifacts, target_ref = _evidence_references(source_ref, artifact_ref, target)
        _require_target_family(target_ref, family="experience")
        request = GenerateExperienceRequest(
            scope_id=scope_id,
            source_refs=sources,
            artifact_refs=artifacts,
            target=target_ref,
            reason=reason,
        )
    except ValidationError as error:
        _raise_invalid_request("Experience generation", error)
    asyncio.run(_execute(context, lambda client: client.generate_experience(request)))


@skill_app.command("generate")
def generate_skill(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope receiving the generated Candidate.")],
    origin: Annotated[SkillGenerationOrigin, typer.Option(help="Provenance shape for the generated managed Skill.")],
    source_ref: Annotated[
        list[str] | None,
        typer.Option("--source-ref", help="Exact Source as TYPE/ID; repeat for more evidence."),
    ] = None,
    artifact_ref: Annotated[
        list[str] | None,
        typer.Option("--artifact-ref", help="Exact Artifact as FAMILY/ID@REVISION; repeat for more evidence."),
    ] = None,
    target: Annotated[
        str | None,
        typer.Option(help="Exact evolution target as FAMILY/ID@REVISION; automatically included as evidence."),
    ] = None,
    reason: Annotated[str | None, typer.Option(help="Why this generation is requested.")] = None,
) -> None:
    """Generate at most one pending managed Skill Candidate."""

    try:
        sources, artifacts, target_ref = _evidence_references(source_ref, artifact_ref, target)
        _validate_skill_generation_origin(origin, sources, artifacts, target_ref)
        request = GenerateSkillRequest(
            scope_id=scope_id,
            origin=origin,
            source_refs=sources,
            artifact_refs=artifacts,
            target=target_ref,
            reason=reason,
        )
    except ValidationError as error:
        _raise_invalid_request("managed Skill generation", error)
    asyncio.run(_execute(context, lambda client: client.generate_skill(request)))


@external_skill_app.command("scan")
def scan_external_skills(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope receiving the local Registry projection.")],
) -> None:
    """Refresh explicitly configured local external Skill roots."""

    request = ScanExternalSkillsRequest(scope_id=scope_id)
    asyncio.run(_execute(context, lambda client: client.scan_external_skills(request)))


@external_skill_app.command("list")
def list_external_skills(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the local Registry projection.")],
    include_unavailable: Annotated[
        bool,
        typer.Option(help="Include stale or missing local bindings for audit."),
    ] = False,
) -> None:
    """List external Skills after live host and fingerprint checks."""

    request = ListExternalSkillsRequest(scope_id=scope_id, include_unavailable=include_unavailable)
    asyncio.run(_execute(context, lambda client: client.list_external_skills(request)))


@external_skill_app.command("resolve")
def resolve_external_skill(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the local Registry projection.")],
    external_skill_id: Annotated[str, typer.Argument(help="Stable external Skill identity in the scope.")],
    fingerprint: Annotated[str, typer.Option(help="Exact observed package SHA-256 fingerprint.")],
) -> None:
    """Resolve one exact local package version without fallback or installation."""

    request = ResolveExternalSkillRequest(
        scope_id=scope_id,
        external_skill_id=external_skill_id,
        fingerprint=fingerprint,
    )
    asyncio.run(_execute(context, lambda client: client.resolve_external_skill(request)))


@external_skill_app.command("import")
def import_external_skill(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope receiving the managed Candidate.")],
    external_skill_id: Annotated[str, typer.Argument(help="Stable external Skill identity in the scope.")],
    fingerprint: Annotated[str, typer.Option(help="Exact observed package SHA-256 fingerprint.")],
    mode: Annotated[
        ExternalSkillImportMode,
        typer.Option(help="Whether to import or intentionally fork the selected package."),
    ] = ExternalSkillImportMode.IMPORT,
    reason: Annotated[str | None, typer.Option(help="Why this managed proposal is requested.")] = None,
) -> None:
    """Capture an exact local snapshot and propose a new managed Skill."""

    request = ImportExternalSkillRequest(
        scope_id=scope_id,
        external_skill_id=external_skill_id,
        fingerprint=fingerprint,
        mode=mode,
        reason=reason,
    )
    asyncio.run(_execute(context, lambda client: client.import_external_skill(request)))


@skill_app.command("export")
def export_managed_skill(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the managed Skill.")],
    artifact_id: Annotated[str, typer.Argument(help="Managed Skill Artifact identity.")],
    target: Annotated[SkillExportTarget, typer.Option(help="Agent integration target.")],
    destination: Annotated[
        Path,
        typer.Option(help="New target Skill directory to create; existing paths are never replaced."),
    ],
    revision: Annotated[int, typer.Option(min=1, help="Exact managed Skill Revision.")],
) -> None:
    """Export one exact approved Revision for an Agent integration target."""

    request = _get_skill_request(scope_id, artifact_id, revision)
    asyncio.run(_export_managed_skill(context, request, target, destination))


@skill_app.command("remote-target-create")
def create_remote_skill_target(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope authorized for the remote target.")],
    agent_kind: Annotated[RemoteAgentKind, typer.Option(help="Remote Agent integration kind.")],
    name: Annotated[str, typer.Option(help="Human-readable remote machine name shown in the Dashboard.")],
) -> None:
    """Create a pending target and print its short-lived enrollment code once."""

    asyncio.run(_create_remote_skill_target(context, scope_id, agent_kind, name))


@skill_app.command("remote-status")
def list_remote_skill_targets(
    context: typer.Context,
    scope_id: Annotated[str, typer.Option(help="Application scope containing the remote targets.")],
    target_id: Annotated[
        str | None,
        typer.Option(help="Optional exact target identity; omit to list the scope."),
    ] = None,
    limit: Annotated[int, typer.Option(min=1, max=200, help="Maximum targets to return.")] = 100,
) -> None:
    """Show remote target enrollment, liveness, desired state, and delivery state."""

    request = ListRemoteSkillTargetsRequest(scope_id=scope_id, target_id=target_id, limit=limit)
    asyncio.run(_execute(context, lambda client: client.list_remote_skill_targets(request)))


@skill_app.command("remote-publish")
def publish_remote_skill(
    context: typer.Context,
    artifact_id: Annotated[str, typer.Argument(help="Approved managed Skill Artifact identity.")],
    scope_id: Annotated[str, typer.Option(help="Application scope containing the Skill and target.")],
    target_id: Annotated[str, typer.Option(help="Enrolled remote target identity.")],
    revision: Annotated[int, typer.Option(min=1, help="Exact approved managed Skill Revision.")],
    expected_generation: Annotated[
        int | None,
        typer.Option(min=0, help="Optional publication CAS generation; resolved automatically when omitted."),
    ] = None,
    allow_deprecated: Annotated[
        bool,
        typer.Option(help="Explicitly allow publishing a deprecated managed Skill."),
    ] = False,
) -> None:
    """Publish or update one exact Skill Revision for a remote target."""

    asyncio.run(
        _publish_remote_skill(
            context,
            scope_id,
            target_id,
            artifact_id,
            revision,
            expected_generation,
            allow_deprecated=allow_deprecated,
        )
    )


@skill_app.command("remote-unpublish")
def unpublish_remote_skill(
    context: typer.Context,
    artifact_id: Annotated[str, typer.Argument(help="Managed Skill Artifact identity to remove remotely.")],
    scope_id: Annotated[str, typer.Option(help="Application scope containing the publication.")],
    target_id: Annotated[str, typer.Option(help="Enrolled remote target identity.")],
    expected_generation: Annotated[
        int | None,
        typer.Option(min=0, help="Optional publication CAS generation; resolved automatically when omitted."),
    ] = None,
) -> None:
    """Request safe removal of one Receiver-managed remote Skill."""

    asyncio.run(_unpublish_remote_skill(context, scope_id, target_id, artifact_id, expected_generation))


@skill_app.command("remote-target-revoke")
def revoke_remote_skill_target(
    context: typer.Context,
    target_id: Annotated[str, typer.Argument(help="Remote target identity to revoke.")],
    scope_id: Annotated[str, typer.Option(help="Application scope containing the remote target.")],
    expected_generation: Annotated[
        int | None,
        typer.Option(min=0, help="Optional target CAS generation; resolved automatically when omitted."),
    ] = None,
) -> None:
    """Revoke a remote Receiver credential while retaining status history."""

    asyncio.run(_revoke_remote_skill_target(context, scope_id, target_id, expected_generation))


@skill_app.command("remote-target-rename")
def rename_remote_skill_target(
    context: typer.Context,
    target_id: Annotated[str, typer.Argument(help="Remote target identity to rename.")],
    name: Annotated[str, typer.Option(help="New human-readable remote machine name.")],
    scope_id: Annotated[str, typer.Option(help="Application scope containing the remote target.")],
    expected_generation: Annotated[
        int | None,
        typer.Option(min=0, help="Optional target CAS generation; resolved automatically when omitted."),
    ] = None,
) -> None:
    """Rename a remote machine without changing its durable target identity."""

    asyncio.run(_rename_remote_skill_target(context, scope_id, target_id, name, expected_generation))


@skill_app.command("remote-enroll")
def enroll_remote_skill_target(
    context: typer.Context,
    workspace: Annotated[Path, typer.Option(help="Local project workspace owned by the Agent Receiver.")] = Path("."),
    enrollment_code: Annotated[
        str | None,
        typer.Option(help="One-time enrollment code; omit to enter it without terminal echo."),
    ] = None,
    config_file: Annotated[
        Path | None,
        typer.Option(help="Credential file to create with owner-only permissions."),
    ] = None,
    environment_fingerprint: Annotated[
        str | None,
        typer.Option(help="Optional target environment SHA-256 fingerprint."),
    ] = None,
    install_service: Annotated[
        bool,
        typer.Option("--install-service", help="Install and start a Linux systemd user service after enrollment."),
    ] = False,
    watch_interval: Annotated[
        float,
        typer.Option(min=1, max=3600, help="Seconds between automatic Pull checks when installing the service."),
    ] = 5,
    allow_insecure_http: Annotated[
        bool,
        typer.Option(
            "--allow-insecure-http",
            help="Allow cleartext remote HTTP on a protected private test network.",
        ),
    ] = False,
) -> None:
    """Enroll this project Receiver without installing a full PowerContext Server."""

    code = enrollment_code or typer.prompt("Enrollment code", hide_input=True)
    asyncio.run(
        _enroll_remote_skill_target(
            context,
            workspace,
            code,
            config_file,
            environment_fingerprint,
            install_service=install_service,
            watch_interval=watch_interval,
            allow_insecure_http=allow_insecure_http,
        )
    )


@skill_app.command("remote-sync")
def sync_remote_skills(
    context: typer.Context,
    config_file: Annotated[
        Path,
        typer.Option(help="Owner-only Receiver credential file created by remote-enroll."),
    ] = Path(".powercontext/remote-skill-target.json"),
) -> None:
    """Reconcile and safely install or unpublish latest remote desired state."""

    asyncio.run(_sync_remote_skills(context, config_file))


@skill_app.command("remote-watch")
def watch_remote_skills(
    context: typer.Context,
    config_file: Annotated[
        Path,
        typer.Option(help="Owner-only Receiver credential file created by remote-enroll."),
    ] = Path(".powercontext/remote-skill-target.json"),
    interval: Annotated[
        float,
        typer.Option(min=1, max=3600, help="Seconds between successful Pull reconciliations."),
    ] = 5,
    max_backoff: Annotated[
        float,
        typer.Option(min=1, max=3600, help="Maximum retry delay after incomplete or failed reconciliation."),
    ] = 60,
) -> None:
    """Continuously Pull and apply the latest remote desired state."""

    try:
        asyncio.run(_watch_remote_skills(context, config_file, interval, max_backoff))
    except KeyboardInterrupt:
        typer.echo("Remote Skill watch stopped.")


@skill_app.command("remote-service-install")
def install_remote_skill_service(
    config_file: Annotated[
        Path,
        typer.Option(help="Owner-only Receiver credential file created by remote-enroll."),
    ] = Path(".powercontext/remote-skill-target.json"),
    interval: Annotated[
        float,
        typer.Option(min=1, max=3600, help="Seconds between automatic Pull reconciliations."),
    ] = 5,
) -> None:
    """Install and start this Receiver as a Linux systemd user service."""

    _install_remote_skill_service(config_file, interval)


@skill_app.command("remote-service-uninstall")
def uninstall_remote_skill_service(
    config_file: Annotated[
        Path,
        typer.Option(help="Receiver credential file identifying the target-scoped user service."),
    ] = Path(".powercontext/remote-skill-target.json"),
) -> None:
    """Stop and remove this Receiver's PowerContext-managed systemd user service."""

    _uninstall_remote_skill_service(config_file)


async def _create_remote_skill_target(
    context: typer.Context,
    scope_id: str,
    agent_kind: RemoteAgentKind,
    name: str,
) -> None:
    options = _options(context)
    token = None if options.api_token is None else options.api_token.get_secret_value()
    try:
        async with PowerContextClient(options.server_url, token=token, timeout=options.timeout) as client:
            enrollment = await client.create_remote_skill_target(
                CreateRemoteSkillTargetRequest(scope_id=scope_id, agent_kind=agent_kind, display_name=name)
            )
    except ClientError as error:
        typer.echo(_error_message(error), err=True)
        raise typer.Exit(code=1) from error
    if options.json_output:
        typer.echo(enrollment.model_dump_json(indent=2))
        return
    typer.echo(f"Machine: {enrollment.target.display_name}")
    typer.echo(f"Target ID: {enrollment.target.target_id}")
    typer.echo(f"Expires: {enrollment.enrollment_expires_at.isoformat()}")
    typer.echo(f"Enrollment code: {enrollment.enrollment_code}")
    typer.echo("Next: run remote-enroll on the target project using the public HTTPS Server URL.")


async def _publish_remote_skill(
    context: typer.Context,
    scope_id: str,
    target_id: str,
    artifact_id: str,
    revision: int,
    expected_generation: int | None,
    *,
    allow_deprecated: bool,
) -> None:
    async def publish(client: PowerContextClient) -> RemoteSkillPublication:
        resolved_generation = expected_generation
        if resolved_generation is None:
            status = await _remote_target_status(client, scope_id, target_id)
            current = next(
                (publication for publication in status.publications if publication.artifact_id == artifact_id),
                None,
            )
            resolved_generation = None if current is None else current.generation
        return await client.publish_remote_skill(
            PublishRemoteSkillRequest(
                scope_id=scope_id,
                target_id=target_id,
                artifact=ArtifactReference(family="skill", artifact_id=artifact_id, revision=revision),
                expected_generation=resolved_generation,
                allow_deprecated=allow_deprecated,
            )
        )

    await _execute(context, publish)


async def _unpublish_remote_skill(
    context: typer.Context,
    scope_id: str,
    target_id: str,
    artifact_id: str,
    expected_generation: int | None,
) -> None:
    async def unpublish(client: PowerContextClient) -> RemoteSkillPublication:
        resolved_generation = expected_generation
        if resolved_generation is None:
            status = await _remote_target_status(client, scope_id, target_id)
            current = next(
                (publication for publication in status.publications if publication.artifact_id == artifact_id),
                None,
            )
            if current is None:
                message = f"remote publication {artifact_id!r} was not found for target {target_id!r}"
                raise typer.BadParameter(
                    message,
                    param_hint="artifact_id",
                )
            resolved_generation = current.generation
        return await client.unpublish_remote_skill(
            UnpublishRemoteSkillRequest(
                scope_id=scope_id,
                target_id=target_id,
                artifact_id=artifact_id,
                expected_generation=resolved_generation,
            )
        )

    await _execute(context, unpublish)


async def _revoke_remote_skill_target(
    context: typer.Context,
    scope_id: str,
    target_id: str,
    expected_generation: int | None,
) -> None:
    async def revoke(client: PowerContextClient) -> RemoteSkillTarget:
        resolved_generation = expected_generation
        if resolved_generation is None:
            status = await _remote_target_status(client, scope_id, target_id)
            resolved_generation = status.target.generation
        return await client.revoke_remote_skill_target(
            RevokeRemoteSkillTargetRequest(
                scope_id=scope_id,
                target_id=target_id,
                expected_generation=resolved_generation,
            )
        )

    await _execute(context, revoke)


async def _rename_remote_skill_target(
    context: typer.Context,
    scope_id: str,
    target_id: str,
    name: str,
    expected_generation: int | None,
) -> None:
    async def rename(client: PowerContextClient) -> RemoteSkillTarget:
        resolved_generation = expected_generation
        if resolved_generation is None:
            status = await _remote_target_status(client, scope_id, target_id)
            resolved_generation = status.target.generation
        return await client.rename_remote_skill_target(
            RenameRemoteSkillTargetRequest(
                scope_id=scope_id,
                target_id=target_id,
                display_name=name,
                expected_generation=resolved_generation,
            )
        )

    await _execute(context, rename)


async def _remote_target_status(
    client: PowerContextClient,
    scope_id: str,
    target_id: str,
) -> RemoteSkillTargetStatus:
    response = await client.list_remote_skill_targets(
        ListRemoteSkillTargetsRequest(scope_id=scope_id, target_id=target_id, limit=1)
    )
    if not response.targets:
        message = f"remote target {target_id!r} was not found"
        raise typer.BadParameter(message, param_hint="--target-id")
    return response.targets[0]


async def _enroll_remote_skill_target(
    context: typer.Context,
    workspace: Path,
    enrollment_code: str,
    config_file: Path | None,
    environment_fingerprint: str | None,
    *,
    install_service: bool,
    watch_interval: float,
    allow_insecure_http: bool,
) -> None:
    options = _options(context)
    try:
        insecure_http = require_remote_skill_server_url(
            options.server_url,
            allow_insecure_http=allow_insecure_http,
        )
    except ValueError as error:
        typer.echo(f"Cannot enroll remote Skill Receiver: {error}", err=True)
        raise typer.Exit(code=2) from error
    resolved_workspace, destination = _remote_receiver_paths(workspace, config_file)
    request = EnrollRemoteSkillTargetRequest(
        enrollment_code=enrollment_code,
        installation_id=f"project-{uuid4().hex}",
        receiver_version=RECEIVER_VERSION,
        environment_fingerprint=environment_fingerprint,
        machine_hostname=socket.gethostname(),
        workspace_name=resolved_workspace.name,
    )
    credential_saved = False
    installation: ReceiverServiceInstallation | None = None
    reservation: int | None = None
    try:
        reservation = _reserve_receiver_config(destination)
        observed_environment_fingerprints = {
            agent_kind: receiver_environment_fingerprint(resolved_workspace, agent_kind)
            for agent_kind in ("codex", "claude_code")
        }
        async with PowerContextClient(
            options.server_url,
            timeout=options.timeout,
            allow_insecure_http=insecure_http,
        ) as client:
            enrolled = await client.enroll_remote_skill_target(request)
        observed_environment_fingerprint = observed_environment_fingerprints[enrolled.agent_kind.value]
        descriptor = reservation
        reservation = None
        _write_receiver_config(
            destination,
            {
                "schema": "powercontext.remote-skill-receiver-config.v1",
                "server_url": options.server_url,
                "target_id": enrolled.target_id,
                "credential": enrolled.credential,
                "agent_kind": enrolled.agent_kind.value,
                "workspace": str(resolved_workspace),
                "state_root": None,
                "receiver_version": RECEIVER_VERSION,
                "environment_fingerprint": observed_environment_fingerprint,
                "allow_insecure_http": insecure_http,
            },
            descriptor=descriptor,
        )
        credential_saved = True
        if install_service:
            installation = install_systemd_user_service(
                destination,
                _read_receiver_config(destination),
                interval_seconds=watch_interval,
            )
    except ClientError as error:
        typer.echo(_error_message(error), err=True)
        raise typer.Exit(code=1) from error
    except (OSError, ReceiverServiceError, ValueError) as error:
        if credential_saved:
            typer.echo(
                f"Receiver credential was saved at {destination}, but automatic sync could not start: {error}", err=True
            )
        else:
            typer.echo(f"Cannot save Receiver credential: {error}", err=True)
        raise typer.Exit(code=2) from error
    finally:
        if reservation is not None:
            os.close(reservation)
            destination.unlink(missing_ok=True)
    if options.json_output:
        typer.echo(
            json.dumps(
                {
                    "target_id": enrolled.target_id,
                    "agent_kind": enrolled.agent_kind.value,
                    "workspace": str(resolved_workspace),
                    "config_file": str(destination),
                    "allow_insecure_http": insecure_http,
                    "service": None
                    if installation is None
                    else {"unit_name": installation.unit_name, "unit_path": str(installation.unit_path)},
                },
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"Enrolled target {enrolled.target_id} for {enrolled.agent_kind.value}.")
    typer.echo(f"Credential saved with owner-only permissions at {destination}.")
    if insecure_http:
        typer.echo(
            "WARNING: Receiver credentials and Skill packages will use cleartext HTTP. "
            "Use this only on a protected private test network.",
            err=True,
        )
    if installation is None:
        typer.echo(f"Next: cd {resolved_workspace} && powercontext skill remote-service-install")
    else:
        typer.echo(f"Automatic remote Skill sync is active in {installation.unit_name}.")


def _remote_receiver_paths(workspace: Path, config_file: Path | None) -> tuple[Path, Path]:
    resolved_workspace = workspace.expanduser().resolve(strict=False)
    destination = (
        resolved_workspace / ".powercontext" / "remote-skill-target.json"
        if config_file is None
        else config_file.expanduser().resolve(strict=False)
    )
    return resolved_workspace, destination


async def _sync_remote_skills(context: typer.Context, config_file: Path) -> None:
    try:
        config = _read_receiver_config(config_file)
        async with RemoteSkillReceiver(config) as receiver:
            result = await receiver.sync()
    except ClientError as error:
        typer.echo(_error_message(error), err=True)
        raise typer.Exit(code=1) from error
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"Cannot sync remote Skills: {error}", err=True)
        raise typer.Exit(code=2) from error
    json_output = _options(context).json_output
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "requested": result.requested,
                    "succeeded": result.succeeded,
                    "failed": result.failed,
                    "receipt_pending": result.receipt_pending,
                },
                indent=2,
                sort_keys=True,
            )
        )
    elif result.requested == 0 and result.receipt_pending == 0:
        typer.echo("Remote Skills are already current; no actions were needed.")
    else:
        typer.echo(
            f"Remote Skill sync: {result.succeeded} succeeded, {result.failed} failed, "
            f"{result.receipt_pending} Receipts pending ({result.requested} actions)."
        )
        if result.succeeded:
            typer.echo("Changes are discoverable on the next Agent session or discovery cycle.")
        if result.failed:
            typer.echo("One or more remote Skill actions failed; inspect remote-status before retrying.", err=True)
        if result.receipt_pending:
            typer.echo("Run remote-sync again to finish pending delivery Receipts.", err=True)
    if result.failed or result.receipt_pending:
        raise typer.Exit(code=1)


async def _watch_remote_skills(
    context: typer.Context,
    config_file: Path,
    interval: float,
    max_backoff: float,
) -> None:
    if max_backoff < interval:
        typer.echo("Cannot watch remote Skills: --max-backoff must not be shorter than --interval", err=True)
        raise typer.Exit(code=2)
    try:
        config = _read_receiver_config(config_file)
        typer.echo(f"Watching remote Skills for {config.target_id} every {interval:g} seconds. Press Ctrl+C to stop.")
        async with RemoteSkillReceiver(config) as receiver:
            await receiver.watch(
                interval_seconds=interval,
                max_backoff_seconds=max_backoff,
                on_result=_print_receiver_watch_result,
                on_error=_print_receiver_watch_error,
            )
    except ServerResponseError as error:
        if error.status_code in {401, 403}:
            typer.echo("Receiver credential was rejected; automatic sync is stopping.", err=True)
            raise typer.Exit(code=3) from error
        typer.echo(_error_message(error), err=True)
        raise typer.Exit(code=1) from error
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"Cannot watch remote Skills: {error}", err=True)
        raise typer.Exit(code=2) from error


def _print_receiver_watch_result(result: ReceiverSyncResult) -> None:
    if result.requested == 0 and result.receipt_pending == 0:
        return
    typer.echo(
        f"Remote Skill sync: {result.succeeded} succeeded, {result.failed} failed, "
        f"{result.receipt_pending} Receipts pending ({result.requested} actions)."
    )


def _print_receiver_watch_error(error: Exception, retry_delay: float) -> None:
    typer.echo(f"Remote Skill sync failed; retrying in {retry_delay:g} seconds: {error}", err=True)


def _install_remote_skill_service(config_file: Path, interval: float) -> ReceiverServiceInstallation:
    try:
        config = _read_receiver_config(config_file)
        installation = install_systemd_user_service(config_file, config, interval_seconds=interval)
    except (OSError, ReceiverServiceError, ValueError) as error:
        typer.echo(f"Cannot install automatic remote Skill sync: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Automatic remote Skill sync is active in {installation.unit_name}.")
    typer.echo(f"Unit: {installation.unit_path}")
    return installation


def _uninstall_remote_skill_service(config_file: Path) -> ReceiverServiceInstallation:
    try:
        config = _read_receiver_config(config_file)
        installation = uninstall_systemd_user_service(config.target_id)
    except (OSError, ReceiverServiceError, ValueError) as error:
        typer.echo(f"Cannot uninstall automatic remote Skill sync: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(f"Automatic remote Skill sync is stopped for {config.target_id}.")
    return installation


def _reserve_receiver_config(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return os.open(path, flags, 0o600)


def _write_receiver_config(path: Path, value: dict[str, object], *, descriptor: int) -> None:
    temporary: Path | None = None
    try:
        os.close(descriptor)
        temporary_descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        temporary = Path(temporary_name)
        with os.fdopen(temporary_descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.chmod(0o600)
        os.replace(temporary, path)
    except BaseException:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        path.unlink(missing_ok=True)
        raise


def _read_receiver_config(path: Path) -> RemoteSkillReceiverConfig:
    resolved = path.expanduser().resolve(strict=True)
    if os.name != "nt" and resolved.stat().st_mode & 0o077:
        raise ValueError("Receiver credential file must not be accessible by group or other users")  # noqa: TRY003
    value = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or value.pop("schema", None) != "powercontext.remote-skill-receiver-config.v1":
        raise ValueError("Receiver credential file schema is invalid")  # noqa: TRY003
    return RemoteSkillReceiverConfig.model_validate(value)


def _options(context: typer.Context) -> _ClientOptions:
    overrides = context.meta.get("powercontext.client.overrides", _ClientOverrides())
    settings = ClientSettings()
    return _ClientOptions(
        server_url=settings.server_url if overrides.server_url is None else overrides.server_url,
        api_token=settings.api_token,
        timeout=settings.timeout if overrides.timeout is None else overrides.timeout,
        json_output=overrides.json_output,
    )


async def _execute(context: typer.Context, operation: _ClientOperation) -> None:
    options = _options(context)
    try:
        token = None if options.api_token is None else options.api_token.get_secret_value()
        async with PowerContextClient(options.server_url, token=token, timeout=options.timeout) as client:
            response = await operation(client)
    except ClientError as exc:
        typer.echo(_error_message(exc), err=True)
        raise typer.Exit(code=1) from exc

    if options.json_output:
        typer.echo(response.model_dump_json(indent=2))
        return
    _print_human_response(response)


def _get_skill_request(scope_id: str, artifact_id: str, revision: int) -> GetSkillRequest:
    return GetSkillRequest(
        scope_id=scope_id,
        artifact=ArtifactReference(family="skill", artifact_id=artifact_id, revision=revision),
    )


def _evidence_references(
    source_values: list[str] | None,
    artifact_values: list[str] | None,
    target_value: str | None,
) -> tuple[list[SourceReference], list[ArtifactReference], ArtifactReference | None]:
    sources = _source_references(source_values or [])
    artifacts = _artifact_references(artifact_values or [], parameter="--artifact-ref")
    target = None if target_value is None else _artifact_reference(target_value, parameter="--target")
    if target is not None and target not in artifacts:
        artifacts.append(target)
    return sources, artifacts, target


def _source_references(values: list[str]) -> list[SourceReference]:
    references: list[SourceReference] = []
    seen: set[tuple[str, str]] = set()
    for value in values:
        name, separator, source_id = value.partition("/")
        if not separator or not name or not source_id:
            _raise_bad_parameter("expected TYPE/ID", parameter="--source-ref")
        try:
            reference = SourceReference(name=name, source_id=source_id)
        except ValidationError as error:
            _raise_bad_parameter(_validation_message(error), parameter="--source-ref", cause=error)
        identity = (reference.name, reference.source_id)
        if identity in seen:
            _raise_bad_parameter(f"duplicate Source reference: {value}", parameter="--source-ref")
        references.append(reference)
        seen.add(identity)
    return references


def _artifact_references(values: list[str], *, parameter: str) -> list[ArtifactReference]:
    references: list[ArtifactReference] = []
    seen: set[tuple[str, str, int]] = set()
    for value in values:
        reference = _artifact_reference(value, parameter=parameter)
        identity = (reference.family, reference.artifact_id, reference.revision)
        if identity in seen:
            _raise_bad_parameter(f"duplicate Artifact reference: {value}", parameter=parameter)
        references.append(reference)
        seen.add(identity)
    return references


def _artifact_reference(value: str, *, parameter: str) -> ArtifactReference:
    family, family_separator, versioned_id = value.partition("/")
    artifact_id, revision_separator, revision_text = versioned_id.rpartition("@")
    if not family_separator or not family or not revision_separator or not artifact_id or not revision_text:
        _raise_bad_parameter("expected FAMILY/ID@REVISION", parameter=parameter)
    try:
        revision = int(revision_text)
        return ArtifactReference(family=family, artifact_id=artifact_id, revision=revision)
    except (ValueError, ValidationError) as error:
        _raise_bad_parameter(_validation_message(error), parameter=parameter, cause=error)


def _require_target_family(target: ArtifactReference | None, *, family: str) -> None:
    if target is not None and target.family != family:
        _raise_bad_parameter(f"target must identify a {family} Artifact", parameter="--target")


def _validate_skill_generation_origin(
    origin: SkillGenerationOrigin,
    sources: list[SourceReference],
    artifacts: list[ArtifactReference],
    target: ArtifactReference | None,
) -> None:
    if origin is SkillGenerationOrigin.EXPERIENCE:
        if target is not None or not artifacts or any(reference.family != "experience" for reference in artifacts):
            _raise_bad_parameter("experience origin requires Experience refs and no target", parameter="--origin")
        return
    if origin is SkillGenerationOrigin.SOURCE:
        if target is not None or not sources or artifacts:
            _raise_bad_parameter("source origin requires only Source refs", parameter="--origin")
        return
    if target is None or target.family != "skill" or not sources:
        _raise_bad_parameter("usage origin requires a target Skill and Source refs", parameter="--origin")


def _instructions(value: str | None, path: Path | None) -> str:
    if value is not None:
        if path is not None:
            _raise_bad_parameter(
                "provide exactly one of --instructions or --instructions-file",
                parameter="--instructions",
            )
        return value
    if path is None:
        _raise_bad_parameter(
            "provide exactly one of --instructions or --instructions-file",
            parameter="--instructions",
        )
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        _raise_bad_parameter(
            f"cannot read UTF-8 instructions: {error}",
            parameter="--instructions-file",
            cause=error,
        )


def _raise_invalid_request(name: str, error: ValidationError) -> Never:
    _raise_bad_parameter(f"invalid {name}: {_validation_message(error)}", cause=error)


def _raise_bad_parameter(
    message: str,
    *,
    parameter: str | None = None,
    cause: BaseException | None = None,
) -> Never:
    raise typer.BadParameter(message, param_hint=parameter) from cause


def _validation_message(error: ValueError | ValidationError) -> str:
    if isinstance(error, ValidationError):
        return "; ".join(item["msg"] for item in error.errors())
    return str(error)


async def _export_managed_skill(
    context: typer.Context,
    request: GetSkillRequest,
    target: SkillExportTarget,
    destination: Path,
) -> None:
    options = _options(context)
    try:
        token = None if options.api_token is None else options.api_token.get_secret_value()
        async with PowerContextClient(options.server_url, token=token, timeout=options.timeout) as client:
            response = await client.get_skill(request)
            if response.content.package is None:
                exported = export_skill(
                    ArtifactRef(
                        family=response.artifact.family,
                        artifact_id=response.artifact.artifact_id,
                        revision=response.artifact.revision,
                    ),
                    response.content,
                    target,
                    destination,
                )
            else:
                download = await client.download_skill_package(
                    GetSkillPackageRequest(scope_id=request.scope_id, artifact=request.artifact)
                )
                try:
                    archive_bytes = base64.b64decode(download.archive_base64, validate=True)
                except (binascii.Error, ValueError) as error:
                    raise ValueError("Server returned an invalid Skill package archive") from error  # noqa: TRY003
                package = capture_skill_archive(archive_bytes)
                if package.reference.model_dump(mode="json") != response.content.package.model_dump(mode="json"):
                    raise ValueError(  # noqa: TRY003, TRY301
                        "downloaded Skill package does not match the Artifact"
                    )
                runtime_content = SkillContent(
                    name=response.content.name,
                    description=response.content.description,
                    instructions=response.content.instructions,
                    validation=tuple(item.root for item in response.content.validation),
                    package=package.reference,
                    license=response.content.license,
                    compatibility=response.content.compatibility,
                    metadata=response.content.metadata or {},
                    allowed_tools=response.content.allowed_tools,
                )
                validate_skill_projection_target(
                    runtime_content,
                    AgentSkillTarget(
                        target_id="client",
                        agent_kind=target.value,
                        installation_scope="project",
                        path=destination.parent,
                        allow_managed_publish=True,
                    ),
                )
                materialize_skill_package(package, destination)
                exported = destination
    except ClientError as error:
        typer.echo(_error_message(error), err=True)
        raise typer.Exit(code=1) from error
    except (FileExistsError, OSError, ValueError) as error:
        typer.echo(f"Cannot export managed Skill for {target.value}: {error}", err=True)
        raise typer.Exit(code=2) from error
    typer.echo(
        f"Exported {response.artifact.artifact_id}@{response.artifact.revision} for {target.value} to {exported}"
    )


def _error_message(error: ClientError) -> str:
    if error.request_id is None:
        return str(error)
    return f"{error} (request ID: {error.request_id})"


def _print_human_response(response: _ClientResponse) -> None:
    if isinstance(response, (ListRemoteSkillTargetsResponse, RemoteSkillPublication, RemoteSkillTarget)):
        _print_remote_response(response)
        return
    match response:
        case Capabilities():
            typer.echo(f"Source types: {_items(response.source_types)}")
            typer.echo(f"Artifact families: {_items(response.artifact_families)}")
            typer.echo(f"Memory extraction: {'enabled' if response.memory_extraction else 'disabled'}")
            typer.echo(f"Experience generation: {'enabled' if response.experience_generation else 'disabled'}")
            typer.echo(f"Managed Skill generation: {'enabled' if response.managed_skill_generation else 'disabled'}")
            typer.echo(f"External Skill Registry: {'enabled' if response.external_skill_registry else 'disabled'}")
            typer.echo(f"Handoff generation: {'enabled' if response.handoff_generation else 'disabled'}")
            typer.echo(f"Search modes: {_items(response.search_modes)}")
            typer.echo(f"Context versions: {_items(response.context_versions)}")
        case ReadinessResponse():
            typer.echo(f"Status: {response.status}")
            for name, status in sorted(response.checks.items()):
                typer.echo(f"{name}: {status}")
        case HealthResponse():
            typer.echo(f"Status: {response.status}")
        case ScopedStats():
            _print_stats(response)
        case (
            ArtifactCandidate()
            | ArtifactCandidatePage()
            | ExternalSkillResolution()
            | GeneratedCandidateResponse()
            | ListExternalSkillsResponse()
            | ScanExternalSkillsResponse()
        ):
            typer.echo(response.model_dump_json(indent=2))
        case SkillArtifact():
            typer.echo(response.model_dump_json(indent=2))


def _print_remote_response(
    response: ListRemoteSkillTargetsResponse | RemoteSkillPublication | RemoteSkillTarget,
) -> None:
    match response:
        case ListRemoteSkillTargetsResponse():
            _print_remote_skill_targets(response)
        case RemoteSkillPublication():
            typer.echo(
                f"Remote publication {response.artifact_id}@{response.desired_revision} -> {response.target_id}: "
                f"desired={response.desired_state.value}, state={response.state.value}, generation={response.generation}"
            )
            typer.echo("The target applies this desired state on its next remote-sync.")
        case RemoteSkillTarget():
            typer.echo(
                f"Remote target {response.display_name} ({response.target_id}): state={response.state.value}, "
                f"agent={response.agent_kind.value}, generation={response.generation}"
            )


def _print_remote_skill_targets(response: ListRemoteSkillTargetsResponse) -> None:
    if not response.targets:
        typer.echo("No remote Skill targets found.")
        return
    for index, status in enumerate(response.targets):
        if index:
            typer.echo("")
        target = status.target
        last_seen = "never" if target.last_seen_at is None else target.last_seen_at.isoformat()
        installation = "not enrolled" if target.installation_id is None else target.installation_id
        typer.echo(
            f"Target {target.display_name} ({target.target_id}): state={target.state.value}, "
            f"agent={target.agent_kind.value}, "
            f"generation={target.generation}"
        )
        typer.echo(f"  Installation: {installation}; last seen: {last_seen}")
        environment = " / ".join(value for value in (target.machine_hostname, target.workspace_name) if value)
        typer.echo(f"  Environment: {environment or 'not reported'}")
        if not status.publications:
            typer.echo("  Publications: none")
            continue
        typer.echo("  Publications:")
        for publication in status.publications:
            if publication.observed_revision is not None:
                observed = f"revision {publication.observed_revision}"
            elif publication.state.value == "unpublished":
                observed = "absent"
            else:
                observed = "not reported"
            error = "" if publication.last_error_code is None else f", error={publication.last_error_code}"
            typer.echo(
                f"    {publication.artifact_id}: desired={publication.desired_state.value} "
                f"revision {publication.desired_revision}, observed={observed}, "
                f"state={publication.state.value}, generation={publication.generation}{error}"
            )


def _print_stats(response: ScopedStats) -> None:
    inventory = response.inventory
    typer.echo(f"Selection: {response.selection.root.mode} ({len(response.scope_ids)} Scopes)")
    typer.echo(f"As of: {response.as_of.isoformat()}")
    typer.echo(
        "Sources: "
        f"{inventory.sources.total} total, "
        f"{inventory.sources.memory_processed} memory processed, "
        f"{inventory.sources.memory_pending} memory pending"
    )
    typer.echo(f"Artifacts: {inventory.artifacts.total} ({_family_counts(inventory.artifacts.by_family)})")
    typer.echo(
        "Candidates: "
        f"{inventory.candidates.total} total, "
        f"{inventory.candidates.pending} pending, "
        f"{inventory.candidates.approved} approved, "
        f"{inventory.candidates.rejected} rejected"
    )
    entries = inventory.memory.entries
    typer.echo(f"Memory entries: {entries.total} total, {entries.active} active, {entries.inactive} inactive")
    period = response.usage.period
    typer.echo(f"Model usage: {period.start_date} to {period.end_date} ({period.timezone})")
    _print_model_usage("Generation", response.usage.totals.generation)
    _print_model_usage("Embedding", response.usage.totals.embedding)
    for item in response.usage.by_purpose:
        if item.generation.requests:
            _print_model_usage(f"  {item.purpose} generation", item.generation)
        if item.embedding.requests:
            _print_model_usage(f"  {item.purpose} embedding", item.embedding)
    recall = response.recall
    if recall.estimator is None:
        typer.echo("Recall token estimation: disabled")
    else:
        totals = recall.totals
        typer.echo(f"Recall token estimator: {recall.estimator.estimator_id}@{recall.estimator.version}")
        typer.echo(
            "Recall tokens: "
            f"{totals.preparations} preparations "
            f"({totals.ready_preparations} ready, {totals.comparable_preparations} comparable), "
            f"{totals.baseline_tokens} baseline, "
            f"{totals.recalled_tokens} recalled, "
            f"{totals.token_reduction} reduction"
        )


def _family_counts(values: Sequence[FamilyCount]) -> str:
    counts = [f"{value.family}={value.total}" for value in values]
    return ", ".join(counts) if counts else "none"


def _print_model_usage(name: str, value: ModelUsageValue) -> None:
    typer.echo(
        f"{name}: {value.requests} requests, "
        f"{_token_count(value.input_tokens)} input tokens, "
        f"{_token_count(value.output_tokens)} output tokens"
    )


def _token_count(value: int | None) -> str:
    return "unknown" if value is None else str(value)


def _items(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "none"


def register_commands(cli: typer.Typer) -> set[str]:
    """Register Server-backed content commands on the product CLI root."""

    cli.command()(capabilities)
    cli.command()(stats)
    cli.command()(live)
    cli.command()(ready)
    cli.add_typer(candidate_app, name="candidate")
    cli.add_typer(experience_app, name="experience")
    cli.add_typer(skill_app, name="skill")
    cli.add_typer(external_skill_app, name="external-skill")
    return {"capabilities", "stats", "live", "ready", "candidate", "experience", "skill", "external-skill"}


__all__ = ["configure_client", "register_commands"]
