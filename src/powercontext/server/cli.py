"""Typer commands owned by the local Server package."""

from __future__ import annotations

from typing import Annotated

import typer
import uvicorn

from powercontext.server.app import create_app
from powercontext.server.settings import ServerSettings

HELP_OPTION_NAMES = ("-h", "--help")

app = typer.Typer(
    name="server",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Run local PowerContext Server components.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Manage the local Server process."""


@app.command()
def run(
    host: Annotated[str | None, typer.Option(help="Address to bind.")] = None,
    port: Annotated[int | None, typer.Option(min=1, max=65535, help="Port to bind.")] = None,
) -> None:
    """Run the FastAPI Server in the foreground."""

    settings = ServerSettings()
    if host is not None or port is not None:
        settings = ServerSettings(
            host=host if host is not None else settings.host,
            port=port if port is not None else settings.port,
        )
    uvicorn.run(
        create_app(settings=settings),
        host=settings.host,
        port=settings.port,
    )
