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

"""Validated configuration for the fixed Bub end-to-end harness."""

from __future__ import annotations

import shutil
import subprocess
from os import environ
from pathlib import Path

from powercontext.client.settings import ClientSettings
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def bub_environment() -> dict[str, str]:
    """Return Bub's native environment without translating its settings."""

    return {name: value for name, value in environ.items() if name.startswith("BUB_") and value}


def powercontext_bub_environment() -> dict[str, str]:
    """Return the PowerContext Bub integration's native environment."""

    return {name: value for name, value in environ.items() if name.startswith("POWERCONTEXT_BUB_") and value}


def prefixed_environment(prefix: str) -> dict[str, str]:
    """Return a host integration's native settings that start with ``prefix``, without translating them."""

    return {name: value for name, value in environ.items() if name.startswith(prefix) and value}


def server_api_token() -> str | None:
    """Return the token the harness Client uses, which the ON arm's integration also needs for the same Server."""

    token = ClientSettings().api_token
    return None if token is None else token.get_secret_value()


# Values the harness derives for an agent are held under this prefix, which no integration reads as its own
# setting, so reading an integration's native environment later never returns a derived value.
_AGENT_SECRET_PREFIX = "POWERCONTEXT_E2E_AGENT_SECRET_"  # noqa: S105 - part of a variable name


def agent_secret(name: str, value: str) -> str:
    """Hold a value the harness derives for an agent in the harness's own environment, and return a reference to it.

    ``name`` is the variable the agent reads, such as ``POWERCONTEXT_BUB_API_TOKEN``, or a short name for a value
    that reaches the agent under several variables, such as ``PROXY_URL``.

    The value stays in this process's environment for the rest of the run, where every child process of the harness
    inherits it. That exposes nothing new: Harbor resolves a reference from the host environment and nowhere else,
    and every value held here derives from a setting that reaches the harness only through that same environment,
    as ``POWERCONTEXT_CLIENT_API_TOKEN`` or ``POWERCONTEXT_E2E_AGENT_PROXY_URL``, which those children inherit
    already.
    Harbor writes each agent's environment to its job files. It keeps the first four and last three characters of a
    sensitive literal, which is most of a short token, and writes a literal under any other name in full. It writes a
    ``${NAME}`` reference as it is, whatever the name, and resolves the reference from this process's environment when
    it starts the agent. A value held here is also an evidence secret.
    """

    held = f"{_AGENT_SECRET_PREFIX}{name.removeprefix('POWERCONTEXT_')}"
    environ[held] = value
    return f"${{{held}}}"


def codex_auth_path() -> Path:
    """Resolve Codex's native authentication document location."""

    return Path(environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser() / "auth.json"


_SECRET_SUFFIXES = ("_API_KEY", "_AUTHORIZATION", "_TOKEN", "_SECRET_ACCESS_KEY")
# A token can also be named in the middle, as in AWS_BEARER_TOKEN_BEDROCK. A plural, as in MAX_THINKING_TOKENS, is a
# count, and a name ending in one of _LOCATION_SUFFIXES, as in AWS_WEB_IDENTITY_TOKEN_FILE, says where a token is:
# its value is a path or an address, which redacting by substring would rewrite in the evidence.
_SECRET_INFIX = "_TOKEN_"  # noqa: S105 - part of a variable name
_LOCATION_SUFFIXES = ("_FILE", "_PATH", "_URL")
# Local model servers accept any key, and the placeholders commonly passed to them are ordinary words and numbers.
# They protect nothing, and redacting them by substring would rewrite the evidence. Any other value is redacted,
# however short.
_PLACEHOLDER_CREDENTIALS = frozenset({"1", "true", "none", "null", "empty", "dummy", "ollama", "lm-studio"})


def _names_a_secret(name: str) -> bool:
    # Settings read their variables in any case, so POWERCONTEXT_CLIENT_API_TOKEN may be set in lower case.
    name = name.upper()
    if name.startswith(_AGENT_SECRET_PREFIX):
        return True
    if name.endswith(_LOCATION_SUFFIXES):
        return False
    return name.endswith(_SECRET_SUFFIXES) or _SECRET_INFIX in name


class ModelNotConfiguredError(RuntimeError):
    """Report model-backed workloads whose host lacks its runtime model or another required setting."""

    def __init__(self, workload_ids: tuple[str, ...], settings: tuple[str, ...]) -> None:
        joined_ids = ", ".join(workload_ids)
        super().__init__(f"The following workloads require {', '.join(settings)}: {joined_ids}")


class HarnessSettings(BaseSettings):
    """Configuration owned by the end-to-end harness."""

    model_config = SettingsConfigDict(
        env_ignore_empty=True,
        extra="ignore",
        frozen=True,
        populate_by_name=True,
    )

    database: str = Field(default="unknown", validation_alias="POWERCONTEXT_E2E_DATABASE")
    repository: Path = Field(default_factory=_repository_root, validation_alias="POWERCONTEXT_E2E_REPOSITORY")
    commit: str | None = Field(default=None, validation_alias="GITHUB_SHA")

    agent_proxy_url: SecretStr | None = Field(
        default=None,
        validation_alias="POWERCONTEXT_E2E_AGENT_PROXY_URL",
    )

    def repository_path(self) -> Path:
        return self.repository.expanduser().resolve()

    def commit_id(self) -> str:
        if self.commit:
            return self.commit
        git = shutil.which("git")
        if git is None:
            return "unknown"
        completed = subprocess.run(  # noqa: S603 - executable is resolved by shutil.which
            [git, "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"

    def evidence_secrets(self) -> tuple[str, ...]:
        # Provider keys and tokens, and the full Authorization headers the host integrations send to the Server.
        values = {
            value
            for name, value in environ.items()
            if _names_a_secret(name) and value and value.lower() not in _PLACEHOLDER_CREDENTIALS
        }
        if self.agent_proxy_url is not None and (proxy_url := self.agent_proxy_url.get_secret_value()):
            values.add(proxy_url)
        return tuple(sorted(values, key=lambda value: (-len(value), value)))
