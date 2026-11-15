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

"""A plugin's Dashboard page is sandboxed and reaches only its own routes through the session."""

import asyncio
import json
import re
import shutil
import subprocess

import httpx
import pytest

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, create_scope, running_server

PLUGIN = """
from pydantic import BaseModel

from powercontext.plugins import ApiRequest, ArtifactPlugin, ChatMessage, PluginApi, PluginPages, Rendering, Route


class Echo(BaseModel):
    text: str


class Demo:
    def __init__(self, ctx):
        self.ctx = ctx

    async def render_artifact(self, request):
        return Rendering(text=request.artifact.content.get("text", ""))

    async def create_artifact(self, request, writer):
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content)

    async def update_artifact(self, request, writer):
        async with writer.transaction() as tx:
            await tx.put(request.artifact_id, content=request.content)

    async def forget_artifact(self, request, writer):
        async with writer.transaction() as tx:
            await tx.set_lifecycle(request.artifact_id, "deprecated")

    async def echo(self, request: ApiRequest):
        return {"text": request.body.text}

    async def upload(self, request: ApiRequest):
        return {"size": len(request.body)}

    async def complete(self, request: ApiRequest):
        return {"answer": await self.ctx.models.generate([ChatMessage(role="user", content="hello")])}


async def setup(ctx):
    return Demo(ctx)


plugin = ArtifactPlugin(
    family="demo-notes",
    setup=setup,
    api=PluginApi(
        routes=(
            Route(method="POST", path="/echo", handler="echo", access="authenticated", body="json", request_model=Echo),
            Route(
                method="POST",
                path="/upload",
                handler="upload",
                access="authenticated",
                body="binary",
                max_body_bytes=8 * 1024 * 1024,
            ),
            Route(method="POST", path="/complete", handler="complete", access="authenticated"),
        )
    ),
    pages=PluginPages(title={"en": "Demo notes", "zh": "演示"}),
)
"""
PAGE = '<!doctype html><title>Demo</title><script src="/dashboard/plugins/_client.js"></script>'


