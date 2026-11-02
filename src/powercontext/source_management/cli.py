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

"""Deployment commands and native service entry points for Source management."""

# Credential input is validated and errors are redacted at the command boundary.
# ruff: noqa: TRY301

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Annotated

import typer

from powercontext.paths import powercontext_data_dir
from powercontext.source_management.config import ManagerConfig, RuntimeConfig, read_config, secret
from powercontext.source_management.models import ManagementError
from powercontext.source_management.service import Role, protected_write, service_action

source_app = typer.Typer(name="source", help="Manage Source services and instances.", no_args_is_help=True)
manager_app = typer.Typer(name="manager", no_args_is_help=True)
runtime_app = typer.Typer(name="runtime", no_args_is_help=True)
credentials_app = typer.Typer(name="credentials", no_args_is_help=True)
source_app.add_typer(manager_app)
source_app.add_typer(runtime_app)
ConfigFile = Annotated[Path, typer.Option("--config", help="Path to the deployment configuration.")]


def output(value: object) -> None:
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


def fail(error: Exception) -> None:
    if isinstance(error, ManagementError):
        typer.echo(f"{error.code}: {error.message}", err=True)
    else:
        # Validation inputs can include tokens. Do not print exception payloads.
        typer.echo(
            f"deployment-failed: {type(error).__name__}; check configuration and private credential files", err=True
        )
    raise typer.Exit(1) from error


def _operate(role: Role, action: str, path: Path, system_service: bool = False) -> None:
    try:
        config = read_config(path, ManagerConfig if role == "manager" else RuntimeConfig)
        if action == "install":
            secret(config.server.credential_file)
            if isinstance(config, RuntimeConfig):
                secret(config.management.credential_file)
        if action in {"install", "uninstall"} or system_service:
            output(service_action(role, config, "install" if action == "start" else action))
        else:
            from powercontext.source_management.background import process_action

            output(process_action(role, config, action))
    except (OSError, ValueError, RuntimeError, ManagementError) as error:
        fail(error)


def _commands(app: typer.Typer, role: Role) -> None:
    for action in ("start", "install", "status", "stop", "uninstall"):

        def register(action: str) -> None:
            def command(
                config: ConfigFile,
                system_service: Annotated[
                    bool, typer.Option(help="Operate an explicitly installed system service.")
                ] = False,
            ) -> None:
                _operate(role, action, config, system_service)

            app.command(action)(command)

        register(action)


_commands(manager_app, "manager")
_commands(runtime_app, "runtime")


@manager_app.command("run", hidden=True)
def manager_run(config: ConfigFile) -> None:
    """Native service entry point; install uses systemd and returns immediately."""
    import uvicorn

    from powercontext.source_management.manager import create_manager_app

    try:
        settings = read_config(config, ManagerConfig)
        secret(settings.server.credential_file)
        uvicorn.run(create_manager_app(settings), host=settings.host, port=settings.port, access_log=False)
    except Exception as error:
        fail(error)


@runtime_app.command("run", hidden=True)
def runtime_run(config: ConfigFile) -> None:
    """Native service entry point for one managed Source instance."""
    from powercontext.source_management.runtime import run_runtime

    try:
        asyncio.run(run_runtime(read_config(config, RuntimeConfig)))
    except Exception as error:
        fail(error)


@credentials_app.command("set")
def credentials_set(
    alias: Annotated[str, typer.Argument(help="Local external credential reference.")],
    plugin: Annotated[str, typer.Option(help="Module or local entry declaring credential fields.")],
) -> None:
    """Prompt for plugin-declared external secrets and persist only protected files."""
    from powercontext.source_management.loading import load_local_module

    try:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", alias):
            raise ManagementError("invalid-alias", "Credential aliases must be simple local names")
        # Loading a plugin is a trusted local code operation; no Server is contacted.
        module = load_local_module(Path.cwd(), plugin)
        names = sorted(getattr(module, "credential_fields", ()))
        if not names:
            raise ManagementError("no-credentials", "This plugin declares no external credential fields")
        if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) for name in names):
            raise ManagementError("invalid-secret-name", "Plugin credential names must be simple local names")
        values = {name: typer.prompt(name, hide_input=True) for name in names}
        if any(not value.strip() for value in values.values()):
            raise ManagementError("empty-secret", "Credential values must not be empty")
        directory = powercontext_data_dir() / "source-credentials" / alias
        for name, value in values.items():
            protected_write(directory / name, value)
        output({"credential": alias, "fields": names})
    except Exception as error:
        fail(error)


@source_app.command("load")
def load_command(
    config: ConfigFile,
    kind: Annotated[
        str, typer.Option(help="write loads trusted code in Server; collect deploys a Runtime.")
    ] = "collect",
    server_url: Annotated[str | None, typer.Option(help="Running Server URL for write deployment.")] = None,
    credential_file: Annotated[Path | None, typer.Option(help="Server administrator credential file.")] = None,
    env_file: Annotated[Path | None, typer.Option(help="The running Server's environment file.")] = None,
    update: Annotated[bool, typer.Option(help="Replace a stopped collect deployment; keep it disabled.")] = False,
) -> None:
    """Load trusted write code in Server, or deploy one collect instance."""
    from powercontext.server.settings import ServerSettings
    from powercontext.source_management.deployment import DeploymentCoordinator

    try:
        if kind == "collect":
            import yaml

            from powercontext.source_management.deployment import LocalInstance

            local = LocalInstance.model_validate(yaml.safe_load(config.read_text(encoding="utf-8")))
            if local.deployment is not None:
                from powercontext.source_management.remote_deployment import load

                output(load(config, local, update=update))
                return
        settings = ServerSettings(_env_file=env_file) if env_file is not None else ServerSettings()  # ty: ignore[unknown-argument]
        if kind == "write":
            if update:
                raise ManagementError("invalid-update", "Write code updates require a controlled Server restart")
            import httpx
            import yaml

            from powercontext.source_management.write import WritePluginConfig

            configuration = WritePluginConfig.model_validate(yaml.safe_load(config.read_text(encoding="utf-8")))
            entry_path = config.resolve().parent / configuration.entry
            if entry_path.exists():
                configuration = configuration.model_copy(update={"entry": str(entry_path.resolve())})
            token = (
                secret(credential_file)
                if credential_file is not None
                else (None if settings.auth.token is None else settings.auth.token.get_secret_value())
            )
            url = server_url or settings.public_url or f"http://127.0.0.1:{settings.http.port}"
            from powercontext.client.settings import normalize_server_url

            url = normalize_server_url(url)
            with httpx.Client(
                base_url=url, headers={} if token is None else {"Authorization": "Bearer " + token}, timeout=60
            ) as client:
                response = client.post("/v1/source-management/write/load", json=configuration.model_dump(mode="json"))
                if response.status_code >= 400:
                    error = response.json().get("error", {})
                    raise ManagementError(
                        error.get("code", "deployment-failed"),
                        error.get("message", "Server rejected the write plugin"),
                        response.status_code,
                    )
                output(response.json())
            return
        if kind != "collect":
            raise ManagementError("invalid-kind", "Choose write or collect")
        if settings.source_plugins_dir is None and not settings.source_management.autostart:
            raise ManagementError(
                "deployment-unconfigured",
                "Configure source_plugins_dir or source_management.autostart on the running Server",
            )
        output(asyncio.run(DeploymentCoordinator(settings).load(config, update=update)))
    except Exception as error:
        fail(error)


def register_commands(app: typer.Typer) -> set[str]:
    app.add_typer(source_app)
    app.add_typer(credentials_app)
    return {"source", "credentials"}


if __name__ == "__main__":
    source_app()
