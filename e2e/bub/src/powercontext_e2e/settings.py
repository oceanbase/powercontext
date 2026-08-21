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


def codex_auth_path() -> Path:
    """Resolve Codex's native authentication document location."""

    return Path(environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser() / "auth.json"


class ModelNotConfiguredError(RuntimeError):
    """Report model-backed workloads without a runtime model."""

    def __init__(self, workload_ids: tuple[str, ...]) -> None:
        joined_ids = ", ".join(workload_ids)
        super().__init__(f"The following workloads require BUB_MODEL: {joined_ids}")


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
        secret_names = {
            name
            for name in environ
            if name == "BUB_API_KEY"
            or (name.startswith("BUB_") and name.endswith("_API_KEY"))
            or name == "POWERCONTEXT_CLIENT_API_TOKEN"
        }
        values = {environ[name] for name in secret_names if environ[name]}
        if self.agent_proxy_url is not None and (proxy_url := self.agent_proxy_url.get_secret_value()):
            values.add(proxy_url)
        return tuple(sorted(values, key=lambda value: (-len(value), value)))
