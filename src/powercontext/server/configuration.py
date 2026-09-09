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

"""Shared configuration loading for foreground and native-service Server entry points."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_settings import SettingsError

from powercontext.cli.env_file import EnvironmentFileError, environment_context, read_environment_file
from powercontext.paths import POWERCONTEXT_HOME_ENV
from powercontext.server.settings import ServerSettings

DEFAULT_SERVER_ENV_FILE = Path(".env")
_SERVER_ENVIRONMENT_PREFIX = "POWERCONTEXT_SERVER_"


def _is_server_environment_name(name: str) -> bool:
    """Match Server settings the same way pydantic-settings matches environment names."""

    return name.casefold().startswith(_SERVER_ENVIRONMENT_PREFIX.casefold())


class ServerConfigurationError(ValueError):
    """Report a failure while loading or constructing Server settings."""

    def __init__(self, cause: EnvironmentFileError | OSError | ValidationError | ValueError) -> None:
        super().__init__(str(cause))
        self.cause = cause


def resolve_server_environment_file(
    env_file: Path | None,
    *,
    discover: bool,
    directory: Path | None = None,
) -> Path | None:
    """Select an explicit env file or discover ``.env`` in one CLI working directory."""

    if env_file is not None:
        expanded = env_file.expanduser()
        return Path(os.path.abspath(expanded))
    if not discover:
        return None
    candidate = (Path.cwd() if directory is None else directory) / DEFAULT_SERVER_ENV_FILE
    return Path(os.path.abspath(candidate)) if candidate.is_file() else None


@contextmanager
def server_settings_context(
    *,
    host: str | None = None,
    port: int | None = None,
    env_file: Path | None = None,
    environment: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
    process_environment_overrides: bool = False,
) -> Iterator[ServerSettings]:
    """Load one reproducible Server configuration for the lifetime of a process operation.

    Explicit environment files remain authoritative by default for service and maintenance
    entry points. ``server run`` opts into process-environment precedence explicitly.
    """

    if env_file is not None and environment is not None:
        raise ServerConfigurationError(ValueError("env_file and environment are mutually exclusive"))
    try:
        loaded: Mapping[str, str] = (
            read_environment_file(env_file)
            if env_file is not None
            else dict(environment)
            if environment is not None
            else {}
        )
    except (EnvironmentFileError, OSError) as error:
        raise ServerConfigurationError(error) from error
    server_environment = {name for name in os.environ if _is_server_environment_name(name)}
    if env_file is not None:
        if process_environment_overrides:
            # Use the project's strict parser for every assignment. Applying only names that
            # are absent from the process environment gives ServerSettings the desired
            # process > dotenv > default precedence without asking pydantic-settings to parse
            # the same file a second time with python-dotenv semantics.
            process_names = {name.casefold() for name in os.environ}
            runtime_environment = {
                name: value for name, value in loaded.items() if name.casefold() not in process_names
            }
            loaded_context = environment_context(runtime_environment, override=False)
        else:
            # Native services and maintenance commands must keep the historical file-authority
            # behavior. Their launcher/controller also use this mode, so preflight and runtime
            # resolve the same effective configuration.
            loaded_context = environment_context(loaded, override=True, clear=server_environment)
    elif environment is not None or data_dir is not None:
        if data_dir is not None:
            loaded = {**loaded, POWERCONTEXT_HOME_ENV: str(data_dir.expanduser().resolve())}
            server_environment.add(POWERCONTEXT_HOME_ENV)
        loaded_context = environment_context(loaded, override=True, clear=server_environment)
    else:
        loaded_context = nullcontext()
    data_dir_context = (
        environment_context({POWERCONTEXT_HOME_ENV: str(data_dir.expanduser().resolve())}, override=True)
        if env_file is not None and data_dir is not None
        else nullcontext()
    )
    with loaded_context, data_dir_context:
        http_overrides: dict[str, Any] = {}
        if host is not None:
            http_overrides["host"] = host
        if port is not None:
            http_overrides["port"] = port
        settings_kwargs: dict[str, Any] = {"http": http_overrides} if http_overrides else {}
        try:
            settings = ServerSettings(**settings_kwargs)
        except (SettingsError, ValidationError) as error:
            raise ServerConfigurationError(error) from error
        yield settings


__all__ = [
    "DEFAULT_SERVER_ENV_FILE",
    "ServerConfigurationError",
    "resolve_server_environment_file",
    "server_settings_context",
]
