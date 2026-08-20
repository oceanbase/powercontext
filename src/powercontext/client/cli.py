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
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Never, TypeAlias

import typer
from pydantic import SecretStr, ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.client.client import PowerContextClient
from powercontext.client.errors import ClientError
from powercontext.client.projections import SkillExportTarget, export_skill
from powercontext.client.settings import ClientSettings
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    ArtifactReference,
    CandidateFamily,
    CandidateStatus,
    Capabilities,
    ExperienceProposal,
    ExternalSkillImportMode,
    ExternalSkillResolution,
    FamilyCount,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetArtifactCandidateRequest,
    GetSkillRequest,
    GetStatsRequest,
    HealthResponse,
    ImportExternalSkillRequest,
    ListArtifactCandidatesRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ModelUsageValue,
    ReadinessResponse,
    RejectArtifactCandidateRequest,
    ResolveExternalSkillRequest,
    ReviseArtifactCandidateRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopedStats,
    SkillArtifact,
    SkillGenerationOrigin,
    SkillProposal,
    SkillValidationItem,
    SourceReference,
    StatsPeriod,
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
    | ReadinessResponse
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
    scope_id: Annotated[str, typer.Option(help="Application scope to inspect.")],
    period: Annotated[StatsPeriod, typer.Option(help="Bounded UTC statistics period.")] = StatsPeriod.FIELD_30D,
) -> None:
    """Show current inventory and bounded usage for one scope."""

    request = GetStatsRequest(scope_id=scope_id, period=period)
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


def _print_stats(response: ScopedStats) -> None:
    inventory = response.inventory
    typer.echo(f"Scope: {response.scope_id}")
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
