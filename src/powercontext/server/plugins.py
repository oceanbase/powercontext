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

"""Load plugins at startup and hand their calls to the Artifact processing Supervisor.

Nothing about a plugin is persisted: the directory as it is at startup decides which
plugins exist. Plugin code runs only in check, Worker, and host child processes. User
changes to plugin Artifacts and the plugin's own routes go to its host process, which
authorizes, prechecks, and writes as the caller; this process only authenticates and
forwards.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from collections.abc import Callable
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import timedelta
from functools import partial
from pathlib import Path
from typing import Any

from sqlalchemy import select

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE
from powercontext.builtin.plugins.executor import PluginWorkerSpec, run_plugin_worker
from powercontext.builtin.plugins.families import register_plugin_family, register_source_binding, revision_metadata
from powercontext.builtin.plugins.host_protocol import PluginHostSpec
from powercontext.builtin.plugins.manifest import PluginManifest
from powercontext.builtin.plugins.writer import plugin_merge_service
from powercontext.builtin.records import BaseValueNotFoundError
from powercontext.builtin.runtime.artifact_merge import ArtifactMergeAccess
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    ArtifactProcessingSupervisors,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.builtin.runtime.processing_discovery import SourceProcessingPendingProvider
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY
from powercontext.http._generated.models import PluginPage, PluginScopePage, PluginScopeState, PluginState
from powercontext.plugins import Cron, Interval
from powercontext.server.plugin_hosts import PluginHost

logger = logging.getLogger(__name__)
CHECK_TIMEOUT_SECONDS = 60
# How soon a dirty Scope is admitted when the plugin asks to run on new Sources.
ON_SOURCES_INTERVAL_SECONDS = 2.0


@dataclass
class _Plugin:
    name: str
    directory: Path
    manifest: PluginManifest | None = None
    error: str | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    host: PluginHost | None = None

    @property
    def failure(self) -> str | None:
        return self.error if self.error is not None or self.host is None else self.host.failure

    def state(self) -> PluginState:
        spec = None if self.manifest is None else self.manifest.spec
        triggers = []
        if spec is not None:
            triggers = [
                name
                for name, enabled in (("schedule", spec.schedule is not None), ("sources", spec.on_sources))
                if enabled
            ]
        return PluginState.model_validate({
            "name": self.name,
            "kind": "artifact",
            "status": "failed" if self.failure is not None else "loaded",
            "error": self.failure,
            "family": None if spec is None else spec.family,
            "binding_name": None if self.manifest is None else self.manifest.binding_name,
            "scopes": None if spec is None or spec.scopes is None else list(spec.scopes),
            "source_types": None if spec is None or spec.inputs is None else list(spec.inputs.source_types),
            "triggers": triggers,
        })


async def check_plugin_directory(directory: Path) -> PluginManifest:
    """Import user code only in a time-limited child process."""

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "powercontext.builtin.plugins.manifest",
        str(directory),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), CHECK_TIMEOUT_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ValueError("plugin check timed out") from None  # noqa: TRY003
    lines = output.decode("utf-8", "replace").strip().splitlines()
    try:
        result = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise ValueError("plugin check produced no result") from None  # noqa: TRY003
    if "error" in result:
        raise ValueError(result["error"])
    return PluginManifest.model_validate(result["manifest"])


class PluginManager:
    def __init__(
        self,
        database: AsyncDatabase,
        config: DatabaseConfig,
        supervisor: ArtifactProcessingSupervisors | None,
        security_builder: Callable[[], dict[str, Any] | None],
        on_supervisor: Callable[[ArtifactProcessingSupervisors], None],
        plugins_dir: Path | None,
        *,
        register_merge: Callable[[Any], None] | None = None,
        dispatch: bool = True,
        inference_config: Any = None,
        storage_root: Path | None = None,
        host_security_builder: Callable[[], dict[str, Any] | None] | None = None,
        serve_requests: bool = True,
    ) -> None:
        self.database = database
        self.register_merge = register_merge
        # API-only instances register families for reads but never dispatch plugin Workers.
        self.dispatch = dispatch
        self.config = config
        self.supervisor = supervisor
        self.security_builder = security_builder
        self.on_supervisor = on_supervisor
        self.plugins_dir = None if plugins_dir is None else plugins_dir.expanduser().resolve()
        self.plugins: dict[str, _Plugin] = {}
        self._resources = AsyncExitStack()
        self.inference_config = inference_config
        self.storage_root = None if storage_root is None else str(storage_root)
        self.host_security_builder = host_security_builder or security_builder
        # Instances that receive no requests (the background role) start no host processes.
        self.serve_requests = serve_requests

    async def __aenter__(self) -> PluginManager:
        directory = self.plugins_dir
        if directory is None:
            return self
        try:
            entries = await asyncio.to_thread(lambda: sorted(path for path in directory.iterdir() if path.is_dir()))
        except OSError as error:
            log_safely(logger, logging.WARNING, f"Plugin directory unavailable ({type(error).__name__})")
            return self
        families: set[str] = set()
        for entry in entries:
            plugin = _Plugin(name=entry.name, directory=entry.resolve())
            self.plugins[entry.name] = plugin
            try:
                manifest = await check_plugin_directory(plugin.directory)
                if manifest.spec.family in families:
                    raise ValueError("family is already provided by another plugin")  # noqa: TRY003, TRY301
                plugin.manifest = manifest
                families.add(manifest.spec.family)
                await self._register(plugin)
            except Exception as error:
                plugin.error = str(error) if isinstance(error, ValueError) else type(error).__name__
                log_safely(logger, logging.WARNING, f"Plugin {entry.name!r} failed to load")
        if self.serve_requests:
            self._prepare_hosts()
        return self

    def _prepare_hosts(self) -> None:
        loaded = [
            (plugin, plugin.manifest)
            for plugin in self.plugins.values()
            if plugin.error is None and plugin.manifest is not None
        ]
        families = tuple(sorted(manifest.spec.family for _, manifest in loaded))
        bindings = tuple(
            (manifest.binding_name, manifest.spec.scopes, manifest.spec.inputs.source_types)
            for _, manifest in loaded
            if manifest.spec.inputs is not None
        )
        security = self.host_security_builder()
        for plugin, manifest in loaded:
            plugin.host = PluginHost(
                plugin.name,
                PluginHostSpec(
                    database=self.config,
                    directory=str(plugin.directory),
                    manifest=manifest.model_dump(mode="json"),
                    security=security,
                    inference=self.inference_config,
                    plugin_families=families,
                    source_bindings=bindings,
                    storage_root=self.storage_root,
                ),
            )
            # Every loaded plugin's host starts now, so the first request does not wait for a spawn.
            task = asyncio.create_task(self._start_host(plugin.host), name=f"plugin-host-start-{plugin.name}")
            plugin.tasks.append(task)

    @staticmethod
    async def _start_host(host: PluginHost) -> None:
        try:
            await host.start()
        except Exception as error:
            log_safely(logger, logging.WARNING, f"Plugin host {host.name} did not start ({type(error).__name__})")

    async def __aexit__(self, *_: object) -> None:
        for plugin in self.plugins.values():
            for task in plugin.tasks:
                task.cancel()
            await asyncio.gather(*plugin.tasks, return_exceptions=True)
        await asyncio.gather(
            *(plugin.host.close() for plugin in self.plugins.values() if plugin.host is not None),
            return_exceptions=True,
        )
        await self._resources.aclose()

    async def _ensure_supervisor(self) -> ArtifactProcessingSupervisors:
        if self.supervisor is None:
            controller = await self._resources.enter_async_context(
                ArtifactProcessingSupervisor(
                    database=self.database,
                    bindings=(),
                    lease_mode="oceanbase" if isinstance(self.config, OceanBaseConfig) else "single-process",
                )
            )
            self.supervisor = ArtifactProcessingSupervisors((controller,))
            self.on_supervisor(self.supervisor)
        return self.supervisor

    async def _register(self, plugin: _Plugin) -> None:
        manifest = plugin.manifest
        if manifest is None:
            raise ValueError("plugin manifest is unavailable")  # noqa: TRY003
        generate = manifest.spec
        await self._register_source_definitions(manifest)
        register_plugin_family(generate.family)
        if self.register_merge is not None:
            self.register_merge(
                plugin_merge_service(
                    generate.family,
                    ArtifactMergeAccess(generate.family),
                    SourceRepository(BUILTIN_SOURCE_REGISTRY),
                    metadata=revision_metadata(manifest.name, manifest.fingerprint),
                )
            )
        scopes = None if generate.scopes is None else frozenset(generate.scopes)
        inputs = generate.inputs
        if inputs is not None:
            register_source_binding(manifest.binding_name, scopes, frozenset(inputs.source_types))
        interval = None
        cron = None
        if inputs is not None:
            if isinstance(generate.schedule, Cron):
                cron = generate.schedule
            seconds = [
                *([generate.schedule.seconds] if isinstance(generate.schedule, Interval) else []),
                *([ON_SOURCES_INTERVAL_SECONDS] if generate.on_sources else []),
            ]
            interval = timedelta(seconds=min(seconds)) if seconds else None

        async def in_scope(connection, candidates):
            return frozenset(candidates if scopes is None else set(candidates) & scopes)

        # Without a schedule or on_sources the plugin has no background generation and no binding.
        if not self.dispatch or (generate.schedule is None and not generate.on_sources):
            return
        spec = PluginWorkerSpec(
            database=self.config,
            directory=str(plugin.directory),
            manifest=manifest.model_dump(mode="json"),
            security=self.security_builder(),
            inference=self.inference_config,
            storage_root=self.storage_root,
        )
        supervisor = await self._ensure_supervisor()
        await supervisor.replace_binding(
            manifest.binding_name,
            ArtifactProcessingBinding(
                binding_name=manifest.binding_name,
                artifact_family=generate.family,
                launcher=SpawnArtifactProcessingWorkerLauncher(partial(run_plugin_worker, spec)),
                max_workers=generate.max_workers,
                worker_timeout_seconds=generate.timeout_seconds,
                automatic_processing_interval=interval,
                cron=None if cron is None else cron.expression,
                timezone="Asia/Shanghai" if cron is None else cron.timezone,
                pending_provider=None
                if inputs is None
                else SourceProcessingPendingProvider(self.database, manifest.binding_name, generate.family),
                automatic_scope_filter=in_scope,
                invocation_scope_filter=in_scope,
            ),
        )
        supervisor.wake(manifest.binding_name)
        if inputs is None and isinstance(generate.schedule, Interval):
            # Without inputs there is no dirty Source work; request each listed Scope when due.
            plugin.tasks.append(
                asyncio.create_task(self._tick(manifest, generate.schedule.seconds), name=f"plugin-{plugin.name}")
            )

    async def _register_source_definitions(self, manifest: PluginManifest) -> None:
        """Register the manifests of the plugin's Source Definitions; identical manifests are reused."""

        from powercontext.builtin.persistence.source_definitions import SourceDefinitionManifestRepository
        from powercontext.errors import SourceDefinitionNotFoundError
        from powercontext.sources.observations import SourceDefinitionManifest

        repository = SourceDefinitionManifestRepository()
        for item in manifest.source_manifests:
            definition = SourceDefinitionManifest.model_validate(item)
            try:
                BUILTIN_SOURCE_REGISTRY.definition_for_name(definition.name)
            except SourceDefinitionNotFoundError:
                pass
            else:
                raise ValueError(f"Source type {definition.name} is a built-in Source type")  # noqa: TRY003
            async with self.database.transaction() as connection:
                await repository.register(connection, definition)

    async def _tick(self, manifest: PluginManifest, seconds: float) -> None:
        intents = ArtifactProcessingIntentRepository()
        while True:
            await asyncio.sleep(seconds)
            try:
                async with self.database.transaction() as connection:
                    for scope_id in manifest.spec.scopes or ():
                        await intents.request(connection, scope_id, manifest.binding_name)
                if self.supervisor is not None:
                    self.supervisor.wake(manifest.binding_name)
            except Exception as error:
                log_safely(logger, logging.WARNING, f"Plugin schedule request failed ({type(error).__name__})")

    def page(self) -> PluginPage:
        return PluginPage(items=[plugin.state() for _, plugin in sorted(self.plugins.items())])

    def _plugin(self, name: str) -> _Plugin:
        plugin = self.plugins.get(name)
        if plugin is None:
            raise BaseValueNotFoundError("plugin", name)
        return plugin

    def get(self, name: str) -> PluginState:
        return self._plugin(name).state()

    async def scopes(self, name: str, *, cursor: str | None, limit: int) -> PluginScopePage:
        """Per-Scope progress, read from the shared intent and cursor tables by any Server role."""

        manifest = self._plugin(name).manifest
        if manifest is None:
            return PluginScopePage(items=[], next_cursor=None)
        intents = ARTIFACT_PROCESSING_INTENTS_TABLE
        statement = (
            select(intents)
            .where(intents.c.binding_name == manifest.binding_name)
            .order_by(intents.c.scope_id)
            .limit(limit + 1)
        )
        if cursor is not None:
            statement = statement.where(intents.c.scope_id > cursor)
        sources = SourceRepository(BUILTIN_SOURCE_REGISTRY)
        consumes = manifest.spec.inputs is not None
        items = []
        async with self.database.transaction() as connection:
            rows = (await connection.execute(statement)).mappings().all()
            for row in rows[:limit]:
                scope_id = row["scope_id"]
                consumed = source_position = None
                if consumes:
                    saved = await SourceCursorRepository().load(connection, scope_id, manifest.binding_name)
                    consumed = 0 if saved is None else saved.cursor.sequence
                    source_position = await sources.journal_position(connection, scope_id)
                items.append(
                    PluginScopeState(
                        scope_id=scope_id,
                        pending=row["dirty_generation"] > row["clean_generation"]
                        or row["requested_generation"] > row["handled_generation"],
                        consumed_position=consumed,
                        source_position=source_position,
                    )
                )
        return PluginScopePage(items=items, next_cursor=rows[limit - 1]["scope_id"] if len(rows) > limit else None)

    def families(self) -> frozenset[str]:
        """The families of the plugins this manager loaded."""

        return frozenset(
            plugin.manifest.spec.family
            for plugin in self.plugins.values()
            if plugin.error is None and plugin.manifest is not None
        )

    def plugin_for_family(self, family: str) -> PluginManifest | None:
        for plugin in self.plugins.values():
            if plugin.error is None and plugin.manifest is not None and plugin.manifest.spec.family == family:
                return plugin.manifest
        return None

    def loaded_manifest(self, name: str) -> PluginManifest | None:
        plugin = self.plugins.get(name)
        return None if plugin is None or plugin.error is not None else plugin.manifest

    def host(self, name: str) -> PluginHost:
        plugin = self._plugin(name)
        if plugin.host is None:
            raise BaseValueNotFoundError("plugin", name)
        return plugin.host

    def host_for_source_type(self, source_type: str) -> tuple[PluginManifest, PluginHost] | None:
        for plugin in self.plugins.values():
            manifest = plugin.manifest
            if (
                plugin.error is None
                and manifest is not None
                and plugin.host is not None
                and any(item["name"] == source_type for item in manifest.source_manifests)
            ):
                return manifest, plugin.host
        return None

    def host_for_family(self, family: str) -> tuple[PluginManifest, PluginHost] | None:
        for plugin in self.plugins.values():
            manifest = plugin.manifest
            if plugin.error is None and manifest is not None and manifest.spec.family == family:
                return (manifest, plugin.host) if plugin.host is not None else None
        return None
