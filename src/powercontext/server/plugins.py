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

"""Load plugins, follow changes to the plugin directory, and hand calls to Workers and host processes.

Nothing about a plugin is persisted: each Server process scans the plugin directory and
keeps its own view of which plugin versions are loaded. Code is imported only from
read-only version snapshots, and only in check, Worker, and host child processes. User
changes to plugin Artifacts and the plugin's own routes go to its host process, which
authorizes, prechecks, and writes as the caller; this process only authenticates and
forwards.
"""

from __future__ import annotations

import asyncio
import json
import logging
import multiprocessing
import re
import sys
import time
from collections.abc import Callable, Mapping
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any

import tzlocal
from sqlalchemy import select

from powercontext._logging import log_safely
from powercontext.builtin.persistence.cursors import SourceCursorRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_INTENTS_TABLE
from powercontext.builtin.plugins.collector import collection_family, run_source_collect_worker
from powercontext.builtin.plugins.executor import PluginWorkerSpec, run_plugin_worker
from powercontext.builtin.plugins.families import (
    register_plugin_family,
    register_source_binding,
    revision_metadata,
    unregister_plugin_family,
    unregister_source_binding,
)
from powercontext.builtin.plugins.host import run_plugin_upgrade
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
from powercontext.server.plugin_hosts import READY_TIMEOUT_SECONDS, HostProcessBudget, PluginHost
from powercontext.server.plugin_versions import (
    content_digest,
    make_snapshot,
    remove_snapshot,
    remove_stale_snapshots,
)

logger = logging.getLogger(__name__)
CHECK_TIMEOUT_SECONDS = 60
# How soon a dirty Scope is admitted when the plugin asks to run on new Sources.
ON_SOURCES_INTERVAL_SECONDS = 2.0
# A replaced snapshot outlives the Worker time limit by this much before it is deleted.
_WORKER_GRACE_SECONDS = 60.0


class _VersionFailed(Exception):
    def __init__(self, category: str) -> None:
        super().__init__(category)
        self.category = category


async def _run_upgrade(spec: PluginHostSpec, timeout: float) -> bool:  # noqa: ASYNC109 - the child enforces it too
    """Run a new version's upgrade in a short-lived child on an instance without host processes."""

    process = multiprocessing.get_context("spawn").Process(
        target=run_plugin_upgrade, args=(spec,), name="powercontext-plugin-upgrade"
    )
    await asyncio.to_thread(process.start)
    await asyncio.to_thread(process.join, timeout + READY_TIMEOUT_SECONDS)
    if process.is_alive():
        process.kill()
        await asyncio.to_thread(process.join)
        return False
    return process.exitcode == 0


@dataclass
class _Plugin:
    name: str
    # The deployer's directory; it is only scanned, never imported from.
    directory: Path
    manifest: PluginManifest | None = None
    # Why no version of the plugin is loaded.
    error: str | None = None
    tasks: list[asyncio.Task[None]] = field(default_factory=list)
    host: PluginHost | None = None
    snapshot: Path | None = None
    activated_at: datetime | None = None
    # Why the latest newer version was not activated; the version in use stays.
    last_check_failure: str | None = None
    last_digest: str | None = None
    failed_digest: str | None = None

    @property
    def failure(self) -> str | None:
        return self.error if self.error is not None or self.host is None else self.host.failure

    @property
    def code_directory(self) -> Path:
        return self.directory if self.snapshot is None else self.snapshot

    def state(self) -> PluginState:
        manifest = self.manifest
        spec = None if manifest is None else manifest.spec
        artifact = None if manifest is None else manifest.artifact
        source = None if manifest is None else manifest.source
        triggers = []
        if artifact is not None:
            triggers = [
                name
                for name, enabled in (("schedule", artifact.schedule is not None), ("sources", artifact.on_sources))
                if enabled
            ]
        elif source is not None and source.collect is not None:
            triggers = ["schedule"]
        scopes = artifact.scopes if artifact is not None else None if source is None else source.scopes
        return PluginState.model_validate({
            "name": self.name,
            "kind": "source" if source is not None else "artifact",
            "status": "failed" if self.failure is not None else "loaded",
            "error": self.failure,
            "family": None if artifact is None else artifact.family,
            "binding_name": None if manifest is None or not _binds(manifest) else manifest.binding_name,
            "scopes": None if scopes is None else list(scopes),
            "source_types": None if artifact is None or artifact.inputs is None else list(artifact.inputs.source_types),
            "triggers": triggers,
            "version_digest": None if manifest is None else manifest.fingerprint,
            "declaration_digest": None if manifest is None else manifest.declaration_digest,
            "activated_at": self.activated_at,
            "last_check_failure": self.last_check_failure,
            "routes": [
                {"method": route.method, "path": route.path, "access": route.access, "mcp": route.mcp}
                for route in (() if spec is None or spec.api is None else spec.api.routes)
            ],
            "pages": None
            if spec is None or spec.pages is None
            else {"title": spec.pages.title, "path": f"/dashboard/plugins/{self.name}"},
        })


