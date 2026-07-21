"""Typer commands owned by the remote Client SDK."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Annotated, TypeAlias

import typer

from powercontext.api import Capabilities, CapabilityLimit, HealthResponse, ReadinessResponse
from powercontext.client.client import PowerContextClient
from powercontext.client.errors import ClientError

DEFAULT_SERVER_URL = "http://127.0.0.1:8000"
DEFAULT_TIMEOUT = 10.0
HELP_OPTION_NAMES = ("-h", "--help")
_ClientResponse: TypeAlias = Capabilities | HealthResponse | ReadinessResponse

app = typer.Typer(
    name="client",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Inspect a remote PowerContext Server.",
    no_args_is_help=True,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ClientOptions:
    server_url: str
    timeout: float
    json_output: bool


@app.callback()
def main(
    context: typer.Context,
    server_url: Annotated[
        str,
        typer.Option(envvar="POWERCONTEXT_SERVER_URL", help="PowerContext Server base URL."),
    ] = DEFAULT_SERVER_URL,
    timeout: Annotated[
        float,
        typer.Option(
            envvar="POWERCONTEXT_CLIENT_TIMEOUT",
            help="HTTP timeout in seconds.",
            min=0.1,
        ),
    ] = DEFAULT_TIMEOUT,
    json_output: Annotated[
        bool,
        typer.Option("--json", help="Write the response as JSON."),
    ] = False,
) -> None:
    """Configure remote Client commands."""

    context.meta["powercontext.client.options"] = _ClientOptions(
        server_url=server_url,
        timeout=timeout,
        json_output=json_output,
    )


@app.command()
def capabilities(context: typer.Context) -> None:
    """Show behavior enabled by the remote Server runtime."""

    with _client_for(context) as client:
        _print_result(client.get_capabilities, options=_options(context))


@app.command()
def live(context: typer.Context) -> None:
    """Check whether the remote API process is alive."""

    with _client_for(context) as client:
        _print_result(client.get_liveness, options=_options(context))


@app.command()
def ready(context: typer.Context) -> None:
    """Check whether remote Server bindings are ready."""

    with _client_for(context) as client:
        _print_result(client.get_readiness, options=_options(context))


def _options(context: typer.Context) -> _ClientOptions:
    return context.meta["powercontext.client.options"]


@contextmanager
def _client_for(context: typer.Context) -> Iterator[PowerContextClient]:
    options = _options(context)
    with PowerContextClient(options.server_url, timeout=options.timeout) as client:
        yield client


def _print_result(operation: Callable[[], _ClientResponse], *, options: _ClientOptions) -> None:
    try:
        response = operation()
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
            typer.echo(f"Search modes: {_items(response.search_modes)}")
            typer.echo(f"Limits: {_limits(response.limits)}")
        case ReadinessResponse():
            typer.echo(f"Status: {response.status}")
            for name, status in sorted(response.checks.items()):
                typer.echo(f"{name}: {status}")
        case HealthResponse():
            typer.echo(f"Status: {response.status}")


def _items(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def _limits(limits: list[CapabilityLimit]) -> str:
    return ", ".join(f"{limit.name}={limit.value}" for limit in limits) if limits else "none"