def test_plugin_page_is_sandboxed_and_forwards_only_same_origin_session_requests(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "demo" / "ui").mkdir(parents=True)
    (plugins / "demo" / "plugin.py").write_text(PLUGIN, encoding="utf-8")
    (plugins / "demo" / "ui" / "index.html").write_text(PAGE, encoding="utf-8")
    pages = "/dashboard/plugins/demo"
    environment = {
        "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
        # The deployment ceiling caps the upload route's own 8 MiB limit.
        "POWERCONTEXT_SERVER_PLUGIN_API_MAX_BODY_BYTES": "4096",
        # pydantic-ai's built-in test model answers every request without a provider.
        "POWERCONTEXT_SERVER_INFERENCE": json.dumps({"generation_model": "test"}),
    }

    async def scenario():
        async with running_server(tmp_path, plugins, extra_env=environment) as client:
            async with httpx.AsyncClient(base_url=client.base_url, timeout=20) as anonymous:
                asset = await anonymous.get(f"{pages}/ui/index.html")
                assert asset.status_code == 200, asset.text
                policy = asset.headers["content-security-policy"]
                assert policy.startswith("sandbox allow-scripts") and "allow-same-origin" not in policy
                # Only page assets are served: never plugin code, never outside the pages directory.
                for path in ("plugin.py", "../plugin.py", "%2e%2e/plugin.py", "index.html/../../plugin.py"):
                    leaked = await anonymous.get(f"{pages}/ui/{path}")
                    assert leaked.status_code in {401, 404} and b"import" not in leaked.content, path
                assert (await anonymous.get("/dashboard/plugins/_client.js")).status_code == 200
                assert (await anonymous.get(pages)).status_code == 401
                forwarded = await anonymous.post(
                    f"{pages}/api/echo", json={"text": "x"}, headers={"Sec-Fetch-Site": "same-origin"}
                )
                assert forwarded.status_code == 401
                unauthenticated = await anonymous.get(
                    f"{pages}/artifacts/any-scope", headers={"Sec-Fetch-Site": "same-origin"}
                )
                assert unauthenticated.status_code == 401

            session = {"powercontext_dashboard_token": ADMIN_AUTHORIZATION_VALUE}
            async with httpx.AsyncClient(base_url=client.base_url, cookies=session, timeout=20) as browser:
                page = await browser.get(pages, params={"lang": "en"})
                assert page.status_code == 200, page.text
                assert 'sandbox="allow-scripts' in page.text and "Demo notes" in page.text
                assert f'href="{pages}?' in (await browser.get("/dashboard/home")).text

                same_origin = {"Sec-Fetch-Site": "same-origin"}
                echoed = await browser.post(f"{pages}/api/echo", json={"text": "hello"}, headers=same_origin)
                assert echoed.status_code == 200, echoed.text
                assert echoed.json() == {"text": "hello"}
                # The framed page is cross-site with an opaque origin; neither form is forwarded.
                for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}, {}):
                    rejected = await browser.post(f"{pages}/api/echo", json={"text": "x"}, headers=headers)
                    assert rejected.status_code == 403

                large = await browser.post(f"{pages}/api/upload", content=b"x" * 5000, headers=same_origin)
                assert large.status_code == 413, large.text
                small = await browser.post(f"{pages}/api/upload", content=b"x" * 3000, headers=same_origin)
                assert small.json() == {"size": 3000}

                # The page also reaches the generic Artifact API, always for its plugin's own family.
                scope = await create_scope(client, "plugin-page-artifacts")
                notes = f"{pages}/artifacts/{scope}"
                created = await browser.post(
                    notes, json={"family": "experience", "content": {"text": "draft"}}, headers=same_origin
                )
                assert created.status_code == 201, created.text
                assert created.json()["family"] == "demo-notes" and created.headers["location"]
                note = f"{notes}/{created.json()['artifact_id']}"
                read = await browser.get(note, headers=same_origin)
                assert read.status_code == 200 and read.json()["content"] == {"text": "draft"}, read.text
                listed = await browser.get(notes, headers=same_origin)
                assert [item["artifact_id"] for item in listed.json()["items"]] == [created.json()["artifact_id"]]
                edit = {"content": {"text": "final"}}
                edited = await browser.put(note, json=edit, headers={**same_origin, "If-Match": read.headers["etag"]})
                assert edited.status_code == 200 and edited.json()["revision"] == 2, edited.text
                stale = await browser.put(note, json=edit, headers={**same_origin, "If-Match": read.headers["etag"]})
                assert stale.status_code == 412, stale.text
                state = (await browser.get(f"{note}/governance", headers=same_origin)).json()
                forgotten = await browser.post(
                    f"{note}/forget", json={"revision": 2, "state_version": state["state_version"]}, headers=same_origin
                )
                assert forgotten.status_code == 200, forgotten.text
                state = (await browser.get(f"{note}/governance", headers=same_origin)).json()
                assert state["lifecycle_state"] == "deprecated"
                # The same session and origin rules apply, and the path stays below the family.
                for headers in ({"Sec-Fetch-Site": "cross-site"}, {"Origin": "null"}, {}):
                    assert (await browser.get(note, headers=headers)).status_code == 403
                assert (await browser.get(f"{notes}/%2e%2e/experience", headers=same_origin)).status_code == 404

                # Repeated query parameters reach the public API as sent, so tag filters match as they do there.
                tagged = {}
                for name, tags in (("both", ["a", "b"]), ("only-a", ["a"]), ("only-b", ["b"])):
                    made = await browser.post(notes, json={"content": {"text": name}}, headers=same_origin)
                    assert made.status_code == 201, made.text
                    tagged[made.json()["artifact_id"]] = name
                    labels = f"{notes}/{made.json()['artifact_id']}/tags"
                    etag = (await browser.get(labels, headers=same_origin)).headers["etag"]
                    labelled = await browser.put(labels, json={"tags": tags}, headers={**same_origin, "If-Match": etag})
                    assert labelled.status_code == 200, labelled.text
                public = f"/v1/scopes/{scope}/artifacts/demo-notes"
                for match, expected in (("all", {"both"}), ("any", {"both", "only-a", "only-b"})):
                    query = (("tag", "a"), ("tag", "b"), ("tag_match", match))
                    forwarded = await browser.get(notes, params=query, headers=same_origin)
                    assert forwarded.status_code == 200, forwarded.text
                    assert {tagged[item["artifact_id"]] for item in forwarded.json()["items"]} == expected, match
                    assert forwarded.json() == (await client.get(public, params=query)).json()
                # A repeated single-valued parameter is the public API's to judge, not silently reduced to one value.
                repeated = (("limit", "1"), ("limit", "2"))
                direct = await client.get(public, params=repeated)
                assert (
                    await browser.get(notes, params=repeated, headers=same_origin)
                ).status_code == direct.status_code

            # Plugins call the Server's generation model without seeing its address or credentials.
            completed = await client.post("/v1/plugins/demo/api/complete")
            assert completed.status_code == 200, completed.text
            assert completed.json()["answer"]

    asyncio.run(scenario())


