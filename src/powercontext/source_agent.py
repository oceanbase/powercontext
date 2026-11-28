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

The runner only connects out to the central Server. It first registers: the Server
decides its name, which the runner keeps in a local state file, unless a fixed name is
given for deployments without persistent state. Each sync reports the Source plugins it
loaded and fetches every target assigned to it. Each plugin collects on its own
schedule, and new targets right away. For each target the runner submits its Sources
first and then commits the new checkpoint against the old one, together with the
target's status and the configuration version it collected under; a failure anywhere
leaves the checkpoint where it was, so the next collection repeats the same Sources,
which the Server deduplicates. A failing plugin or target never stops the others.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import secrets
import socket
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
            # The Server gets only the error type; its text, which may hold local paths, stays in this log.
            logger.warning("Plugin %s failed to load", entry.name, exc_info=True)
            report.append({"name": entry.name, "version": None, "error": type(error).__name__})
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

    # A machine without any time zone configuration has no local zone name; UTC is tzlocal's
    # documented default for that state, and the same fallback the Server runner applies.
    zone = ZoneInfo(schedule.timezone or tzlocal.get_localzone_name() or "UTC")
    upcoming = CronTrigger.from_crontab(schedule.expression, timezone=zone).get_next_fire_time(None, now)
    return 60.0 if upcoming is None else max(0.0, (upcoming - now).total_seconds())


class RunnerNameTaken(Exception):
    """The fixed name belongs to a runner another principal registered."""


def _state_path(state_dir: Path, server: str) -> Path:
    # One registration per central Server, so one device can serve several deployments.
    return state_dir / f"source-agent-{hashlib.sha256(server.rstrip('/').encode()).hexdigest()[:16]}.json"


def _write_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{secrets.token_hex(4)}")
    staging.write_text(json.dumps(state), encoding="utf-8")
    os.replace(staging, path)


class SourceAgent:
    def __init__(
        self,
        server: str,
        token: str,
        plugins_dir: Path,
        *,
        name: str | None = None,
        suggest_name: str | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.server = server
        self.token = token
        self.plugins, self.report = load_source_plugins(plugins_dir)
        # A fixed name is registered as given and needs no local state.
        self.fixed_name = name
        self.suggest_name = suggest_name
        self.state_dir = state_dir
        self.name: str | None = None
        self.resources = AsyncExitStack()
        # When each (plugin, Scope) collects next, and when each target was last attempted.
        self._due: dict[tuple[str, str], float] = {}
        self._attempted: dict[str, float] = {}
        # Targets whose last committed attempt left more to collect; they go first in the next call.
        self._more: set[str] = set()
        self._known: set[str] = set()

    async def __aenter__(self) -> SourceAgent:
        self.http = await self.resources.enter_async_context(
            httpx.AsyncClient(base_url=self.server, headers={"Authorization": f"Bearer {self.token}"}, timeout=60)
        )
        try:
            self.name = await self._register()
        except BaseException:
            await self.resources.aclose()
            raise
        return self

    async def _register(self) -> str:
        if self.fixed_name is not None:
            response = await self.http.post("/v1/source-runners", json={"name": self.fixed_name})
            if response.status_code == 409:
                raise RunnerNameTaken(self.fixed_name)
            response.raise_for_status()
            return response.json()["runner"]
        from powercontext.paths import powercontext_data_dir

        path = _state_path(self.state_dir or powercontext_data_dir(), self.server)
        state: dict[str, str] = {}
        if path.is_file():
            state = json.loads(path.read_text(encoding="utf-8"))
        if "registration_id" not in state:
            # Saved before registering, so a registration whose reply was lost is repeated, not duplicated.
            state = {"registration_id": secrets.token_urlsafe(24)}
            _write_state(path, state)
        request = {
            "registration_id": state["registration_id"],
            "suggested_name": self.suggest_name or socket.gethostname(),
        }
        response = await self.http.post("/v1/source-runners", json=request)
        response.raise_for_status()
        runner = response.json()["runner"]
        if state.get("runner") != runner:
            _write_state(path, {**state, "runner": runner})
        return runner

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
            try:
                # A schedule whose next time cannot be computed fails like a failed collect: this plugin
                # is due again next cycle and the other plugins and Scopes go on. The previous deadline is
                # dropped first, so a target that arrived in this cycle does not wait behind it.
                self._due.pop(key, None)
                self._due[key] = now + _next_delay(plugin.collect, datetime.now(UTC))
                outcomes.extend(await self._collect(plugin, key[1], targets, "target_added" if added else "schedule"))
            except Exception as error:
                # One plugin's failure, also while reporting it, leaves the other plugins and Scopes collecting.
                logger.warning("Plugin %s collection failed (%s)", plugin.name, type(error).__name__)
                outcomes.extend({"target_id": target["target_id"], "error": "collect_failed"} for target in targets)
        self._known = {target["target_id"] for target in synced["targets"]}
        self._more &= self._known
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
        # Many targets rotate: those with more to collect go first, then the least recently attempted.
        chosen = sorted(
            targets,
            key=lambda target: (
                target["target_id"] not in self._more,
                self._attempted.get(target["target_id"], 0.0),
            ),
        )[: collect.max_targets]
        request = CollectRequest(
            scope_id=scope_id,
            reason=reason,
            targets=tuple(
                CollectTarget(target_id=item["target_id"], parameters=item["parameters"], checkpoint=item["checkpoint"])
                for item in chosen
            ),
            max_items=collect.max_items,
        )
        versions = {item["target_id"]: item["config_version"] for item in chosen}
        for item in chosen:
            self._attempted[item["target_id"]] = time.monotonic()
            # Kept only by an attempt that commits and reports more.
            self._more.discard(item["target_id"])
        try:
            result = _checked(
                await asyncio.wait_for((await self._collector(plugin)).collect(request), collect.timeout_seconds),
                request,
            )
        except Exception as error:
            # A failed call commits nothing; the same checkpoints are offered next time.
            logger.warning("Plugin %s collect failed (%s)", plugin.name, type(error).__name__)
            for item in request.targets:
                await self._report(
                    plugin, scope_id, item, versions[item.target_id], ("collect_failed", type(error).__name__)
                )
            return [{"target_id": item.target_id, "error": "collect_failed"} for item in request.targets]
        outcomes = []
        for item in request.targets:
            outcome = result.targets.get(item.target_id)
            if outcome is None:
                continue
            version = versions[item.target_id]
            if outcome.error_code is not None:
                await self._report(plugin, scope_id, item, version, (outcome.error_code, outcome.error_message or ""))
                outcomes.append({"target_id": item.target_id, "error": outcome.error_code})
                continue
            try:
                submitted = [await self._submit(plugin, scope_id, collected) for collected in outcome.items]
                changed = await self._commit(plugin, scope_id, item, outcome.checkpoint, version)
            except Exception as error:
                # Also what the plugin returned in a wrong shape: this target fails, the others go on.
                logger.warning("Plugin %s submit failed (%s)", plugin.name, type(error).__name__)
                await self._report(plugin, scope_id, item, version, ("submit_failed", type(error).__name__))
                outcomes.append({"target_id": item.target_id, "error": "submit_failed"})
                continue
            if changed:
                # The target changed while it was collected; the next sync brings its new configuration.
                outcomes.append({"target_id": item.target_id, "error": "target_changed"})
                continue
            if outcome.has_more:
                self._more.add(item.target_id)
            outcomes.append({"target_id": item.target_id, "sources": submitted})
        return outcomes

    async def _report(
        self, plugin: _Plugin, scope_id: str, target: CollectTarget, version: int, error: tuple[str, str]
    ) -> None:
        """Report a failed attempt on the target; the checkpoint stays where it was."""

        try:
            await self._commit(plugin, scope_id, target, target.checkpoint, version, error=error)
        except Exception as report_error:
            logger.warning("Plugin %s status report failed (%s)", plugin.name, type(report_error).__name__)

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
        config_version: int,
        *,
        error: tuple[str, str] | None = None,
    ) -> bool:
        """Commit the checkpoint with the attempt's status; True when the Server refused it for a changed target."""

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
                "config_version": config_version,
            },
        )
        if response.status_code == 409 and _error_code(response) == "target_changed":
            return True
        if error is None:
            response.raise_for_status()
        elif response.is_error:
            logger.warning("Plugin %s status report failed (%s)", plugin.name, response.status_code)
        return False


