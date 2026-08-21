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

"""Typer shell assembled from installed PowerContext roles."""

from __future__ import annotations

from collections.abc import Iterable
from importlib.metadata import entry_points, version
from typing import Annotated

import typer

from powercontext.client.cli import configure_client, register_commands

COMMAND_PROVIDER_GROUP = "powercontext.cli"
HELP_OPTION_NAMES = ("-h", "--help")
DOCUMENTATION_URL = "https://github.com/oceanbase/powercontext/tree/main/docs/en/docs"
ISSUES_URL = "https://github.com/oceanbase/powercontext/issues"


def _show_version(value: bool) -> None:
    if value:
        typer.echo(version("powercontext"))
        raise typer.Exit()


def create_cli(commands: Iterable[typer.Typer] | None = None) -> typer.Typer:
    """Create the Server-backed CLI with optional process command providers."""

    cli = typer.Typer(
        context_settings={"help_option_names": HELP_OPTION_NAMES},
        epilog=f"Documentation: {DOCUMENTATION_URL}\nIssues: {ISSUES_URL}",
        no_args_is_help=True,
        pretty_exceptions_enable=False,
    )

    @cli.callback()
    def main(
        context: typer.Context,
        server_url: Annotated[
            str | None,
            typer.Option(help="PowerContext Server base URL used by content commands."),
        ] = None,
        timeout: Annotated[
            float | None,
            typer.Option(min=0.1, help="HTTP timeout in seconds used by content commands."),
        ] = None,
        json_output: Annotated[
            bool,
            typer.Option("--json", help="Write content command responses as JSON."),
        ] = False,
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
        """Operate one PowerContext Server and its integrations."""

        configure_client(
            context,
            server_url=server_url,
            timeout=timeout,
            json_output=json_output,
        )

    registered_names = register_commands(cli)

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