# Runs the host page's own script against a stub page: delivers the frame's messages and records what it fetches.
HOST_PAGE_HARNESS = """
const fs = require('node:fs');
const [script, messages] = [fs.readFileSync(process.argv[2], 'utf8'), JSON.parse(process.argv[3])];
const replies = [], fetched = [];
const frame = {
  contentWindow: { postMessage: message => replies.push(message) },
  isConnected: true,
  parentElement: { removeAttribute() {} },
  addEventListener() {},
};
let listener;
globalThis.me = selector =>
  selector === undefined ? { run: body => body({}) } : selector === 'iframe' ? frame : { remove() {}, hidden: true };
globalThis.document = { documentElement: { getAttribute: () => 'light' } };
globalThis.location = new URL('http://dashboard.test/dashboard/plugins/demo');
globalThis.window = { addEventListener: (type, handler) => { listener = handler; }, removeEventListener() {} };
globalThis.fetch = async (url, init) => {
  fetched.push([init.method, String(url)]);
  return { status: 204, headers: { get: () => null } };
};
new Function(script)();
(async () => {
  for (const data of messages) await listener({ source: frame.contentWindow, data });
  console.log(JSON.stringify({ fetched, replies }));
})();
"""


def test_host_page_forwards_only_destinations_inside_its_plugin(tmp_path):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to run the host page script")
    plugins = tmp_path / "plugins"
    (plugins / "demo" / "ui").mkdir(parents=True)
    (plugins / "demo" / "plugin.py").write_text(PLUGIN, encoding="utf-8")
    (plugins / "demo" / "ui" / "index.html").write_text(PAGE, encoding="utf-8")

    async def host_page() -> str:
        environment = {"POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true"}
        async with running_server(tmp_path, plugins, extra_env=environment) as client:
            session = {"powercontext_dashboard_token": ADMIN_AUTHORIZATION_VALUE}
            async with httpx.AsyncClient(base_url=client.base_url, cookies=session, timeout=20) as browser:
                page = await browser.get("/dashboard/plugins/demo", params={"lang": "en"})
                assert page.status_code == 200, page.text
                return page.text

    (script,) = re.findall(r"<script>\s*(me\(\)\.run\(section => \{.*?)</script>", asyncio.run(host_page()), re.S)
    (tmp_path / "host-page.js").write_text(script, encoding="utf-8")
    (tmp_path / "harness.js").write_text(HOST_PAGE_HARNESS, encoding="utf-8")

    def request(identity, **fields):
        return {"type": "powercontext.request", "id": identity, **fields}

    def artifacts(identity, path, scope="scope-a", method="GET"):
        return request(identity, target="artifacts", method=method, path=path, scopeId=scope)

    escapes = (
        # The URL parser resolves encoded dot segments, here into another plugin's forwarding endpoint.
        artifacts(10, "/%2e%2e/%2e%2e/%2e%2e/other/artifacts/scope-a"),
        artifacts(11, "/%2E%2E/%2e./.%2e/other/api/echo"),
        artifacts(12, "/../../other/artifacts/scope-a"),
        artifacts(13, "/note", scope=".."),
        artifacts(14, "/note", method="DELETE"),
        request(15, method="POST", path="/%2e%2e/%2e%2e/other/api/echo"),
        request(16, method="POST", path="/undeclared"),
    )
    allowed = (
        artifacts(1, ""),
        artifacts(2, "/note-1/forget", method="POST"),
        request(3, method="POST", path="/echo", json={"text": "x"}),
    )
    completed = subprocess.run(
        [node, str(tmp_path / "harness.js"), str(tmp_path / "host-page.js"), json.dumps([*escapes, *allowed])],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    base = "http://dashboard.test/dashboard/plugins/demo"
    assert result["fetched"] == [
        ["GET", f"{base}/artifacts/scope-a"],
        ["POST", f"{base}/artifacts/scope-a/note-1/forget"],
        ["POST", f"{base}/api/echo"],
    ]
    refused = {reply["id"]: reply.get("error") for reply in result["replies"] if reply.get("status") == 404}
    assert refused == {message["id"]: "route_not_declared" for message in escapes}
