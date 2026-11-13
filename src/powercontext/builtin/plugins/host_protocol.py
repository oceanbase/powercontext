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

"""Picklable messages between the Server and a plugin host process over a duplex Pipe."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

HostMethod = Literal[
    "create_artifact",
    "update_artifact",
    "merge_artifacts",
    "forget_artifact",
    "restore_artifact",
    "create_source",
    "render_artifact",
    "search_artifacts",
    "api",
]
HostOutcome = Literal[
    "ok",
    "invalid_content",
    "not_implemented",
    "forbidden",
    "not_found",
    "precondition_failed",
    "conflict",
    "contract_violation",
    "api_error",
    "model_unavailable",
    "error",
]


@dataclass(frozen=True)
class PluginHostSpec:
    """Everything a fresh interpreter needs to become core runtime plus one plugin."""

    database: Any
    directory: str
    manifest: dict[str, Any]
    security: dict[str, Any] | None
    inference: Any = None
    # Every loaded plugin family and Source binding, so reads and Source writes behave as in the Server.
    plugin_families: tuple[str, ...] = ()
    source_bindings: tuple[tuple[str, tuple[str, ...] | None, tuple[str, ...]], ...] = ()
    storage_root: str | None = None


@dataclass(frozen=True)
class HostReady:
    implemented: tuple[str, ...]


@dataclass(frozen=True)
class HostFailed:
    error: str
    # The plugin breaks its contract, for example it lacks a required method; restarting cannot help.
    permanent: bool = False
    # "upgrade_failed" when the version's upgrade raised or timed out.
    category: str | None = None


@dataclass(frozen=True)
class HostRequest:
    id: str
    method: HostMethod
    scope_id: str | None
    # The caller Server authenticated, and the audit context of the HTTP request; None without access control.
    caller: Any
    audit: Any
    payload: dict[str, Any] = field(default_factory=dict)
    body: Any = None


@dataclass(frozen=True)
class HostResponse:
    id: str
    outcome: HostOutcome
    result: Any = None
    message: str | None = None
    field: str | None = None
    status: int | None = None
    error_type: str | None = None


@dataclass(frozen=True)
class HostShutdown:
    pass


@dataclass(frozen=True)
class HostCancel:
    """The request outlived its time limit; cancel its task so its write transaction rolls back."""

    id: str


@dataclass(frozen=True)
class HostRegistry:
    """The plugin families and Source bindings loaded in the Server now, after a plugin was added or removed."""

    plugin_families: tuple[str, ...]
    source_bindings: tuple[tuple[str, tuple[str, ...] | None, tuple[str, ...]], ...]


@dataclass(frozen=True)
class HostHeartbeat:
    """Sent every second by a task on the host's event loop, so a blocked loop stops sending it."""
