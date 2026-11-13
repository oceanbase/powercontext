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

"""A device collection runner: sync targets from the central Server, collect, and submit.

The runner only connects out to the central Server. Each sync reports the Source
plugins it loaded and fetches every target assigned to it. Each plugin collects on its
own schedule, and new targets right away. For each target the runner submits its
Sources first and then commits the new checkpoint against the old one, together with
the target's status; a failure anywhere leaves the checkpoint where it was, so the next
collection repeats the same Sources, which the Server deduplicates.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import defaultdict
from contextlib import AsyncExitStack
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
import typer
from pydantic import JsonValue

from powercontext.plugins import (
    Collect,
    CollectRequest,
    CollectResult,
    CollectTarget,
    Cron,
    PluginContext,
    PluginResponse,
    SourcePlugin,
)
from powercontext.sources.collection import TARGET_CHECKPOINT_VERSION

logger = logging.getLogger("powercontext.source_agent")


class _Center:
    """``ctx.center``: the plugin's own routes on the central Server, called with the runner's credential."""

    def __init__(self, client: httpx.AsyncClient, plugin: str, version: str) -> None:
        self._client = client
        self._plugin = plugin
        self._version = version

    async def call_plugin(
        self,
        method: str,
        path: str,
        /,
        *,
        json: JsonValue | None = None,
        content: bytes | None = None,
        content_type: str | None = None,
    ) -> PluginResponse:
        headers = {"X-PowerContext-Plugin-Version": self._version}
        if content_type is not None:
            headers["Content-Type"] = content_type
        response = await self._client.request(
            method, f"/v1/plugins/{self._plugin}/api{path}", json=json, content=content, headers=headers
        )
        kind = response.headers.get("content-type", "")
        is_json = kind.startswith("application/json") and bool(response.content)
        return PluginResponse(
            status=response.status_code,
            content_type=kind or None,
            json_body=response.json() if is_json else None,
            content=None if is_json else response.content,
        )


class _Plugin:
    def __init__(self, name: str, directory: Path, declaration: SourcePlugin) -> None:
        from powercontext.sources.definitions import SourceDefinitionRegistry

        self.name = name
        self.directory = directory
        self.declaration = declaration
        self.registry = SourceDefinitionRegistry(declaration.definitions)
        self.collector: Any = None

    @property
    def collect(self) -> Collect:
        assert self.declaration.collect is not None  # noqa: S101 - only collecting plugins load
        return self.declaration.collect


def load_source_plugins(directory: Path) -> tuple[dict[str, _Plugin], list[dict[str, JsonValue]]]:
    """Load every collecting Source plugin; the report lists each with its version or load error."""

    from powercontext.builtin.plugins.manifest import load_plugin

    plugins = {}
    report: list[dict[str, JsonValue]] = []
    for entry in sorted(path for path in directory.iterdir() if (path / "plugin.py").is_file()):
        try:
            _, declaration = load_plugin(entry)
        except Exception as error:
            message = str(error) if isinstance(error, ValueError) else type(error).__name__
            report.append({"name": entry.name, "version": None, "error": message[:1024]})
            continue
        if isinstance(declaration, SourcePlugin) and declaration.collect is not None:
            plugins[entry.name] = _Plugin(entry.name, entry.resolve(), declaration)
            report.append({"name": entry.name, "version": declaration.version, "error": None})
    return plugins, report


def _checked(result: object, request: CollectRequest) -> CollectResult:
    """The collection contract, enforced as on the Server: no unknown targets and no more than max_items."""

    if not isinstance(result, CollectResult):
        raise TypeError("collect must return a CollectResult")  # noqa: TRY003
    if set(result.targets) - {item.target_id for item in request.targets}:
        raise ValueError("collect returned a target that was not requested")  # noqa: TRY003
    if sum(len(outcome.items) for outcome in result.targets.values()) > request.max_items:
        raise ValueError("collect returned more items than max_items")  # noqa: TRY003
    return result


def _next_delay(collect: Collect, now: datetime) -> float:
    schedule = collect.schedule
    if not isinstance(schedule, Cron):
        return schedule.seconds
    from zoneinfo import ZoneInfo

    import tzlocal
    from apscheduler.triggers.cron import CronTrigger

    zone = ZoneInfo(schedule.timezone or tzlocal.get_localzone_name())
    upcoming = CronTrigger.from_crontab(schedule.expression, timezone=zone).get_next_fire_time(None, now)
    return 60.0 if upcoming is None else max(0.0, (upcoming - now).total_seconds())


class SourceAgent:
    def __init__(self, server: str, token: str, plugins_dir: Path, *, name: str | None = None) -> None:
        self.server = server
        self.token = token
        self.plugins, self.report = load_source_plugins(plugins_dir)
        self.name = name
        self.resources = AsyncExitStack()
        # When each (plugin, Scope) collects next, and when each target was last attempted.
        self._due: dict[tuple[str, str], float] = {}
        self._attempted: dict[str, float] = {}
        self._known: set[str] = set()

    async def __aenter__(self) -> SourceAgent:
        self.http = await self.resources.enter_async_context(
            httpx.AsyncClient(base_url=self.server, headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
        )
        if self.name is None:
            # The runner is named after its credential's subject unless told otherwise.
            response = await self.http.get("/v1/access/principal")
            response.raise_for_status()
            self.name = response.json()["principal"]["id"]
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.resources.aclose()

    async def cycle(self, *, collect_all: bool = False) -> list[dict[str, Any]]:
        """Sync once, then collect every (plugin, Scope) that is due, has new targets, or ``collect_all``."""

        response = await self.http.post(f"/v1/source-runners/{self.name}/sync", json={"plugins": self.report})
        response.raise_for_status()
        synced = response.json()
        for rejected in synced["rejected"]:
            logger.warning("Plugin %s rejected by the Server (%s)", rejected["name"], rejected["code"])
        groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for target in synced["targets"]:
            groups[(target["capability"], target["scope_id"])].append(target)
        now = time.monotonic()
        outcomes = []
        for key, targets in groups.items():
            plugin = self.plugins.get(key[0])
            if plugin is None:
                continue
            added = [target for target in targets if target["target_id"] not in self._known]
            if not (collect_all or added or now >= self._due.get(key, 0.0)):
                continue
            self._due[key] = now + _next_delay(plugin.collect, datetime.now(UTC))
            outcomes.extend(await self._collect(plugin, key[1], targets, "target_added" if added else "schedule"))
        self._known = {target["target_id"] for target in synced["targets"]}
        return outcomes

    async def _collector(self, plugin: _Plugin) -> Any:
        if plugin.collector is None:
            context = PluginContext(
                name=plugin.name,
                scope_id=None,
                plugin_dir=plugin.directory,
                logger=logging.getLogger(f"powercontext.plugin.{plugin.name}"),
                resources=self.resources,
                center=_Center(self.http, plugin.name, plugin.declaration.version),
            )
            assert plugin.declaration.setup is not None  # noqa: S101 - collect requires setup
            plugin.collector = await plugin.declaration.setup(context)
        return plugin.collector

    async def _collect(
        self,
        plugin: _Plugin,
        scope_id: str,
        targets: list[dict[str, Any]],
        reason: Literal["schedule", "target_added"],
    ) -> list[dict[str, Any]]:
        collect = plugin.collect
        # Many targets rotate: the least recently attempted go first.
        chosen = sorted(targets, key=lambda target: self._attempted.get(target["target_id"], 0.0))[
            : collect.max_targets
        ]
        request = CollectRequest(
            scope_id=scope_id,
            reason=reason,
            targets=tuple(
                CollectTarget(target_id=item["target_id"], parameters=item["parameters"], checkpoint=item["checkpoint"])
                for item in chosen
            ),
            max_items=collect.max_items,
        )
        for item in chosen:
            self._attempted[item["target_id"]] = time.monotonic()
        try:
            result = _checked(
                await asyncio.wait_for((await self._collector(plugin)).collect(request), collect.timeout_seconds),
                request,
            )
        except Exception as error:
            # A failed call commits nothing; the same checkpoints are offered next time.
            logger.warning("Plugin %s collect failed (%s)", plugin.name, type(error).__name__)
            for item in request.targets:
                await self._commit(
                    plugin, scope_id, item, item.checkpoint, error=("collect_failed", type(error).__name__)
                )
            return [{"target_id": item.target_id, "error": "collect_failed"} for item in request.targets]
        outcomes = []
        for item in request.targets:
            outcome = result.targets.get(item.target_id)
            if outcome is None:
                continue
            if outcome.error_code is not None:
                await self._commit(
                    plugin, scope_id, item, item.checkpoint, error=(outcome.error_code, outcome.error_message or "")
                )
                outcomes.append({"target_id": item.target_id, "error": outcome.error_code})
                continue
            try:
                submitted = [await self._submit(plugin, scope_id, collected) for collected in outcome.items]
                await self._commit(plugin, scope_id, item, outcome.checkpoint)
            except httpx.HTTPError as error:
                logger.warning("Plugin %s submit failed (%s)", plugin.name, type(error).__name__)
                outcomes.append({"target_id": item.target_id, "error": "submit_failed"})
                continue
            outcomes.append({"target_id": item.target_id, "sources": submitted})
        return outcomes

    async def _submit(self, plugin: _Plugin, scope_id: str, collected: Any) -> str:
        from powercontext.sources.observations import project_source_for_transport

        definition = plugin.registry.definition_for_name(collected.definition)
        if not isinstance(collected.value, definition.input_class):
            raise TypeError(f"{collected.definition} expects {definition.input_class.__name__}")  # noqa: TRY003
        observation = project_source_for_transport(plugin.registry, await plugin.registry.resolve(collected.value))
        response = await self.http.post(
            "/v1/source-observations",
            json={
                "scope_id": scope_id,
                "observation": observation.model_dump(mode="json"),
                "plugin": {"name": plugin.name, "version": plugin.declaration.version},
            },
        )
        response.raise_for_status()
        return response.json()["source"]["source_id"]

    async def _commit(
        self,
        plugin: _Plugin,
        scope_id: str,
        target: CollectTarget,
        checkpoint: JsonValue | None,
        *,
        error: tuple[str, str] | None = None,
    ) -> None:
        status: dict[str, Any] = {"succeeded": error is None}
        if error is not None:
            status.update(error_code=error[0][:128], error_message=error[1][:1024])
        response = await self.http.post(
            "/v1/connector-checkpoints/commit",
            json={
                "binding": {
                    "scope_id": scope_id,
                    "binding_id": target.target_id,
                    "connector_name": plugin.name,
                    "connector_version": TARGET_CHECKPOINT_VERSION,
                },
                "expected": target.checkpoint,
                "checkpoint": checkpoint,
                "plugin": {"name": plugin.name, "version": plugin.declaration.version},
                "status": status,
            },
        )
        if error is None:
            response.raise_for_status()
        elif response.is_error:
            logger.warning("Plugin %s status report failed (%s)", plugin.name, response.status_code)


def source_agent(
    server: Annotated[str, typer.Option("--server", help="The central PowerContext Server URL")],
    plugins: Annotated[Path, typer.Option("--plugins", help="Directory of Source plugins")],
    name: Annotated[
        str | None, typer.Option("--name", help="Runner name; defaults to the credential's subject")
    ] = None,
    interval: Annotated[float, typer.Option("--interval", min=1, help="Seconds between syncs")] = 30.0,
    once: Annotated[bool, typer.Option("--once", help="Sync and collect every target once, then exit")] = False,
) -> None:
    """Collect on this device with Source plugins and submit to the central Server."""

    token = os.environ.get("POWERCONTEXT_SOURCE_AGENT_TOKEN")
    if not token:
        raise typer.BadParameter("set POWERCONTEXT_SOURCE_AGENT_TOKEN to the runner's credential")  # noqa: TRY003

    async def run() -> None:
        async with SourceAgent(server, token, plugins, name=name) as agent:
            while True:
                try:
                    outcomes = await agent.cycle(collect_all=once)
                    if once:
                        typer.echo(json.dumps({"runner": agent.name, "outcomes": outcomes}))
                        return
                except httpx.HTTPError as error:
                    if once:
                        raise
                    # Includes plugin_version_incompatible while the Server upgrades; retry at the next sync.
                    logger.warning("Sync failed (%s); retrying", type(error).__name__)
                await asyncio.sleep(interval)

    asyncio.run(run())
