"""CLI commands owned by the ready-to-run service entry point."""

from __future__ import annotations

from typing import Annotated, Any

import typer

from powercontext.server.factory import create_server_app
from powercontext.server.settings import HttpConfig, ServerSettings

HELP_OPTION_NAMES = ("-h", "--help")

app = typer.Typer(
    name="server",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Run a configured PowerContext service.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Manage the PowerContext service process."""


@app.command()
def run(
    host: Annotated[str | None, typer.Option(help="Address to bind.")] = None,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Port to bind.")] = None,
) -> None:
    """Run the ASGI service in the foreground."""

    environment = ServerSettings()
    http = HttpConfig(
        host=environment.http.host if host is None else host,
        port=environment.http.port if port is None else port,
    )
    settings = environment.model_copy(update={"http": http})
    _run_server(
        create_server_app(settings=settings),
        host=settings.http.host,
        port=settings.http.port,
    )


def _run_server(application: Any, *, host: str, port: int) -> None:
    import uvicorn

    uvicorn.run(application, host=host, port=port)
