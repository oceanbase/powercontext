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

"""The device runner's choice of targets when a plugin takes fewer per call than it is assigned."""

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

import httpx
import pytest

from powercontext.source_agent import SourceAgent, load_source_plugins

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "source_plugins" / "ticket-chat"


def test_a_target_with_more_to_collect_goes_first_in_the_next_call(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    source = (EXAMPLE / "plugin.py").read_text(encoding="utf-8")
    one_per_call = "collect=Collect(schedule=Interval(seconds=10), max_targets=1)"
    reports_more = "checkpoint=Checkpoint(seen=seen).model_dump(), has_more=Path(settings.file + '.more').exists())"
    edited = source.replace("collect=Collect(schedule=Interval(seconds=10))", one_per_call).replace(
        "checkpoint=Checkpoint(seen=seen).model_dump())", reports_more
    )
    assert one_per_call in edited and reports_more in edited
    (plugins / "ticket-chat").mkdir(parents=True)
    (plugins / "ticket-chat" / "plugin.py").write_text(edited, encoding="utf-8")
    files = {name: tmp_path / f"{name}.json" for name in ("a", "b")}
    for file in files.values():
        shutil.copy(EXAMPLE / "chats.json", file)
    more = Path(f"{files['a']}.more")
    more.touch()
    checkpoints: dict[str, object] = {}

    def central(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sync"):
            targets = [
                {
                    "scope_id": "scope",
                    "target_id": name,
                    "capability": "ticket-chat",
                    "parameters": {"file": str(file)},
                    "config_version": 1,
                    "checkpoint": checkpoints.get(name),
                }
                for name, file in files.items()
            ]
            return httpx.Response(200, json={"targets": targets, "rejected": []})
        if request.url.path == "/v1/source-observations":
            return httpx.Response(200, json={"source": {"source_id": "stored"}})
        body = json.loads(request.content)
        checkpoints[body["binding"]["binding_id"]] = body["checkpoint"]
        return httpx.Response(200, json={})

    async def scenario() -> list[str]:
        agent = SourceAgent("http://central", "token", plugins, name="robot-7")
        agent.name = "robot-7"
        collected = []
        async with httpx.AsyncClient(base_url="http://central", transport=httpx.MockTransport(central)) as client:
            agent.http = client
            async with agent.resources:
                for _ in range(4):
                    outcomes = await agent.cycle(collect_all=True)
                    collected.extend(item["target_id"] for item in outcomes)
                    if len(collected) == 2:
                        # The second call still reported more; the third finds nothing left.
                        await asyncio.to_thread(more.unlink)
        return collected

    # "a" goes first while its last call reported more; once a call reports none, "b" gets its turn.
    assert asyncio.run(scenario()) == ["a", "a", "a", "b"]


def write_plugin_with_collect_schedule(directory: Path, name: str, schedule: str) -> None:
    source = (EXAMPLE / "plugin.py").read_text(encoding="utf-8")
    edited = source.replace("    Interval,\n", "    Cron,\n    Interval,\n").replace(
        "collect=Collect(schedule=Interval(seconds=10))", f"collect=Collect(schedule={schedule})"
    )
    assert f"collect=Collect(schedule={schedule})" in edited and "    Cron,\n" in edited
    (directory / name).mkdir(parents=True)
    (directory / name / "plugin.py").write_text(edited, encoding="utf-8")


@pytest.mark.parametrize(
    "schedule",
    ['Cron(expression="every five minutes")', 'Cron(expression="* * * * *", timezone="Mars/Olympus")'],
    ids=["unparseable-expression", "unknown-timezone"],
)
def test_a_collect_schedule_the_runners_cannot_compute_fails_the_plugin_load(tmp_path, schedule) -> None:
    plugins = tmp_path / "plugins"
    write_plugin_with_collect_schedule(plugins, "ticket-chat", schedule)

    loaded, report = load_source_plugins(plugins)

    assert loaded == {}
    assert report == [{"name": "ticket-chat", "version": None, "error": "ValidationError"}]


def test_a_valid_cron_collect_schedule_still_loads(tmp_path) -> None:
    plugins = tmp_path / "plugins"
    write_plugin_with_collect_schedule(
        plugins, "ticket-chat", 'Cron(expression="*/5 * * * *", timezone="Asia/Shanghai")'
    )

    loaded, report = load_source_plugins(plugins)

    assert list(loaded) == ["ticket-chat"]
    assert report == [{"name": "ticket-chat", "version": "1.0.0", "error": None}]


def test_a_declared_timezone_keeps_the_load_independent_of_the_local_zone(tmp_path, monkeypatch) -> None:
    import apscheduler.triggers.cron as cron_trigger_module

    def no_local_zone():
        from zoneinfo import ZoneInfoNotFoundError

        raise ZoneInfoNotFoundError()

    monkeypatch.setattr(cron_trigger_module, "get_localzone", no_local_zone)
    plugins = tmp_path / "plugins"
    write_plugin_with_collect_schedule(plugins, "ticket-chat", 'Cron(expression="*/5 * * * *", timezone="UTC")')

    loaded, report = load_source_plugins(plugins)

    assert list(loaded) == ["ticket-chat"]
    assert report == [{"name": "ticket-chat", "version": "1.0.0", "error": None}]


def test_a_timezone_less_cron_collects_on_a_machine_without_a_local_zone(tmp_path, monkeypatch) -> None:
    plugins = tmp_path / "plugins"
    write_plugin_with_collect_schedule(plugins, "ticket-chat", 'Cron(expression="*/5 * * * *")')
    chats = tmp_path / "chats.json"
    shutil.copy(EXAMPLE / "chats.json", chats)
    monkeypatch.setattr("tzlocal.get_localzone_name", lambda: None)
    committed: list[str] = []

    def central(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sync"):
            targets = [
                {
                    "scope_id": "scope",
                    "target_id": "t",
                    "capability": "ticket-chat",
                    "parameters": {"file": str(chats)},
                    "config_version": 1,
                    "checkpoint": None,
                }
            ]
            return httpx.Response(200, json={"targets": targets, "rejected": []})
        if request.url.path == "/v1/source-observations":
            return httpx.Response(200, json={"source": {"source_id": "stored"}})
        committed.append(json.loads(request.content)["binding"]["binding_id"])
        return httpx.Response(200, json={})

    async def scenario() -> None:
        agent = SourceAgent("http://central", "token", plugins, name="robot-7")
        agent.name = "robot-7"
        async with httpx.AsyncClient(base_url="http://central", transport=httpx.MockTransport(central)) as client:
            agent.http = client
            async with agent.resources:
                await agent.cycle(collect_all=True)

    asyncio.run(scenario())
    assert committed == ["t"]


def test_a_plugin_whose_next_time_cannot_be_computed_does_not_block_the_others(tmp_path, monkeypatch) -> None:
    plugins = tmp_path / "plugins"
    write_plugin_with_collect_schedule(plugins, "slow", "Interval(seconds=60)")
    write_plugin_with_collect_schedule(plugins, "steady", "Interval(seconds=3600)")
    chats = tmp_path / "steady.json"
    shutil.copy(EXAMPLE / "chats.json", chats)

    def central(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sync"):
            targets = [
                {
                    "scope_id": "scope",
                    "target_id": "t-slow",
                    "capability": "slow",
                    "parameters": {"file": str(tmp_path / "slow.json")},
                    "config_version": 1,
                    "checkpoint": None,
                },
                {
                    "scope_id": "scope",
                    "target_id": "t-steady",
                    "capability": "steady",
                    "parameters": {"file": str(chats)},
                    "config_version": 1,
                    "checkpoint": None,
                },
            ]
            return httpx.Response(200, json={"targets": targets, "rejected": []})
        if request.url.path == "/v1/source-observations":
            return httpx.Response(200, json={"source": {"source_id": "stored"}})
        return httpx.Response(200, json={})

    def broken(collect, now):
        # A stand-in for any scheduling failure; slow is the plugin it hits.
        if collect.schedule.seconds == 60:
            raise ValueError("no next time")  # noqa: TRY003
        return collect.schedule.seconds

    monkeypatch.setattr("powercontext.source_agent._next_delay", broken)

    async def scenario() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        agent = SourceAgent("http://central", "token", plugins, name="robot-7")
        agent.name = "robot-7"
        async with httpx.AsyncClient(base_url="http://central", transport=httpx.MockTransport(central)) as client:
            agent.http = client
            async with agent.resources:
                return await agent.cycle(collect_all=True), await agent.cycle(collect_all=True)

    # slow comes first in the sync order; steady behind it still collects in the same cycle.
    first, second = asyncio.run(scenario())
    assert [item["target_id"] for item in first if "error" in item] == ["t-slow"]
    assert [item["target_id"] for item in first if "error" not in item] == ["t-steady"]
    # The failed plugin does not disable the cycle: both are attempted again next time.
    assert [item["target_id"] for item in second if "error" in item] == ["t-slow"]
    assert [item["target_id"] for item in second if "error" not in item] == ["t-steady"]


def test_a_new_target_is_retried_next_cycle_when_its_next_time_cannot_be_computed(tmp_path, monkeypatch) -> None:
    plugins = tmp_path / "plugins"
    write_plugin_with_collect_schedule(plugins, "steady", "Interval(seconds=3600)")
    chats = tmp_path / "steady.json"
    shutil.copy(EXAMPLE / "chats.json", chats)
    checkpoints: dict[str, object] = {}
    # t-b arrives after t-a was collected and scheduled an hour out.
    arrivals = iter([("t-a",), ("t-a", "t-b"), ("t-a", "t-b")])

    def central(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/sync"):
            targets = [
                {
                    "scope_id": "scope",
                    "target_id": name,
                    "capability": "steady",
                    "parameters": {"file": str(chats)},
                    "config_version": 1,
                    "checkpoint": checkpoints.get(name),
                }
                for name in next(arrivals)
            ]
            return httpx.Response(200, json={"targets": targets, "rejected": []})
        if request.url.path == "/v1/source-observations":
            return httpx.Response(200, json={"source": {"source_id": "stored"}})
        body = json.loads(request.content)
        checkpoints[body["binding"]["binding_id"]] = body["checkpoint"]
        return httpx.Response(200, json={})

    delays = iter([3600.0, ValueError("no next time"), 3600.0])

    def next_delay(collect, now):
        # A stand-in for a next time that can be computed, then cannot, then can again.
        delay = next(delays)
        if isinstance(delay, ValueError):
            raise delay
        return delay

    monkeypatch.setattr("powercontext.source_agent._next_delay", next_delay)

    async def scenario() -> list[list[dict[str, Any]]]:
        agent = SourceAgent("http://central", "token", plugins, name="robot-7")
        agent.name = "robot-7"
        async with httpx.AsyncClient(base_url="http://central", transport=httpx.MockTransport(central)) as client:
            agent.http = client
            async with agent.resources:
                return [await agent.cycle() for _ in range(3)]

    first, second, third = asyncio.run(scenario())
    assert [item["target_id"] for item in first] == ["t-a"]
    assert [(item["target_id"], item["error"]) for item in second] == [
        ("t-a", "collect_failed"),
        ("t-b", "collect_failed"),
    ]
    # The new target does not wait out the hour set for t-a: the next ordinary cycle collects it.
    assert sorted(item["target_id"] for item in third if "error" not in item) == ["t-a", "t-b"]
