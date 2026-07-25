"""Typer shell assembled from installed PowerContext roles."""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points, version
from typing import Annotated

import typer

COMMAND_PROVIDER_GROUP = "powercontext.cli"
HELP_OPTION_NAMES = ("-h", "--help")
DOCUMENTATION_URL = "https://oceanbase.github.io/powercontext/"
ISSUES_URL = "https://github.com/oceanbase/powercontext/issues"


def _show_version(value: bool) -> None:
    if value:
        typer.echo(version("powercontext"))
        raise typer.Exit()


def create_cli(commands: Iterable[typer.Typer] | None = None) -> typer.Typer:
    """Create the CLI from command providers installed with each role."""

    cli = typer.Typer(
        context_settings={"help_option_names": HELP_OPTION_NAMES},
        epilog=f"Documentation: {DOCUMENTATION_URL}\nIssues: {ISSUES_URL}",
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )

    @cli.callback()
    def main(
        version_requested: Annotated[
            bool,
            typer.Option(
                "--version",
                callback=_show_version,
                help="Show the installed version and exit.",
                is_eager=True,
            ),
        ] = False,
    ) -> None:
        """Run commands supplied by installed PowerContext roles."""

    if commands is None:
        installed_commands: list[typer.Typer] = []
        for entry_point in sorted(entry_points(group=COMMAND_PROVIDER_GROUP), key=lambda item: item.name):
            try:
                command = entry_point.load()
            except ModuleNotFoundError:
                continue
            if not isinstance(command, typer.Typer):
                raise TypeError(  # noqa: TRY003
                    f"CLI command provider {entry_point.name!r} did not load a Typer application"
                )
            installed_commands.append(command)
        commands = installed_commands

    registered_names: set[str] = set()
    for command in commands:
        name = command.info.name
        if not isinstance(name, str) or not name:
            raise ValueError("CLI command applications must define a name")  # noqa: TRY003
        if name in registered_names:
            raise ValueError(f"duplicate CLI command provider: {name}")  # noqa: TRY003
        cli.add_typer(command, name=name)
        registered_names.add(name)
    return cli


def main() -> None:
    """Run the PowerContext CLI."""

    create_cli()()
