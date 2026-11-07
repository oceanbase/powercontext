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

"""Server-owned executable Source definitions and synchronous writes."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import insert, select

from powercontext.builtin.persistence.errors import StoredPayloadConflictError
from powercontext.builtin.persistence.source_definitions import SourceDefinitionManifestRepository
from powercontext.builtin.persistence.tables import SOURCE_WRITE_PLUGINS_TABLE
from powercontext.builtin.runtime.models import SubmitSourceObservation
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.builtin.sources.observation_validation import validate_source_definition_manifest
from powercontext.limits import MAX_SOURCE_OBSERVATION_BYTES
from powercontext.source_management.loading import load_local_module
from powercontext.source_management.models import BUILTIN_CAPABILITY, Capability, ManagementError, Model
from powercontext.sources import SourceDefinitionRegistry, manifest_for_definition, project_source_for_transport

logger = logging.getLogger(__name__)


class WritePluginConfig(Model):
    id: str = Field(min_length=1, max_length=128, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")
    entry: str = Field(min_length=1, max_length=4096)


@dataclass(frozen=True)
class LoadedWritePlugin:
    configuration: WritePluginConfig
    registry: SourceDefinitionRegistry
    capabilities: tuple[Capability, ...]

    def declaration(self) -> dict[str, Any]:
        return {
            "configuration": self.configuration.model_dump(mode="json"),
            "capabilities": [capability.declaration() for capability in self.capabilities],
            "definitions": [
                manifest_for_definition(d).model_dump(mode="json", by_alias=True) for d in self.registry.definitions
            ],
        }


def load_write_plugin(config: WritePluginConfig, workspace: Path) -> LoadedWritePlugin:
    path = (workspace / config.entry).expanduser()
    if path.exists():
        config = config.model_copy(update={"entry": str(path.resolve())})
    module = load_local_module(workspace, config.entry)
    registry = SourceDefinitionRegistry(module.definitions)
    if not registry.definitions:
        raise ManagementError("invalid-plugin", "A write plugin must export Source definitions")
    builtin_names = {d.name for d in BUILTIN_SOURCE_REGISTRY.definitions}
    if any(d.name in builtin_names for d in registry.definitions):
        raise ManagementError("definition-conflict", "A plugin cannot replace a built-in Source definition", 409)
    capabilities = tuple(c for value in module.capabilities if (c := Capability.model_validate(value)).kind == "write")
    if not capabilities:
        raise ManagementError("invalid-plugin", "A write plugin must export write capabilities")
    identities = set()
    for capability in capabilities:
        key = capability.name, capability.version
        if capability.name == BUILTIN_CAPABILITY.name or key in identities:
            raise ManagementError("capability-conflict", "Write capability identities must be unique", 409)
        identities.add(key)
        definition = registry.definition_for_name(capability.definition_name or "")
        if not issubclass(definition.input_class, BaseModel) or capability.input_schema.get("type") != "object":
            raise ManagementError("invalid-plugin", "Write inputs must use a Pydantic object model")
        if (
            definition.version != capability.definition_version
            or capability.input_schema != definition.input_class.model_json_schema()
        ):
            raise ManagementError("invalid-plugin", "Write capabilities must match their Definition and input model")
    for definition in registry.definitions:
        _validate_definition(definition)
    return LoadedWritePlugin(config, registry, capabilities)


def _validate_definition(definition) -> None:
    if any(not inspect.iscoroutinefunction(getattr(definition, name)) for name in ("resolve", "read")):
        raise ManagementError("invalid-plugin", "Source Definition resolve and read must be async")
    if any(inspect.iscoroutinefunction(p.project) for p in definition.projections):
        raise ManagementError("invalid-plugin", "Source Projection project must be synchronous")
    for method in (definition.resolve, definition.read, *(p.project for p in definition.projections)):
        try:
            inspect.signature(method).bind(object())
        except (TypeError, ValueError) as error:
            raise ManagementError(
                "invalid-plugin", "Definition and Projection methods must accept one value"
            ) from error
    validate_source_definition_manifest(manifest_for_definition(definition))


class SourceWritePlugins:
    def __init__(self, database, workspace: Path) -> None:
        self.database, self.workspace = database, workspace
        self.plugins: dict[str, LoadedWritePlugin] = {}
        self._lock = asyncio.Lock()
        self._jobs: set[asyncio.Task[Any]] = set()

    def capabilities(self) -> tuple[Capability, ...]:
        return tuple(c for p in self.plugins.values() for c in p.capabilities)

    def find(self, name: str, version: str) -> tuple[LoadedWritePlugin, Capability] | None:
        return next(
            ((p, c) for p in self.plugins.values() for c in p.capabilities if (c.name, c.version) == (name, version)),
            None,
        )

    async def restore(self) -> None:
        async with self.database.transaction() as connection:
            rows = (await connection.execute(select(SOURCE_WRITE_PLUGINS_TABLE))).mappings().all()
        for row in rows:
            try:
                definition = json.loads(row["definition"])
                await self.load(WritePluginConfig.model_validate(definition["configuration"]))
            except Exception as error:
                # Entrypoint/configuration exceptions may contain secrets or submitted content.
                logger.error(  # noqa: TRY400 - never log entry/configuration exception inputs
                    "Source write plugin %s could not be restored (%s)", row["plugin_id"], type(error).__name__
                )

    async def load(
        self, config: WritePluginConfig, *, reserved_capabilities: set[tuple[str, str]] | None = None
    ) -> dict[str, Any]:
        async with self._lock:
            path = (self.workspace / config.entry).expanduser()
            if path.exists():
                config = config.model_copy(update={"entry": str(path.resolve())})
            old = self.plugins.get(config.id)
            if old is not None:
                if old.configuration != config:
                    raise ManagementError("plugin-conflict", "The plugin id already has a different entry", 409)
                return self.state(old)
            try:
                candidate = await asyncio.to_thread(load_write_plugin, config, self.workspace)
            except ManagementError:
                raise
            except Exception as error:
                raise ManagementError(
                    "invalid-plugin", "The local entry or Source declarations could not be loaded"
                ) from error
            # Each capability identity has exactly one loaded owner. A collect declaration
            # cannot make write executable, and conflicts are checked before publication.
            if any(
                self.find(c.name, c.version) is not None or (c.name, c.version) in (reserved_capabilities or ())
                for c in candidate.capabilities
            ):
                raise ManagementError("capability-conflict", "The write capability is already loaded", 409)
            await self._persist(candidate)
            self.plugins[config.id] = candidate
            return self.state(candidate)

    async def _persist(self, plugin: LoadedWritePlugin) -> None:
        definition = plugin.declaration()
        table = SOURCE_WRITE_PLUGINS_TABLE
        try:
            async with self.database.transaction() as connection:
                saved = await connection.scalar(
                    select(table.c.definition).where(table.c.plugin_id == plugin.configuration.id)
                )
                if saved is not None and json.loads(saved) != definition:
                    raise ManagementError(
                        "plugin-conflict", "The persisted plugin configuration or contract differs", 409
                    )
                repository = SourceDefinitionManifestRepository()
                for source in plugin.registry.definitions:
                    await repository.register(connection, manifest_for_definition(source))
                if saved is None:
                    await connection.execute(
                        insert(table).values(
                            plugin_id=plugin.configuration.id,
                            definition=json.dumps(definition, ensure_ascii=False).encode(),
                        )
                    )
        except StoredPayloadConflictError as error:
            raise ManagementError(
                "definition-conflict", "A Source Definition version cannot change its contract", 409
            ) from error

    @staticmethod
    def state(plugin: LoadedWritePlugin) -> dict[str, Any]:
        return {
            "id": plugin.configuration.id,
            "entry": plugin.configuration.entry,
            "capabilities": [c.declaration() for c in plugin.capabilities],
        }

    async def write(
        self, application, scope_id: str, name: str, version: str, value: dict[str, Any], authorize
    ) -> dict[str, Any]:
        found = self.find(name, version)
        if found is None:
            raise ManagementError("capability-unavailable", "The write capability version is not loaded", 503)
        plugin, capability = found
        definition = plugin.registry.definition_for_name(capability.definition_name or "")

        def validate_input():
            capability.validate_input(value)
            try:
                return definition.input_class.model_validate(value)
            except Exception as error:
                raise ManagementError("invalid-input", "The Source input is invalid") from error

        def materialize(source):
            try:
                observation = project_source_for_transport(plugin.registry, source)
                size = len(observation.model_dump_json().encode())
            except Exception as error:
                raise ManagementError("invalid-input", "The materialized Source output is invalid") from error
            if (
                observation.source_type != capability.definition_name
                or observation.definition_version != capability.definition_version
            ):
                raise ManagementError("invalid-input", "The resolved Source does not match the write Definition")
            if size > MAX_SOURCE_OBSERVATION_BYTES:
                raise ManagementError("observation-too-large", "Materialized Source observation exceeds 4 MiB")
            return observation

        validated = await self._in_thread(validate_input)
        try:
            source = await plugin.registry.resolve(validated)
        except Exception as error:
            raise ManagementError("invalid-input", "The Source could not be resolved") from error
        observation = await self._in_thread(materialize, source)
        await authorize()
        receipt = await application.ingestion.submit(
            SubmitSourceObservation(scope_id=scope_id, observation=observation)
        )
        return {
            "kind": "write",
            "result": {"outcome": "saved", "source_refs": [receipt.source_ref.model_dump(mode="json")]},
        }

    async def _in_thread(self, function, *args):
        # Cancelling an await does not stop trusted Python code already executing in
        # a thread. Keep its slot until it actually exits, including after a timeout.
        if len(self._jobs) >= 64:
            raise ManagementError("server-busy", "Too many Source materializations are running", 503)
        task = asyncio.create_task(asyncio.to_thread(function, *args))
        self._jobs.add(task)

        def completed(task):
            self._jobs.discard(task)
            if not task.cancelled():
                task.exception()

        task.add_done_callback(completed)
        return await asyncio.shield(task)
