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

"""Deployment configuration and protected secret references."""

from __future__ import annotations

import os
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field, field_validator

from powercontext.client.transport_policy import normalize_client_url
from powercontext.source_management.models import Model
from powercontext.source_management.storage import canonical
from powercontext.transport import is_loopback_host


class Endpoint(Model):
    url: str
    credential_file: Path

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        normalized = normalize_client_url(value)
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" and not is_loopback_host(parsed.hostname or ""):
            raise ValueError("Source management credentials require HTTPS outside loopback")  # noqa: TRY003
        return normalized


class ManagerConfig(Model):
    server: Endpoint
    deployment_id: str
    manager_id: str
    state_dir: Path
    host: str = "127.0.0.1"
    port: int = Field(default=8010, ge=1, le=65535)
    request_seconds: float = Field(default=10, gt=0, le=60)
    lease_seconds: float = Field(default=30, ge=3, le=300)


class RuntimeConfig(Model):
    deployment_id: str = "powercontext"
    instance_id: str
    plugin: str
    plugin_dir: Path
    management: Endpoint
    server: Endpoint
    state_dir: Path
    execution_location: str
    configuration_digest: str
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, Path] = Field(default_factory=dict)


class CollectDeployment(Model):
    """Execution-machine deployment references; administrator credentials stay out of RuntimeConfig."""

    server: Endpoint
    management_url: str
    execution_credential_file: Path
    state_dir: Path
    deployment_id: str = "powercontext"
    start: Literal["background", "local-service"] = "background"

    @field_validator("management_url")
    @classmethod
    def normalize_management_url(cls, value: str) -> str:
        return Endpoint(url=value, credential_file=Path("unused")).url


def configuration_digest(plugin: str, config: dict[str, Any], secrets) -> str:
    """Digest the approved business parameters and declared secret aliases, without their values."""
    return sha256(canonical({"plugin": plugin, "config": config, "secrets": sorted(secrets)}).encode()).hexdigest()


def read_config(path: Path, model):
    path = path.expanduser().resolve()
    value = model.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
    for name in ("state_dir", "plugin_dir"):
        configured = getattr(value, name, None)
        if configured is not None:
            object.__setattr__(value, name, (path.parent / configured).resolve())
    for name in ("server", "management"):
        endpoint = getattr(value, name, None)
        if endpoint is not None:
            object.__setattr__(endpoint, "credential_file", (path.parent / endpoint.credential_file).resolve())
    return value


def secret(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise ValueError("Credential must be a regular protected file")  # noqa: TRY003
    if os.name == "posix" and path.stat().st_mode & 0o077:
        raise ValueError("Credential file must not be accessible to other users (chmod 600)")  # noqa: TRY003
    value = path.read_text(encoding="utf-8").strip()
    if not value:
        raise ValueError("Credential file is empty")  # noqa: TRY003
    return value