def _error_code(response: httpx.Response) -> str | None:
    try:
        return response.json()["error"]["code"]
    except (ValueError, KeyError, TypeError):
        return None


def source_agent(
    server: Annotated[str, typer.Option("--server", help="The central PowerContext Server URL")],
    plugins: Annotated[Path, typer.Option("--plugins", help="Directory of Source plugins")],
    name: Annotated[
        str | None,
        typer.Option(
            "--name",
            help="Register under exactly this name and keep no local state; for containers and CI. "
            "Fails when another principal registered the name",
        ),
    ] = None,
    suggest_name: Annotated[
        str | None,
        typer.Option(
            "--suggest-name",
            help="The name to ask the Server for at the first start; the host name by default. "
            "The Server assigns another one when it is taken",
        ),
    ] = None,
    state_dir: Annotated[
        Path | None,
        typer.Option(
            "--state-dir", help="Where the registered name is kept; the PowerContext data directory by default"
        ),
    ] = None,
    interval: Annotated[float, typer.Option("--interval", min=1, help="Seconds between syncs")] = 30.0,
    once: Annotated[bool, typer.Option("--once", help="Sync and collect every target once, then exit")] = False,
) -> None:
    """Collect on this device with Source plugins and submit to the central Server."""

    token = os.environ.get("POWERCONTEXT_SOURCE_AGENT_TOKEN")
    if not token:
        raise typer.BadParameter("set POWERCONTEXT_SOURCE_AGENT_TOKEN to the runner's credential")  # noqa: TRY003
    if name is not None and (suggest_name is not None or state_dir is not None):
        raise typer.BadParameter("--name keeps no local state; do not combine it with --suggest-name or --state-dir")  # noqa: TRY003

    async def run() -> None:
        async with SourceAgent(
            server, token, plugins, name=name, suggest_name=suggest_name, state_dir=state_dir
        ) as agent:
            while True:
                try:
                    outcomes = await agent.cycle(collect_all=once)
                    if once:
                        typer.echo(json.dumps({"runner": agent.name, "outcomes": outcomes}))
                        return
                except Exception as error:
                    # Cancellation and interrupts are not Exceptions and still stop the runner.
                    if once:
                        raise
                    # Includes plugin_version_incompatible while the Server upgrades; retry at the next sync.
                    logger.warning("Cycle failed (%s); retrying", type(error).__name__)
                await asyncio.sleep(interval)

    try:
        asyncio.run(run())
    except RunnerNameTaken as error:
        raise typer.BadParameter(  # noqa: TRY003
            f"another principal registered the runner name {error}; an administrator must delete that record first"
        ) from None
