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

import httpx

from powercontext.source_agent import SourceAgent

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
