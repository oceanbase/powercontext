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

from powercontext.cli.env_file import EnvironmentFileError, environment_context, read_environment_file
from powercontext.paths import POWERCONTEXT_HOME_ENV
from powercontext.server.settings import ServerSettings


class ServerConfigurationError(ValueError):
    """Report a failure while loading or constructing Server settings."""

    def __init__(self, cause: EnvironmentFileError | OSError | ValidationError | ValueError) -> None:
        super().__init__(str(cause))
        self.cause = cause


@contextmanager
def server_settings_context(
    *,
    host: str | None = None,
    port: int | None = None,
    env_file: Path | None = None,
    environment: Mapping[str, str] | None = None,
    data_dir: Path | None = None,
) -> Iterator[ServerSettings]:
    """Load one reproducible Server configuration for the lifetime of a process operation."""

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
    if data_dir is not None:
        loaded = {**loaded, POWERCONTEXT_HOME_ENV: str(data_dir.expanduser().resolve())}
    server_environment = {name for name in os.environ if name.startswith("POWERCONTEXT_SERVER_")}
    if data_dir is not None:
        server_environment.add(POWERCONTEXT_HOME_ENV)
    loaded_context = (
        environment_context(loaded, override=True, clear=server_environment)
        if env_file is not None or environment is not None or data_dir is not None
        else nullcontext()
    )
    with loaded_context:
        http_overrides: dict[str, Any] = {}
        if host is not None:
            http_overrides["host"] = host
        if port is not None:
            http_overrides["port"] = port
        settings_kwargs: dict[str, Any] = {"http": http_overrides} if http_overrides else {}
        try:
            settings = ServerSettings(**settings_kwargs)
        except ValidationError as error:
            raise ServerConfigurationError(error) from error
        yield settings


__all__ = ["ServerConfigurationError", "server_settings_context"]
