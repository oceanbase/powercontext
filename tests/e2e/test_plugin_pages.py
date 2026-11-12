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

import httpx

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server

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

            # Plugins call the Server's generation model without seeing its address or credentials.
            completed = await client.post("/v1/plugins/demo/api/complete")
            assert completed.status_code == 200, completed.text
            assert completed.json()["answer"]

    asyncio.run(scenario())
