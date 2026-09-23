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

"""Forward setup and host doctor to rules in the selected integration source."""

from __future__ import annotations

import argparse
from functools import wraps
from pathlib import Path
from typing import Annotated

import typer
from typer.core import TyperGroup
from typer.main import get_group
from typing_extensions import override

from powercontext.cli.errors import SetupError
from powercontext.cli.integration_source import DEFAULT_REF, load_rules, resolve_source


class IntegrationGroup(TyperGroup):
    """Keep CLI syntax while loading host commands outside the client release."""

    @override
    def parse_args(self, ctx, args):
        parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
        parser.add_argument("--source")
        parser.add_argument("--ref")
        selection, remaining = parser.parse_known_args(args)
        ctx.meta["integration_selection"] = vars(selection)
        return super().parse_args(ctx, remaining)

    def _commands(self, ctx, *, target=None, fetch=False):
        selection = ctx.meta.get("integration_selection", {})
        try:
            root = resolve_source(**selection, target=target, fetch=fetch, refresh=self.name == "setup")
            if root is None:
                return None, None
            rules = load_rules(root, "system")
        except SetupError as error:
            raise typer.BadParameter(str(error)) from error
        return get_group(getattr(rules, f"{self.name}_app")), root

    @override
    def list_commands(self, ctx):
        commands, _ = self._commands(ctx)
        return sorted(set(super().list_commands(ctx)) | set(commands.list_commands(ctx) if commands else ()))

    @override
    def get_command(self, ctx, cmd_name):
        command = super().get_command(ctx, cmd_name)
        if command is not None:
            return command
        commands, root = self._commands(ctx, target=cmd_name, fetch=True)
        command = commands.get_command(ctx, cmd_name) if commands else None
        if command is not None and self.name == "setup":
            callback = command.callback

            @wraps(callback)
            def install(**kwargs):
                kwargs["source"] = str(root)
                kwargs["ref"] = ctx.meta.get("integration_selection", {}).get("ref") or DEFAULT_REF
                transport = load_rules(root, "transport")
                env_file = ctx.params.get("env_file")
                token = transport.setup_environment_file.set(Path(env_file) if env_file is not None else None)
                try:
                    return callback(**kwargs)
                finally:
                    transport.setup_environment_file.reset(token)

            command.callback = install
        return command


setup_app = typer.Typer(
    name="setup",
    cls=IntegrationGroup,
    context_settings={"help_option_names": ("-h", "--help")},
    help="Install integrations using repository rules. Select them with --source PATH_OR_REPO and --ref REF.",
    no_args_is_help=True,
)


@setup_app.callback()
def setup(
    env_file: Annotated[
        Path | None, typer.Option(help="Setup environment file; defaults to .env in this directory.")
    ] = None,
) -> None:
    """Run a target's shared setup flow in the installed Python environment."""