async def check_plugin_directory(directory: Path, name: str | None = None) -> PluginManifest:
    """Import user code only in a time-limited child process."""

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "powercontext.builtin.plugins.manifest",
        str(directory),
        *(() if name is None else (name,)),
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


def _binding_fields(manifest: PluginManifest) -> dict[str, Any]:
    """The declaration fields that decide the plugin's Supervisor binding and schedule."""

    return manifest.spec.model_dump(
        include={"family", "inputs", "schedule", "on_sources", "scopes", "max_workers", "timeout_seconds", "collect"}
    )


def _binds(manifest: PluginManifest) -> bool:
    """Whether the plugin has background work: generation, or collection by the Server's own runner."""

    if manifest.artifact is not None:
        return manifest.artifact.schedule is not None or manifest.artifact.on_sources
    return manifest.source is not None and manifest.source.collect is not None


def _family(manifest: PluginManifest) -> str | None:
    return None if manifest.artifact is None else manifest.artifact.family


def _worker_seconds(manifest: PluginManifest) -> float:
    if manifest.artifact is not None:
        return manifest.artifact.timeout_seconds
    collect = None if manifest.source is None else manifest.source.collect
    return 0.0 if collect is None else collect.timeout_seconds


class _VersionLauncher:
    """Launch each Worker from the plugin version current at dispatch.

    Workers already running finish on the snapshot they started with, so a new version
    with the same binding needs no binding replacement, which would cancel them.
    """

    def __init__(self, manager: PluginManager, name: str) -> None:
        self._manager = manager
        self._name = name

    async def start(self, assignment: Any) -> Any:
        plugin = self._manager.plugins[self._name]
        manifest = plugin.manifest
        if manifest is None:
            raise RuntimeError("the plugin is no longer loaded")  # noqa: TRY003
        self._manager.retain(plugin.snapshot, _worker_seconds(manifest))
        spec = PluginWorkerSpec(
            database=self._manager.config,
            directory=str(plugin.code_directory),
            manifest=manifest.model_dump(mode="json"),
            security=self._manager.security_builder(),
            inference=self._manager.inference_config,
            storage_root=self._manager.storage_root,
        )
        entrypoint = run_plugin_worker if manifest.artifact is not None else run_source_collect_worker
        return await SpawnArtifactProcessingWorkerLauncher(partial(entrypoint, spec)).start(assignment)


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
        unregister_merge: Callable[[str], None] | None = None,
        dispatch: bool = True,
        inference_config: Any = None,
        storage_root: Path | None = None,
        snapshot_root: Path | None = None,
        rescan_seconds: float = 0,
        host_security_builder: Callable[[], dict[str, Any] | None] | None = None,
        serve_requests: bool = True,
        host_processes: Mapping[str, int] | None = None,
        host_processes_total: int | None = None,
    ) -> None:
        self.database = database
        self.register_merge = register_merge
        self.unregister_merge = unregister_merge
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
        self.snapshot_root = snapshot_root
        # 0 turns off rescanning: plugins load once at startup.
        self.rescan_seconds = rescan_seconds
        self.host_security_builder = host_security_builder or security_builder
        # Instances that receive no requests (the background role) start no host processes.
        self.serve_requests = serve_requests
        self.host_processes = dict(host_processes or {})
        self.host_budget = HostProcessBudget(host_processes_total)
        # Snapshots this process made; it deletes only these, once nothing here uses them.
        self._created: set[Path] = set()
        # Snapshots kept after they stopped being current: Workers or retiring host processes may still use them.
        self._kept_until: dict[Path, float] = {}
        self._retiring: dict[Path, int] = {}
        self._background: set[asyncio.Task[Any]] = set()
        self._scanner: asyncio.Task[None] | None = None

    async def __aenter__(self) -> PluginManager:
        if self.plugins_dir is None:
            return self
        started = time.time()
        await self.scan(initial=True)
        if self.snapshot_root is not None:
            current = frozenset(plugin.snapshot for plugin in self.plugins.values() if plugin.snapshot is not None)
            await asyncio.to_thread(remove_stale_snapshots, self.snapshot_root, older_than=started, keep=current)
        if self.rescan_seconds > 0:
            self._scanner = asyncio.create_task(self._rescan(), name="plugin-rescan")
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._scanner is not None:
            self._scanner.cancel()
            await asyncio.gather(self._scanner, return_exceptions=True)
        tasks = [*self._background, *(task for plugin in self.plugins.values() for task in plugin.tasks)]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.gather(
            *(plugin.host.close() for plugin in self.plugins.values() if plugin.host is not None),
            return_exceptions=True,
        )
        await self._resources.aclose()

    # Scanning

    async def _rescan(self) -> None:
        while True:
            await asyncio.sleep(self.rescan_seconds)
            try:
                await self.scan()
            except Exception as error:
                log_safely(logger, logging.WARNING, f"Plugin scan failed ({type(error).__name__})")

    async def scan(self, *, initial: bool = False) -> None:
        """Compare each plugin directory with the version in use and add, update, or remove plugins.

        A changed directory is checked only once two scans in a row see the same content, so a
        half-written directory is never loaded; the first scan at startup does not wait.
        """

        directory = self.plugins_dir
        if directory is None:
            return
        started = time.perf_counter()
        try:
            entries = await asyncio.to_thread(lambda: sorted(path for path in directory.iterdir() if path.is_dir()))
        except OSError as error:
            log_safely(logger, logging.WARNING, f"Plugin directory unavailable ({type(error).__name__})")
            return
        names = {entry.name for entry in entries}
        for entry in entries:
            plugin = self.plugins.get(entry.name)
            if plugin is None:
                plugin = self.plugins[entry.name] = _Plugin(name=entry.name, directory=entry.resolve())
            try:
                digest = await asyncio.to_thread(content_digest, plugin.directory)
            except OSError:
                continue
            stable = initial or digest == plugin.last_digest
            plugin.last_digest = digest
            current = None if plugin.manifest is None else plugin.manifest.fingerprint
            if digest in {current, plugin.failed_digest} or not stable:
                continue
            await self._activate(plugin, digest)
        for name in set(self.plugins) - names:
            await self._remove(self.plugins.pop(name))
        await asyncio.to_thread(self._collect_snapshots)
        if not initial or self.rescan_seconds > 0:
            log_safely(
                logger,
                logging.DEBUG,
                "Plugin directory scanned",
                extra={"event": "plugin.scanned", "scan_ms": round((time.perf_counter() - started) * 1000, 3)},
            )

    def retain(self, snapshot: Path | None, seconds: float) -> None:
        """Keep a snapshot at least ``seconds`` past a Worker dispatched from it."""

        if snapshot is not None:
            deadline = time.monotonic() + seconds + _WORKER_GRACE_SECONDS
            self._kept_until[snapshot] = max(deadline, self._kept_until.get(snapshot, 0.0))

    def _collect_snapshots(self) -> None:
        current = {plugin.snapshot for plugin in self.plugins.values()}
        now = time.monotonic()
        for snapshot in list(self._created):
            if snapshot in current or self._retiring.get(snapshot) or self._kept_until.get(snapshot, 0.0) > now:
                continue
            try:
                remove_snapshot(snapshot)
            except OSError:
                continue
            self._created.discard(snapshot)
            self._kept_until.pop(snapshot, None)

    # Activation

    async def _activate(self, plugin: _Plugin, digest: str) -> None:
        if self.snapshot_root is None:
            snapshot = None
        else:
            snapshot = await asyncio.to_thread(make_snapshot, plugin.directory, self.snapshot_root, plugin.name, digest)
            if snapshot is None:
                # The directory changed while it was copied; wait for it to settle again.
                plugin.last_digest = None
                return
            self._created.add(snapshot)
        try:
            manifest = await check_plugin_directory(snapshot or plugin.directory, plugin.name)
            if any(
                other is not plugin
                and other.manifest is not None
                and _family(manifest) is not None
                and _family(other.manifest) == _family(manifest)
                for other in self.plugins.values()
            ):
                raise ValueError("family is already provided by another plugin")  # noqa: TRY003, TRY301
            types = {item["name"] for item in manifest.source_manifests}
            if any(
                other is not plugin
                and other.manifest is not None
                and types & {item["name"] for item in other.manifest.source_manifests}
                for other in self.plugins.values()
            ):
                raise ValueError("a Source type is already provided by another plugin")  # noqa: TRY003, TRY301
        except Exception as error:
            self._check_failed(plugin, digest, str(error) if isinstance(error, ValueError) else type(error).__name__)
            return
        if plugin.manifest is None:
            await self._add(plugin, manifest, snapshot, digest)
        else:
            await self._update(plugin, manifest, snapshot, digest)

    def _check_failed(self, plugin: _Plugin, digest: str, category: str, *, event: str = "check_failed") -> None:
        plugin.failed_digest = digest
        plugin.last_check_failure = category
        if plugin.manifest is None:
            plugin.error = category
        self._event(plugin, event)

    def _event(self, plugin: _Plugin, event: str) -> None:
        manifest = plugin.manifest
        log_safely(
            logger,
            logging.WARNING if event in {"check_failed", "upgrade_failed"} else logging.INFO,
            f"Plugin {plugin.name} {event.replace('_', ' ')}",
            extra={
                "event": "plugin.changed",
                "plugin_name": plugin.name,
                "plugin_event": event,
                "version_digest": None if manifest is None else manifest.fingerprint,
                "declaration_digest": None if manifest is None else manifest.declaration_digest,
            },
        )

    async def _add(self, plugin: _Plugin, manifest: PluginManifest, snapshot: Path | None, digest: str) -> None:
        plugin.manifest, plugin.snapshot, plugin.error = manifest, snapshot, None
        host = None
        try:
            await self._register(plugin)
            failure = await self._prepare_version(plugin, manifest, snapshot)
            if isinstance(failure, str):
                raise _VersionFailed(failure)  # noqa: TRY301
            host = failure
            plugin.host = host
            self._sync_registry()
            await self._bind(plugin)
        except Exception as error:
            await self._unregister(plugin, manifest)
            if host is not None:
                await host.close()
            plugin.manifest = plugin.snapshot = plugin.host = None
            category = error.category if isinstance(error, _VersionFailed) else None
            message = str(error) if isinstance(error, ValueError) else type(error).__name__
            self._check_failed(
                plugin,
                digest,
                category or message,
                event="upgrade_failed" if category == "upgrade_failed" else "check_failed",
            )
            self._sync_registry()
            return
        plugin.activated_at = datetime.now(UTC)
        plugin.last_check_failure = plugin.failed_digest = None
        self._event(plugin, "loaded")

    async def _prepare_version(
        self, plugin: _Plugin, manifest: PluginManifest, snapshot: Path | None
    ) -> PluginHost | str | None:
        """Start the version's host process, or run its upgrade where no host runs.

        Returns the host, None when this instance runs none, or why the version cannot be used.
        A version that declares upgrade is used only after its upgrade finished here.
        """

        spec = self._host_spec(manifest, snapshot or plugin.directory)
        if not self.serve_requests:
            if not manifest.upgrade:
                return None
            return None if await _run_upgrade(spec, manifest.spec.upgrade_timeout_seconds) else "upgrade_failed"
        host = PluginHost(
            plugin.name,
            spec,
            limit=self.host_processes.get(plugin.name, manifest.spec.host_processes),
            budget=self.host_budget,
        )
        await host.start()
        # Without upgrade the first process starts in the background, so a slow start delays no request
        # path; an upgrade must finish before the version is used.
        if manifest.upgrade or plugin.host is not None:
            failure = await host.wait_ready(READY_TIMEOUT_SECONDS + manifest.spec.upgrade_timeout_seconds)
            if failure is not None:
                await host.close()
                return failure
        return host

    async def _update(self, plugin: _Plugin, manifest: PluginManifest, snapshot: Path | None, digest: str) -> None:
        old = plugin.manifest
        assert old is not None  # noqa: S101 - updates replace a loaded version
        prepared = await self._prepare_version(plugin, manifest, snapshot)
        if isinstance(prepared, str):
            self._check_failed(
                plugin, digest, prepared, event="upgrade_failed" if prepared == "upgrade_failed" else "check_failed"
            )
            return
        old_snapshot, old_host = plugin.snapshot, plugin.host
        family_changed = _family(old) != _family(manifest)
        rebind = family_changed or _binding_fields(old) != _binding_fields(manifest)
        try:
            if rebind:
                await self._unbind(plugin)
            if family_changed:
                await self._unregister(plugin, old)
            plugin.manifest, plugin.snapshot, plugin.host = manifest, snapshot, prepared
            if old.declaration_digest != manifest.declaration_digest:
                await self._register(plugin)
            self._sync_registry()
            if rebind:
                await self._bind(plugin)
        except Exception as error:
            await self._abandon(plugin, manifest, prepared, digest, error)
            if old_host is not None:
                self._spawn(self._retire(old_host, old_snapshot))
            return
        if old_snapshot is not None:
            self.retain(old_snapshot, _worker_seconds(old))
        if old_host is not None:
            self._spawn(self._retire(old_host, old_snapshot))
        plugin.activated_at = datetime.now(UTC)
        plugin.last_check_failure = plugin.failed_digest = None
        self._event(plugin, "updated")

    async def _abandon(
        self, plugin: _Plugin, manifest: PluginManifest, prepared: PluginHost | None, digest: str, error: Exception
    ) -> None:
        """Registering the new declaration failed after the old one was released: unload the plugin."""

        if prepared is not None:
            await prepared.close()
        await self._unregister(plugin, manifest)
        plugin.manifest = plugin.snapshot = plugin.host = None
        self._check_failed(plugin, digest, str(error) if isinstance(error, ValueError) else type(error).__name__)
        self._sync_registry()

    async def _remove(self, plugin: _Plugin) -> None:
        manifest = plugin.manifest
        if manifest is None:
            return
        await self._unbind(plugin)
        await self._unregister(plugin, manifest)
        host, snapshot = plugin.host, plugin.snapshot
        plugin.manifest = plugin.host = None
        self._sync_registry()
        if snapshot is not None:
            self.retain(snapshot, _worker_seconds(manifest))
        if host is not None:
            self._spawn(self._retire(host, snapshot))
        self._event(plugin, "removed")

    async def _retire(self, host: PluginHost, snapshot: Path | None) -> None:
        """Let the old version's calls finish within their limits, then stop its processes."""

        if snapshot is not None:
            self._retiring[snapshot] = self._retiring.get(snapshot, 0) + 1
        try:
            await host.retire()
        finally:
            if snapshot is not None:
                self._retiring[snapshot] -= 1

    def _spawn(self, coroutine: Any) -> None:
        task = asyncio.create_task(coroutine)
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _registry(self) -> tuple[tuple[str, ...], tuple[tuple[str, tuple[str, ...] | None, tuple[str, ...]], ...]]:
        loaded = [
            (plugin.manifest, plugin.manifest.artifact)
            for plugin in self.plugins.values()
            if plugin.manifest is not None and plugin.manifest.artifact is not None
        ]
        families = tuple(sorted(artifact.family for _, artifact in loaded))
        bindings = tuple(
            (manifest.binding_name, artifact.scopes, artifact.inputs.source_types)
            for manifest, artifact in loaded
            if artifact.inputs is not None
        )
        return families, bindings

    def _sync_registry(self) -> None:
        """Host processes read and write Sources like this process; tell them which plugins are loaded now."""

        families, bindings = self._registry()
        for plugin in self.plugins.values():
            if plugin.host is not None:
                plugin.host.update_registry(families, bindings)

    def _host_spec(self, manifest: PluginManifest, directory: Path) -> PluginHostSpec:
        families, bindings = self._registry()
        family = _family(manifest)
        if family is not None and family not in families:
            families = tuple(sorted((*families, family)))
        return PluginHostSpec(
            database=self.config,
            directory=str(directory),
            manifest=manifest.model_dump(mode="json"),
            security=self.host_security_builder() if self.serve_requests else self.security_builder(),
            inference=self.inference_config,
            plugin_families=families,
            source_bindings=bindings,
            storage_root=self.storage_root,
        )

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
        """Make the plugin's family, merges, Source types, and Source binding known in this process."""

        manifest = plugin.manifest
        if manifest is None:
            raise ValueError("plugin manifest is unavailable")  # noqa: TRY003
        await self._register_source_definitions(manifest)
        generate = manifest.artifact
        if generate is None:
            return
        register_plugin_family(generate.family)
        if self.register_merge is not None:
            if self.unregister_merge is not None:
                self.unregister_merge(generate.family)
            self.register_merge(
                plugin_merge_service(
                    generate.family,
                    ArtifactMergeAccess(generate.family),
                    SourceRepository(BUILTIN_SOURCE_REGISTRY),
                    metadata=revision_metadata(manifest.name, manifest.fingerprint),
                )
            )
        unregister_source_binding(manifest.binding_name)
        if generate.inputs is not None:
            scopes = None if generate.scopes is None else frozenset(generate.scopes)
            register_source_binding(manifest.binding_name, scopes, frozenset(generate.inputs.source_types))

    async def _unregister(self, plugin: _Plugin, manifest: PluginManifest) -> None:
        # Source Definition manifests stay registered: stored Sources refer to them.
        family = _family(manifest)
        if family is None:
            return
        unregister_plugin_family(family)
        if self.unregister_merge is not None:
            self.unregister_merge(family)
        unregister_source_binding(manifest.binding_name)

    async def _bind(self, plugin: _Plugin) -> None:
        """Register the plugin's background generation or collection with the Supervisor."""

        manifest = plugin.manifest
        assert manifest is not None  # noqa: S101 - only loaded plugins bind
        generate = manifest.artifact
        if generate is None:
            await self._bind_collection(plugin, manifest)
            return
        scopes = None if generate.scopes is None else frozenset(generate.scopes)
        inputs = generate.inputs
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
        supervisor = await self._ensure_supervisor()
        await supervisor.replace_binding(
            manifest.binding_name,
            ArtifactProcessingBinding(
                binding_name=manifest.binding_name,
                artifact_family=generate.family,
                launcher=_VersionLauncher(self, plugin.name),
                max_workers=generate.max_workers,
                worker_timeout_seconds=generate.timeout_seconds,
                automatic_processing_interval=interval,
                cron=None if cron is None else cron.expression,
                timezone="Asia/Shanghai" if cron is None else cron.timezone or tzlocal.get_localzone_name(),
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

    async def _bind_collection(self, plugin: _Plugin, manifest: PluginManifest) -> None:
        """The Server's own runner collects this plugin's targets in Supervisor Workers."""

        declared = manifest.source
        if not self.dispatch or declared is None or declared.collect is None:
            return
        collect = declared.collect
        allowed = None if declared.scopes is None else frozenset(declared.scopes)

        async def in_scope(connection, candidates):
            return frozenset(candidates if allowed is None else set(candidates) & allowed)

        supervisor = await self._ensure_supervisor()
        await supervisor.replace_binding(
            manifest.binding_name,
            ArtifactProcessingBinding(
                binding_name=manifest.binding_name,
                artifact_family=collection_family(manifest.name),
                launcher=_VersionLauncher(self, plugin.name),
                max_workers=collect.max_workers,
                worker_timeout_seconds=collect.timeout_seconds,
                automatic_scope_filter=in_scope,
                invocation_scope_filter=in_scope,
            ),
        )
        plugin.tasks.append(asyncio.create_task(self._collect_due(manifest), name=f"plugin-{plugin.name}"))

    async def _collect_due(self, manifest: PluginManifest) -> None:
        from zoneinfo import ZoneInfo

        from apscheduler.triggers.cron import CronTrigger

        declared = manifest.source
        assert declared is not None and declared.collect is not None  # noqa: S101 - collecting plugins only
        schedule = declared.collect.schedule
        while True:
            if isinstance(schedule, Interval):
                delay = schedule.seconds
            else:
                zone = ZoneInfo(schedule.timezone or tzlocal.get_localzone_name())
                now = datetime.now(UTC)
                upcoming = CronTrigger.from_crontab(schedule.expression, timezone=zone).get_next_fire_time(None, now)
                delay = 60.0 if upcoming is None else max(0.0, (upcoming - now).total_seconds())
            await asyncio.sleep(delay)
            await self.request_collection(manifest.name)

    async def request_collection(self, name: str, scope_id: str | None = None) -> None:
        """Ask the Server's own runner to collect a plugin's targets now, in one Scope or wherever it has targets."""

        from powercontext.builtin.persistence.source_collection import SERVER_RUNNER, SourceCollectionRepository

        manifest = self.loaded_manifest(name)
        if manifest is None or manifest.source is None or manifest.source.collect is None or not self.dispatch:
            return
        intents = ArtifactProcessingIntentRepository()
        try:
            async with self.database.transaction() as connection:
                scopes = (
                    (scope_id,)
                    if scope_id is not None
                    else await SourceCollectionRepository().scopes_with_targets(connection, SERVER_RUNNER, name)
                )
                for scope in scopes:
                    await intents.request(connection, scope, manifest.binding_name)
            if self.supervisor is not None:
                self.supervisor.wake(manifest.binding_name)
        except Exception as error:
            log_safely(logger, logging.WARNING, f"Plugin collection request failed ({type(error).__name__})")

    async def _unbind(self, plugin: _Plugin) -> None:
        for task in plugin.tasks:
            task.cancel()
        await asyncio.gather(*plugin.tasks, return_exceptions=True)
        plugin.tasks.clear()
        manifest = plugin.manifest
        if manifest is not None and self.dispatch and self.supervisor is not None:
            # Removing a binding cancels its running Workers; their windows run again later.
            await self.supervisor.replace_binding(manifest.binding_name, None)

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

    def openapi(self, name: str) -> dict[str, Any]:
        manifest = self.loaded_manifest(name)
        if manifest is None:
            raise BaseValueNotFoundError("plugin", name)
        return plugin_openapi(manifest)

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
        consumes = manifest.artifact is not None and manifest.artifact.inputs is not None
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
            family
            for plugin in self.plugins.values()
            if plugin.error is None and plugin.manifest is not None and (family := _family(plugin.manifest)) is not None
        )

    def plugin_for_family(self, family: str) -> PluginManifest | None:
        for plugin in self.plugins.values():
            if plugin.error is None and plugin.manifest is not None and _family(plugin.manifest) == family:
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

    def source_plugin(self, name: str) -> tuple[PluginManifest, PluginHost | None] | None:
        """A loaded Source plugin and its host process, by plugin name."""

        plugin = self.plugins.get(name)
        if plugin is None or plugin.error is not None or plugin.manifest is None or plugin.manifest.source is None:
            return None
        return plugin.manifest, plugin.host

    def host_for_family(self, family: str) -> tuple[PluginManifest, PluginHost] | None:
        for plugin in self.plugins.values():
            manifest = plugin.manifest
            if plugin.error is None and manifest is not None and _family(manifest) == family:
                return (manifest, plugin.host) if plugin.host is not None else None
        return None


_ERRORS = {
    "401": "The caller is not authenticated.",
    "403": "The caller lacks the access the route requires.",
    "404": "The plugin, route, or resource was not found.",
    "413": "The request body exceeds the route's limit.",
    "422": "The request or its content is invalid.",
    "500": "The plugin failed.",
    "503": "The plugin is temporarily unavailable.",
    "504": "The plugin did not finish within the route's time limit.",
}


def plugin_openapi(manifest: PluginManifest) -> dict[str, Any]:
    """Describe the routes one plugin version declares; the plugin owns their compatibility."""

    from powercontext.builtin.plugins.manifest import mcp_tool_name

    error = {"$ref": "#/components/schemas/Error"}
    paths: dict[str, dict[str, Any]] = {}
    api = manifest.spec.api
    for route in () if api is None else api.routes:
        parameters = [
            {"name": parameter, "in": "path", "required": True, "schema": {"type": "string"}}
            for parameter in re.findall(r"{([A-Za-z_][A-Za-z0-9_]*)}", route.path)
        ]
        operation: dict[str, Any] = {
            "operationId": mcp_tool_name(manifest.name, route.handler),
            "parameters": parameters,
            "x-powercontext-access": {
                "access": route.access,
                **({} if route.scope_param is None else {"scope_param": route.scope_param}),
            },
            "responses": {
                "200": {
                    "description": "The handler's result.",
                    "content": {"application/json": {"schema": route.response_schema or {}}},
                },
                "204": {"description": "The handler returned nothing."},
                **{
                    status: {"description": text, "content": {"application/json": {"schema": error}}}
                    for status, text in _ERRORS.items()
                },
            },
        }
        if route.description is not None:
            operation["description"] = route.description
        if route.body == "json":
            schema = route.request_schema or {}
            operation["requestBody"] = {"required": True, "content": {"application/json": {"schema": schema}}}
        elif route.body == "binary":
            content = {"application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}
            operation["requestBody"] = {"required": True, "content": content}
        paths.setdefault(f"/v1/plugins/{manifest.name}/api{route.path}", {})[route.method.lower()] = operation
    return {
        "openapi": "3.1.0",
        "info": {"title": f"{manifest.name} plugin", "version": manifest.fingerprint[:12]},
        "paths": paths,
        "components": {
            "schemas": {
                "Error": {
                    "type": "object",
                    "required": ["error"],
                    "properties": {
                        "error": {
                            "type": "object",
                            "required": ["code", "message", "details"],
                            "properties": {
                                "code": {"type": "string"},
                                "message": {"type": "string"},
                                "details": {"type": ["object", "null"]},
                            },
                        }
                    },
                }
            }
        },
    }
