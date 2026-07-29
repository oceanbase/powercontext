"""Typer commands owned by the remote Client SDK."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Annotated, TypeAlias

import typer

from powercontext.client.client import PowerContextClient
from powercontext.client.errors import ClientError
from powercontext.client.settings import ClientSettings
from powercontext.http import Capabilities, HealthResponse, ReadinessResponse

HELP_OPTION_NAMES = ("-h", "--help")
_ClientResponse: TypeAlias = Capabilities | HealthResponse | ReadinessResponse
_ClientOperation: TypeAlias = Callable[[PowerContextClient], Awaitable[_ClientResponse]]

app = typer.Typer(
    name="client",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Inspect a remote PowerContext Server.",
    no_args_is_help=True,
)


@dataclass(frozen=True, slots=True)
class _ClientOptions:
    server_url: str
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


def _options(context: typer.Context) -> _ClientOptions:
    return context.meta["powercontext.client.options"]


async def _execute(context: typer.Context, operation: _ClientOperation) -> None:
    options = _options(context)
    try:
        async with PowerContextClient(options.server_url, timeout=options.timeout) as client:
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
            typer.echo(f"Search modes: {_items(response.search_modes)}")
            typer.echo(f"Context versions: {_items(response.context_versions)}")
        case ReadinessResponse():
            typer.echo(f"Status: {response.status}")
            for name, status in sorted(response.checks.items()):
                typer.echo(f"{name}: {status}")
        case HealthResponse():
            typer.echo(f"Status: {response.status}")


def _items(values: Sequence[str]) -> str:
    return ", ".join(values) if values else "none"
