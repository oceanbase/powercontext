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

"""Process-local registration and shared storage of plugin-owned Artifact families.

Plugin families keep no durable declaration: the Server and each Worker register
them from the plugin directory at startup. Content is opaque JSON; core builds no
index for it, and plugins present and search their own families.
"""

from __future__ import annotations

import json
from functools import cache
from typing import Any

from pydantic import BaseModel, ConfigDict, JsonValue, RootModel
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.revision_meta import attach_revision_meta, revision_meta
from powercontext.builtin.persistence.tables import ARTIFACTS_TABLE

_PLUGIN_FAMILIES: set[str] = set()
# binding name -> (Scope allowlist or None, consumed Source types)
_SOURCE_BINDINGS: dict[str, tuple[frozenset[str] | None, frozenset[str]]] = {}


class PluginArtifactContent(BaseModel):
    """Stored body of one plugin revision; core never interprets ``content``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: dict[str, JsonValue]


class PluginContentView(RootModel[dict[str, JsonValue]]):
    """The public shape of a plugin revision: the plugin's content only."""


def register_plugin_family(family: str) -> None:
    from powercontext.builtin.authz.profiles import register_plugin_artifact_family

    _PLUGIN_FAMILIES.add(family)
    register_plugin_artifact_family(family)


def unregister_plugin_family(family: str) -> None:
    """A removed plugin's family is no longer readable: its content usually lives in the plugin's storage."""

    from powercontext.server.authz.profiles import unregister_plugin_artifact_family

    _PLUGIN_FAMILIES.discard(family)
    unregister_plugin_artifact_family(family)


def register_source_binding(binding_name: str, scopes: frozenset[str] | None, source_types: frozenset[str]) -> None:
    """Let Source writes in this process mark the plugin's Scope invocation dirty."""

    _SOURCE_BINDINGS[binding_name] = (scopes, source_types)


def unregister_source_binding(binding_name: str) -> None:
    _SOURCE_BINDINGS.pop(binding_name, None)


def replace_registrations(
    families: tuple[str, ...], bindings: tuple[tuple[str, tuple[str, ...] | None, tuple[str, ...]], ...]
) -> None:
    """Make this process's plugin families and Source bindings exactly the given ones."""

    for family in _PLUGIN_FAMILIES - set(families):
        unregister_plugin_family(family)
    for family in families:
        register_plugin_family(family)
    for binding in set(_SOURCE_BINDINGS) - {name for name, _, _ in bindings}:
        unregister_source_binding(binding)
    for binding, scopes, source_types in bindings:
        register_source_binding(binding, None if scopes is None else frozenset(scopes), frozenset(source_types))


def source_bindings(scope_id: str) -> tuple[str, ...]:
    return tuple(sorted(name for name, (scopes, _) in _SOURCE_BINDINGS.items() if scopes is None or scope_id in scopes))


def is_plugin_family(family: str) -> bool:
    return family in _PLUGIN_FAMILIES


def plugin_families() -> frozenset[str]:
    return frozenset(_PLUGIN_FAMILIES)


@cache
def plugin_artifact_type(family: str) -> type[Artifact[Any]]:
    return type("PluginArtifact", (Artifact[PluginArtifactContent],), {"family": family})


@cache
def plugin_view_type(family: str) -> type[Artifact[Any]]:
    return type("PluginArtifactView", (Artifact[PluginContentView],), {"family": family})


def plugin_artifact_repository(family: str, sources: Any = None) -> ArtifactRepository:
    return ArtifactRepository((plugin_artifact_type(family),), sources=sources)


def plugin_view(artifact: Artifact[Any]) -> Artifact[Any]:
    """Present a stored revision as the plugin's content alone."""

    view = plugin_view_type(artifact.family)(
        artifact_id=artifact.artifact_id,
        revision=artifact.revision,
        content=PluginContentView(artifact.content.content),
        lineage=artifact.lineage,
    )
    attach_revision_meta(view, revision_meta(artifact))
    return view


def revision_metadata(plugin: str, version: str) -> bytes:
    """Canonical JSON naming the plugin and code version that wrote a revision."""

    return json.dumps({"plugin": plugin, "version": version}, separators=(",", ":"), sort_keys=True).encode()


async def record_revision_metadata(
    connection: AsyncConnection, scope_id: str, family: str, artifact_id: str, revision: int, metadata: bytes
) -> None:
    await connection.execute(
        update(ARTIFACTS_TABLE)
        .where(
            ARTIFACTS_TABLE.c.scope_id == scope_id,
            ARTIFACTS_TABLE.c.family == family,
            ARTIFACTS_TABLE.c.artifact_id == artifact_id,
            ARTIFACTS_TABLE.c.revision == revision,
        )
        .values(metadata=metadata)
    )
