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

"""Plugin host processes cancel only timed-out calls, survive a blocked plugin, and grow under load."""

import asyncio

from tests.e2e.plugin_server import create_scope, running_server, wait_for

PLUGIN = """
import asyncio
import os
import time

from pydantic import BaseModel

from powercontext.plugins import ApiRequest, ArtifactPlugin, PluginApi, Rendering, Route, SearchResult


class Wait(BaseModel):
    seconds: float


class Gate(BaseModel):
    started: str
    release: str


class Slow:
    async def render_artifact(self, request):
        return Rendering(text="")

    async def search_artifacts(self, request):
        await asyncio.sleep(30)
        return SearchResult(hits=[])

    async def wait(self, request: ApiRequest):
        await asyncio.sleep(request.body.seconds)
        return {"pid": os.getpid()}

    async def gate(self, request: ApiRequest):
        # Record which host took the call, then hold it until the test releases every call.
        with open(request.body.started, "a") as started:
            started.write(f"{os.getpid()} ")
        for _ in range(900):
            if os.path.exists(request.body.release):
                break
            await asyncio.sleep(0.1)
        return {"pid": os.getpid()}

    async def block(self, request: ApiRequest):
        time.sleep(request.body.seconds)
        return {"pid": os.getpid()}

    async def crowd(self, request: ApiRequest):
        # Occupy every thread of the default executor for a while, as CPU work moved off the event loop would.
        self.crowd = [asyncio.ensure_future(asyncio.to_thread(time.sleep, request.body.seconds)) for _ in range(64)]
        return {"pid": os.getpid()}


async def setup(ctx):
    return Slow()


def route(path, handler, timeout, model=Wait):
    return Route(
        method="POST",
        path=path,
        handler=handler,
        access="authenticated",
        body="json",
        request_model=model,
        timeout_seconds=timeout,
    )


plugin = ArtifactPlugin(
    family="slow-notes",
    setup=setup,
    change_timeout_seconds=1,
    host_processes=2,
    api=PluginApi(
        routes=(
            route("/wait", "wait", 3),
            route("/hold", "wait", 30),
            route("/block", "block", 1),
            route("/crowd", "crowd", 3),
            route("/gate", "gate", 120, Gate),
        )
    ),
)
"""


def test_plugin_hosts_cancel_timed_out_calls_replace_blocked_hosts_and_grow_under_load(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "slow").mkdir(parents=True)
    (plugins / "slow" / "plugin.py").write_text(PLUGIN, encoding="utf-8")
    log = tmp_path / "server.log"

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            api = "/v1/plugins/slow/api"

            async def call(path, seconds):
                return await client.post(f"{api}{path}", json={"seconds": seconds}, timeout=60)

            first = await wait_for(lambda: call("/wait", 0), lambda response: response.status_code == 200, log=log)
            pid = first.json()["pid"]

            # A call past its time limit is cancelled alone; the other call on the same host completes.
            late, prompt = await asyncio.gather(call("/wait", 10), call("/wait", 0.5))
            assert (late.status_code, late.json()["error"]["code"]) == (504, "plugin_timeout"), late.text
            assert prompt.status_code == 200, prompt.text
            assert prompt.json()["pid"] == pid
            assert (await call("/wait", 0)).json()["pid"] == pid

            # A section whose plugin times out is left out; the rest of the context is still prepared.
            scope = await create_scope(client, "plugin-hosts-e2e")
            prepared = await client.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope,
                    "query": "anything",
                    "include_receipt": True,
                    "assembly": {"sections": [{"family": "slow-notes", "limit": 2}, {"family": "memory", "limit": 2}]},
                },
                timeout=60,
            )
            assert prepared.status_code == 200, prepared.text
            searches = prepared.json()["receipt"]["retrieval"]["searches"]
            assert any(item["family"] == "slow-notes" and item["outcome"] == "failed" for item in searches), searches

            # Blocking the event loop stops the heartbeat: the timed-out call gets 504, the others 503.
            blocked, waiting = await asyncio.gather(call("/block", 60), call("/hold", 20))
            assert (blocked.status_code, blocked.json()["error"]["code"]) == (504, "plugin_timeout"), blocked.text
            assert (waiting.status_code, waiting.json()["error"]["code"]) == (503, "plugin_unavailable"), waiting.text
            restarted = await wait_for(lambda: call("/wait", 0), lambda response: response.status_code == 200, log=log)
            assert restarted.json()["pid"] != pid

            # Sixteen held calls fill one host's eight slots, so a second host starts; the limit is two. The calls
            # stay held until both hosts have taken some, however long the second host takes to start.
            started, release = tmp_path / "started.txt", tmp_path / "release"
            gate = {"started": str(started), "release": str(release)}
            held = [asyncio.create_task(client.post(f"{api}/gate", json=gate, timeout=150)) for _ in range(16)]

            async def hosts():
                return set(started.read_text().split()) if started.exists() else set()

            await wait_for(hosts, lambda value: len(value) == 2, log=log)
            release.touch()
            responses = await asyncio.gather(*held)
            assert all(response.status_code == 200 for response in responses), [item.text for item in responses]
            assert len({response.json()["pid"] for response in responses}) == 2

    asyncio.run(scenario())


def test_plugin_hosts_keep_answering_while_plugin_code_fills_the_default_executor(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "slow").mkdir(parents=True)
    (plugins / "slow" / "plugin.py").write_text(PLUGIN, encoding="utf-8")
    log = tmp_path / "server.log"

    async def scenario():
        async with running_server(tmp_path, plugins) as client:
            api = "/v1/plugins/slow/api"

            async def call(path, seconds):
                return await client.post(f"{api}{path}", json={"seconds": seconds}, timeout=60)

            first = await wait_for(lambda: call("/wait", 0), lambda response: response.status_code == 200, log=log)
            pid = first.json()["pid"]
            crowded = await call("/crowd", 10)
            assert crowded.status_code == 200, crowded.text
            assert crowded.json()["pid"] == pid

            # Past the heartbeat timeout, the same host still answers within the route's time limit.
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 8
            while loop.time() < deadline:
                response = await call("/wait", 0)
                assert response.status_code == 200, response.text
                assert response.json()["pid"] == pid
                await asyncio.sleep(0.5)

    asyncio.run(scenario())
