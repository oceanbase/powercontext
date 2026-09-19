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

"""Local repository cache commands for a trusted deployment operator."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer

from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.service import CodeService
from powercontext.builtin.sources import validate_scope_id
from powercontext.server.configuration import server_settings_context

app = typer.Typer(name="code", no_args_is_help=True, help="Build or inspect deployment-configured local code caches.")


@app.command("index")
def index(
    scope: Annotated[
        str, typer.Option(help="Existing Scope explicitly mapped to a local repository by the deployment.")
    ],
    env_file: Annotated[Path | None, typer.Option(help="Deployment environment file.")] = None,
) -> None:
    """Synchronously capture and index current Python files without executing them."""

    _run(scope, env_file, build=True)


@app.command("status")
def status(
    scope: Annotated[str, typer.Option(help="Existing Scope configured for local code understanding.")],
    env_file: Annotated[Path | None, typer.Option(help="Deployment environment file.")] = None,
) -> None:
    """Check the local index against current contents; ordinary Agents use query_code."""

    _run(scope, env_file, build=False)


def _run(scope: str, env_file: Path | None, *, build: bool) -> None:
    scope_id = validate_scope_id(scope)
    try:
        with server_settings_context(env_file=env_file) as settings:
            service = CodeService(settings.code)
            result = asyncio.run(service.index(scope_id) if build else service.status(scope_id))
    except CodeError as error:
        typer.echo(error.code, err=True)
        raise typer.Exit(code=1) from None
    typer.echo(result.model_dump_json(by_alias=True))
