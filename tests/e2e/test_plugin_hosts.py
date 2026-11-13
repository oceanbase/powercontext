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


class Slow:
    async def render_artifact(self, request):
        return Rendering(text="")

    async def search_artifacts(self, request):
        await asyncio.sleep(30)
        return SearchResult(hits=[])

    async def wait(self, request: ApiRequest):
        await asyncio.sleep(request.body.seconds)
        return {"pid": os.getpid()}

    async def block(self, request: ApiRequest):
        time.sleep(request.body.seconds)
        return {"pid": os.getpid()}


async def setup(ctx):
    return Slow()


def route(path, handler, timeout):
    return Route(
        method="POST",
        path=path,
        handler=handler,
        access="authenticated",
        body="json",
        request_model=Wait,
        timeout_seconds=timeout,
    )


plugin = ArtifactPlugin(
    family="slow-notes",
    setup=setup,
    change_timeout_seconds=1,
    host_processes=2,
    api=PluginApi(routes=(route("/wait", "wait", 3), route("/hold", "wait", 30), route("/block", "block", 1))),
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

            # Sixteen long calls fill one host's eight slots, so a second host starts; the limit is two.
            held = await asyncio.gather(*(call("/hold", 8) for _ in range(16)))
            assert all(response.status_code == 200 for response in held), [response.text for response in held]
            assert len({response.json()["pid"] for response in held}) == 2

    asyncio.run(scenario())
