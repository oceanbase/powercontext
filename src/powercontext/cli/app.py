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
from importlib.metadata import PackageNotFoundError, entry_points, requires, version
from typing import Annotated

import typer
from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

from powercontext.client.cli import configure_client, register_commands

COMMAND_PROVIDER_GROUP = "powercontext.cli"
HELP_OPTION_NAMES = ("-h", "--help")
DOCUMENTATION_URL = "https://github.com/oceanbase/powercontext/tree/master/docs/en/docs"
ISSUES_URL = "https://github.com/oceanbase/powercontext/issues"
# Extras are not recorded as installed. Check their declared requirements before
# loading these providers; unrelated packages must not enable an incomplete role.
_OPTIONAL_COMMAND_ROLES = {
    "powercontext.cli.code:app": "builtin",
    "powercontext.server.cli:app": "server",
    "powercontext.service.cli:app": "server",
}


def _role_available(extra: str, requirements: tuple[Requirement, ...]) -> bool:
    pending = {extra}
    checked: set[str] = set()
    while pending:
        selected = pending.pop()
        checked.add(selected)
        for requirement in requirements:
            if requirement.marker is not None and not requirement.marker.evaluate({"extra": selected}):
                continue
            if canonicalize_name(requirement.name) == "powercontext":
                pending.update(requirement.extras - checked)
                continue
            try:
                installed = version(requirement.name)
            except PackageNotFoundError:
                return False
            if not requirement.specifier.contains(installed, prereleases=True):
                return False
    return True


def _show_version(value: bool) -> None:
    if value:
        typer.echo(version("powercontext"))
        raise typer.Exit()


def _load_command_providers() -> list[typer.Typer]:
    try:
        requirements = tuple(Requirement(value) for value in requires("powercontext") or ())
    except PackageNotFoundError:
        requirements = None
    available_roles = {
        role: requirements is not None and _role_available(role, requirements)
        for role in set(_OPTIONAL_COMMAND_ROLES.values())
    }
    commands: list[typer.Typer] = []
    for entry_point in sorted(entry_points(group=COMMAND_PROVIDER_GROUP), key=lambda item: item.name):
        role = _OPTIONAL_COMMAND_ROLES.get(entry_point.value)
        if role is not None and not available_roles[role]:
            continue
        try:
            command = entry_point.load()
        except ModuleNotFoundError as error:
            raise RuntimeError(  # noqa: TRY003
                f"Cannot load CLI command provider {entry_point.name!r}: missing module {error.name!r}. "
                "Repair the provider's installation and its dependencies."
            ) from error
        if not isinstance(command, typer.Typer):
            raise TypeError(  # noqa: TRY003
                f"CLI command provider {entry_point.name!r} did not load a Typer application"
            )
        commands.append(command)
    return commands


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
        allow_insecure_http: Annotated[
            bool | None,
            typer.Option(
                "--allow-insecure-http/--no-allow-insecure-http", help="Explicitly allow unencrypted remote HTTP."
            ),
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
            allow_insecure_http=allow_insecure_http,
            json_output=json_output,
        )

    registered_names = register_commands(cli)
    from powercontext.source_management.cli import register_commands as register_source_commands

    registered_names.update(register_source_commands(cli))

    if commands is None:
        commands = _load_command_providers()

    for command in commands:
        name = command.info.name
        if not isinstance(name, str) or not name:
            raise ValueError("CLI command applications must define a name")  # noqa: TRY003
        if name in registered_names:
            raise ValueError(f"duplicate CLI command provider: {name}")  # noqa: TRY003
        callback = command.registered_callback.callback if command.registered_callback else None
        if not command.registered_commands and not command.registered_groups and callback is not None:
            cli.command(name=name, help=command.info.help, context_settings=command.info.context_settings)(callback)
        else:
            cli.add_typer(command, name=name)
        registered_names.add(name)
    return cli


def main() -> None:
    """Run the PowerContext CLI."""

    create_cli()()
