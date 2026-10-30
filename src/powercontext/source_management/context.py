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

"""The pipeline and resources owned by one plugin instance."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from pathlib import Path
from typing import TypeVar

from powercontext.client import PowerContextClient, RemoteConnectorWorker
from powercontext.sources import (
    Connector,
    ConnectorBinding,
    ConnectorRunResult,
    ConnectorSubmissionStatus,
    SourceDefinitionRegistry,
)

from .errors import PluginError
from .models import SourceTarget
from .targets import ManagedTargets

T = TypeVar("T")


class Secrets:
    def __init__(self, plugin_dir: Path, paths: Mapping[str, Path]) -> None:
        self._directory = plugin_dir
        self._paths = dict(paths)

    async def get(self, alias: str) -> bytes:
        if alias not in self._paths:
            raise PluginError("secret-missing", "secret alias is not configured for this plugin")
        return (self._directory / self._paths[alias]).read_bytes()


class PluginContext:
    def __init__(
        self,
        *,
        plugin_id: str,
        connector_name: str,
        connector_version: str,
        plugin_dir: Path,
        secrets: Mapping[str, Path],
        client: PowerContextClient,
        registry: SourceDefinitionRegistry,
        targets: ManagedTargets,
    ) -> None:
        self.plugin_id = plugin_id
        self._connector_name = connector_name
        self._connector_version = connector_version
        self.plugin_dir = plugin_dir
        self.secrets = Secrets(plugin_dir, secrets)
        self.logger = logging.getLogger(f"powercontext.source.{plugin_id}")
        self._client = client
        self._registry = registry
        self.targets = targets
        self._stack = AsyncExitStack()
        self._connector: Connector | None = None
        self._batch_running = False
        self._stopping = False

    async def enter_async_context(self, resource: AbstractAsyncContextManager[T]) -> T:
        return await self._stack.enter_async_context(resource)

    def on_dispose(self, callback: Callable[[], Awaitable[None] | None]) -> None:
        async def dispose() -> None:
            result = callback()
            if inspect.isawaitable(result):
                await result

        self._stack.push_async_callback(dispose)

    def connect(self, connector: Connector) -> None:
        if self._connector is not None:
            raise PluginError("plugin-contract", "apply must connect exactly one Connector")
        self._connector = connector

    async def run_batch(
        self,
        *,
        scope_id: str,
        targets: tuple[SourceTarget, ...] | None = None,
    ) -> ConnectorRunResult:
        """Run one Scope's batch with its own checkpoint; reject overlapping batches."""
        if self._stopping:
            raise PluginError("plugin-stopping", "cannot start a batch on a stopping or closed context")
        if self._connector is None:
            raise PluginError("plugin-contract", "connect a Connector before run_batch")
        if self._batch_running:
            raise PluginError("batch-running", "a batch is already running on this PluginContext")
        if targets is not None and any(not isinstance(t, SourceTarget) for t in targets):
            raise PluginError("invalid-batch", "Select collect targets for a managed batch")
        selected = targets
        if selected is None:
            raise PluginError("targets-required", "Managed batches must explicitly select their targets")
        binding = ConnectorBinding(
            scope_id=scope_id,
            binding_id=self.plugin_id,
            connector_name=self._connector_name,
            connector_version=self._connector_version,
        )
        self._batch_running = True
        observer = None
        try:
            observer = self.targets.begin(binding, selected)
            connector = self._connector
            result = await RemoteConnectorWorker(
                client=self._client,
                registry=self._registry,
                observer=observer,
                register_definitions=False,
            ).run(connector, binding)
            accepted = sum(item.status is ConnectorSubmissionStatus.ACCEPTED for item in result.items)
            self.logger.info(
                "%s: %s (%d accepted) [scope=%s]", self.plugin_id, result.status.value, accepted, binding.scope_id
            )
        except BaseException as error:
            if observer is not None:
                await observer.interrupted(type(error).__name__)
            raise
        finally:
            self.targets.end()
            self._batch_running = False
        return result

    def stop(self) -> None:
        self._stopping = True

    async def close(self) -> None:
        self.stop()
        await self._stack.aclose()
