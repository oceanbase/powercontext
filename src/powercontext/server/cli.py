"""Typer commands owned by the local Server package."""

from __future__ import annotations

from typing import Annotated

import typer
import uvicorn

from powercontext.server.runtime import create_server_app
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

    http_overrides = {
        name: value
        for name, value in (
            ("host", host),
            ("port", port),
        )
        if value is not None
    }
    # Pydantic Settings merges this partial init mapping with lower-priority environment values.
    settings = ServerSettings(http=http_overrides)  # ty: ignore[invalid-argument-type]
    server_app = create_server_app(settings=settings)
    uvicorn.run(
        server_app,
        host=settings.http.host,
        port=settings.http.port,
    )
