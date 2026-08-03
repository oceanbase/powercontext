"""Typer commands owned by the remote Client SDK."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, TypeAlias

import typer
from pydantic import SecretStr, ValidationError

from powercontext.client.client import PowerContextClient
from powercontext.client.errors import ClientError
from powercontext.client.settings import ClientSettings
from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCandidatePage,
    CandidateFamily,
    CandidateStatus,
    Capabilities,
    GetArtifactCandidateRequest,
    HealthResponse,
    ListArtifactCandidatesRequest,
    ReadinessResponse,
    RejectArtifactCandidateRequest,
    ReviseArtifactCandidateRequest,
)

HELP_OPTION_NAMES = ("-h", "--help")
_ClientResponse: TypeAlias = (
    ArtifactCandidate | ArtifactCandidatePage | Capabilities | HealthResponse | ReadinessResponse
)
_ClientOperation: TypeAlias = Callable[[PowerContextClient], Awaitable[_ClientResponse]]

app = typer.Typer(
    name="client",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Inspect a remote PowerContext Server.",
    no_args_is_help=True,
)
candidate_app = typer.Typer(
    name="candidate",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Inspect and review Artifact Candidates.",
    no_args_is_help=True,
)
app.add_typer(candidate_app, name="candidate")


@dataclass(frozen=True, slots=True)
class _ClientOptions:
    server_url: str
    api_token: SecretStr | None
    timeout: float
    json_output: bool


@app.callback()
def main(
    context: typer.Context,
    server_url: Annotated[
        str | None,
        typer.Option(help="PowerContext Server base URL."),
    ] = None,
    timeout: Annotated[
        float | None,
        typer.Option(
            help="HTTP timeout in seconds.",
            min=0.1,
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the response as JSON."),
    ] = False,
) -> None:
    """Configure remote Client commands."""

    settings = ClientSettings()
    context.meta["powercontext.client.options"] = _ClientOptions(
        server_url=settings.server_url if server_url is None else server_url,
        api_token=settings.api_token,
        timeout=settings.timeout if timeout is None else timeout,
        json_output=json_output,
    )


@app.command()
def capabilities(context: typer.Context) -> None:
    """Show behavior enabled by the remote Server runtime."""

    asyncio.run(_execute(context, lambda client: client.get_capabilities()))


@app.command()
def live(context: typer.Context) -> None:
    """Check whether the remote API process is alive."""

    asyncio.run(_execute(context, lambda client: client.get_liveness()))


@app.command()
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


@candidate_app.command("revise")
def revise_candidate(
    context: typer.Context,
    request_file: Annotated[
        Path,
        typer.Argument(
            exists=True, dir_okay=False, readable=True, help="JSON file containing the complete revision request."
        ),
    ],
) -> None:
    """Append a complete replacement proposal from a JSON request file."""

    try:
        request = ReviseArtifactCandidateRequest.model_validate_json(request_file.read_text(encoding="utf-8"))
    except (OSError, ValidationError, json.JSONDecodeError) as error:
        typer.echo(f"Invalid Candidate revision request: {error}", err=True)
        raise typer.Exit(code=2) from error
    asyncio.run(_execute(context, lambda client: client.revise_artifact_candidate(request)))


def _options(context: typer.Context) -> _ClientOptions:
    return context.meta["powercontext.client.options"]


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
            typer.echo(f"Handoff generation: {'enabled' if response.handoff_generation else 'disabled'}")
            typer.echo(f"Search modes: {_items(response.search_modes)}")
            typer.echo(f"Context versions: {_items(response.context_versions)}")
        case ReadinessResponse():
            typer.echo(f"Status: {response.status}")
            for name, status in sorted(response.checks.items()):
                typer.echo(f"{name}: {status}")
        case HealthResponse():
            typer.echo(f"Status: {response.status}")
        case ArtifactCandidate() | ArtifactCandidatePage():
            typer.echo(response.model_dump_json(indent=2))


def _items(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "none"
