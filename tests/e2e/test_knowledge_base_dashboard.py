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

"""Exercise the sandboxed Dashboard against a real Server and revisioned document APIs."""

import asyncio
import os
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, running_server, wait_for
from tests.e2e.test_knowledge_base import FIXTURE_EMBEDDING, PLUGIN, P


@asynccontextmanager
async def dashboard(tmp_path, extra_plugin=""):
    playwright = pytest.importorskip("playwright.async_api")
    plugins = tmp_path / "plugins"
    shutil.copytree(PLUGIN, plugins / "knowledge-base")
    entry = plugins / "knowledge-base" / "plugin.py"
    entry.write_text(entry.read_text() + FIXTURE_EMBEDDING + extra_plugin)
    async with (
        running_server(
            tmp_path,
            plugins,
            extra_env={
                "POWERCONTEXT_KB_DATA_DIR": str(tmp_path / "data"),
                "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
            },
        ) as client,
        playwright.async_playwright() as pw,
    ):
        executable = os.environ.get("POWERCONTEXT_TEST_CHROMIUM", pw.chromium.executable_path)
        if not await asyncio.to_thread(Path(executable).exists):
            pytest.skip("Install Chromium with playwright install chromium, or set POWERCONTEXT_TEST_CHROMIUM")

        async def call(method, path, **kwargs):
            response = await client.request(method, P + path, **kwargs)
            assert response.is_success, response.text
            return response.json()

        browser = await pw.chromium.launch(executable_path=executable)
        try:
            context = await browser.new_context(viewport={"width": 1440, "height": 1000})
            origin = str(client.base_url).rstrip("/")
            await context.add_cookies([
                {"name": "powercontext_dashboard_token", "value": ADMIN_AUTHORIZATION_VALUE, "url": origin}
            ])
            page = await context.new_page()
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            try:
                yield call, page, page.frame_locator("iframe"), origin, playwright.expect
                assert not errors, errors
            except BaseException:
                await page.screenshot(path=str(tmp_path / "dashboard.png"), full_page=True)
                print("Browser errors:", errors)
                print("Dashboard screenshot:", tmp_path / "dashboard.png")
                for frame in page.frames:
                    print("Frame:", frame.url, (await frame.locator("body").inner_text())[:2500])
                raise
        finally:
            await browser.close()


async def select_library(frame, title):
    await frame.get_by_role("button", name="切换知识库", exact=True).click()
    await frame.get_by_role("menuitem", name=title, exact=True).click()


async def library(call, title, space):
    return await call(
        "POST", "/knowledge-bases", json={"title": title, "space_scope_id": space, "idempotency_key": title}
    )


async def upload(call, tmp_path, kb, path, text):
    base = "/scopes/" + kb["scope_id"]
    receipt = await call(
        "POST", base + "/uploads", params={"relative_path": path, "idempotency_key": path}, content=text.encode()
    )
    await wait_for(
        lambda: call("GET", base + "/imports/" + receipt["request_id"]),
        lambda r: r["state"] == "completed",
        log=tmp_path / "server.log",
    )
    return receipt


def test_dashboard_navigation_reader_edits_and_historical_markdown(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            a, b = await library(call, "A", space), await library(call, "B", space)
            await upload(call, tmp_path, a, "a.md", "# A\n\nBody A")
            receipt = await upload(
                call,
                tmp_path,
                b,
                "b.md",
                "# B\n\n**Body B** [guide][g]\n\n<script>alert('unsafe')</script>\n\n"
                "![remote](https://example.invalid/image.png)\n\n[g]: https://example.com/guide",
            )
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await expect(frame.get_by_role("button", name="切换知识库")).to_be_enabled()
            await page.evaluate(
                """scope => {
                const original = window.fetch;
                window.delay = {held: false};
                window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (!window.delay.held && String(args[0]).includes('/scopes/' + scope + '/documents')) {
                        window.delay.held = true;
                        await new Promise(resolve => { window.delay.release = resolve; });
                    }
                    return response;
                };
            }""",
                a["scope_id"],
            )
            await select_library(frame, "A")
            await page.wait_for_function("window.delay.held")
            await select_library(frame, "B")
            await expect(frame.get_by_role("table")).to_contain_text("b.md")
            await page.evaluate("window.delay.release()")
            await page.wait_for_timeout(300)
            await expect(frame.get_by_role("table")).not_to_contain_text("a.md")
            await frame.get_by_role("button", name="处理记录", exact=True).click()
            await expect(frame.get_by_role("table").last).to_contain_text("已完成")
            await frame.get_by_role("button", name="浏览", exact=True).click()
            body = frame.locator(".markdown-body")
            await expect(body).to_contain_text("Body B")
            await expect(body.locator("strong")).to_have_text("Body B")
            await expect(body.get_by_role("link", name="guide")).to_have_attribute("href", "https://example.com/guide")
            await expect(body.locator("script, img")).to_have_count(0)
            for _ in range(4):
                await frame.locator(".chunk-link").first.click()
                await expect(body).to_have_count(1)
                await expect(body).to_contain_text("Body B")
            await frame.get_by_text("Markdown 源码", exact=True).click()
            await expect(frame.locator(".markdown-source")).to_contain_text("**Body B**")
            await frame.get_by_role("button", name="编辑分块", exact=True).click()
            await frame.get_by_role("textbox", name="分块正文").fill("Browser correction")
            async with page.expect_response(
                lambda r: "/artifacts/" + b["scope_id"] + "/" in r.url and r.request.method == "PUT"
            ) as saved:
                await frame.get_by_role("button", name="保存新版本").click()
            assert (await saved.value).status == 200
            await expect(body).to_have_text("Browser correction")
            docs = await call("GET", "/scopes/" + b["scope_id"] + "/documents")
            assert docs["items"][0]["ref"]["revision"] == 2
            await frame.get_by_role("combobox", name="文档版本").press("ArrowDown")
            await frame.get_by_text("v1 · 历史版本", exact=True).click()
            await expect(body).to_contain_text("Body B")
            await expect(frame.get_by_role("button", name="编辑分块", exact=True)).to_be_disabled()
            await frame.get_by_role("combobox", name="文档版本").press("ArrowDown")
            await frame.get_by_text("v2 · 当前版本", exact=True).last.click()
            await expect(body).to_have_text("Browser correction")
            # A concurrent write must not silently overwrite the user's pinned revision.
            await frame.get_by_role("button", name="编辑分块", exact=True).click()
            await frame.get_by_role("textbox", name="分块正文").fill("Unsaved correction")
            document = receipt["document_id"]
            chunks = await call("GET", f"/scopes/{b['scope_id']}/documents/{document}/revisions/2/chunks")
            endpoint = f"/v1/scopes/{b['scope_id']}/artifacts/document/{document}"
            response = await page.context.request.put(
                origin + endpoint,
                headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}", "If-Match": '"revision:2"'},
                data={
                    "content": {
                        "operation": "edit_chunk",
                        "chunk_id": chunks["items"][0]["chunk_id"],
                        "text": "Concurrent correction",
                    }
                },
            )
            assert response.status == 200, await response.text()
            await frame.get_by_role("button", name="保存新版本").click()
            await expect(frame.get_by_role("textbox", name="分块正文")).to_have_value("Unsaved correction")
            await expect(frame.get_by_role("alert").first).to_contain_text("412")

    asyncio.run(scenario())


@pytest.mark.parametrize("selection", ["same", "another"])
def test_dashboard_keeps_saved_settings_when_reselecting_a_library(tmp_path, selection):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            for title in ("Settings A", "Settings B"):
                await library(call, title, space)
            await page.goto(origin + "/dashboard/plugins/knowledge-base")

            async def select(title):
                await select_library(frame, title)
                await frame.get_by_role("tab", name="设置", exact=True).click()

            async def configure(expected, maximum):
                await expect(frame.get_by_role("spinbutton", name="最大分块字符数")).to_have_value(str(expected))
                await frame.get_by_role("spinbutton", name="最大分块字符数").fill(str(maximum))
                async with page.expect_response(
                    lambda r: r.request.method == "PATCH" and r.url.endswith("/settings")
                ) as saved:
                    await frame.get_by_role("button", name="保存配置").click()
                assert (await saved.value).status == 200
                await expect(frame.get_by_text("配置已保存", exact=True)).to_be_visible()

            await select("Settings A")
            await configure(360, 300)
            if selection == "another":
                await select("Settings B")
            await select("Settings A")
            await configure(300, 280)
            rows = (await call("GET", "/knowledge-bases", params={"space_scope_id": space}))["items"]
            saved = next(row for row in rows if row["value"]["title"] == "Settings A")
            assert saved["version"] == 3
            assert saved["value"]["chunking"] == {"max_chars": 280, "overlap": 40}
            assert next(row for row in rows if row["value"]["title"] == "Settings B")["version"] == 1

    asyncio.run(scenario())


def test_dashboard_space_switch_and_upload_preserve_target(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            a = (await call("GET", "/me/default-space"))["space_scope_id"]
            b = (await call("POST", "/spaces", json={"title": "Space B", "idempotency_key": "B"}))["space_scope_id"]
            old, new = await library(call, "Base A", a), await library(call, "Base B", b)
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await frame.get_by_role("button", name="切换知识库").click()
            old_button = frame.get_by_role("menuitem", name="Base A", exact=True)
            await old_button.evaluate("button => { window.oldLibraryButton = button; }")
            await old_button.click()
            await page.evaluate(
                """space => {
                const original = window.fetch;
                window.delay = {held: false};
                window.fetch = async (...args) => {
                    const response = await original(...args);
                    const url = new URL(String(args[0]));
                    if (!window.delay.held && url.pathname.endsWith('/knowledge-bases') && url.searchParams.get('space_scope_id') === space) {
                        window.delay.held = true;
                        await new Promise(resolve => { window.delay.release = resolve; });
                    }
                    return response;
                };
            }""",
                b,
            )
            await frame.get_by_role("button", name="切换空间").click()
            await frame.get_by_role("menuitem", name="Space B", exact=True).click()
            await page.wait_for_function("window.delay.held")
            await expect(old_button).to_have_count(0)
            await frame.locator("body").evaluate("() => window.oldLibraryButton.click()")
            await expect(frame.get_by_role("button", name="导入文档", exact=True)).to_have_count(0)
            await page.evaluate("window.delay.release()")
            await select_library(frame, "Base B")
            await frame.get_by_role("button", name="导入文档", exact=True).click()
            await frame.locator('input[type="file"]').set_input_files([
                {
                    "name": "selected-space.md",
                    "mimeType": "text/markdown",
                    "buffer": b"Uploaded into the selected space",
                },
                {"name": "invalid.txt", "mimeType": "text/plain", "buffer": b"unsupported"},
            ])
            await frame.get_by_role("button", name="开始导入", exact=True).click()
            await expect(frame.get_by_role("alert")).to_contain_text("已接受 1 / 2 个文件")
            await frame.get_by_role("button", name="完成", exact=True).click()
            docs = await wait_for(
                lambda: call("GET", "/scopes/" + new["scope_id"] + "/documents"),
                lambda d: bool(d["items"]),
                log=tmp_path / "server.log",
            )
            assert docs["items"][0]["content"]["relative_path"] == "selected-space.md"
            untouched = await call("GET", "/scopes/" + old["scope_id"] + "/documents")
            assert not untouched["items"] and not untouched["requests"]
            async with page.expect_response(
                lambda response: response.url.endswith("/api/me/default-space") and response.request.method == "PUT"
            ) as changed_default:
                await frame.get_by_role("button", name="设为默认空间", exact=True).click()
            assert (await changed_default.value).status == 200
            await expect(frame.get_by_role("button", name="设为默认空间", exact=True)).to_be_disabled()
            await page.reload()
            await select_library(frame, "Base B")
            default = await call("GET", "/me/default-space")
            assert default["space_scope_id"] == b
            await page.route(
                "**/api/me/default-space",
                lambda route: route.fulfill(json={**default, "space_scope_id": "revoked", "available": False}),
            )
            await page.reload()
            await expect(frame.get_by_role("alert")).to_contain_text("默认空间不可访问")
            await expect(frame.get_by_role("button", name="切换知识库")).to_have_count(0)
            await frame.get_by_role("button", name="切换空间").click()
            await frame.get_by_role("menuitem", name="Space B", exact=True).click()
            await select_library(frame, "Base B")

    asyncio.run(scenario())


def test_dashboard_cross_library_retrieval_chunk_removal_and_forget(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            a, b = await library(call, "Search A", space), await library(call, "Search B", space)
            await upload(
                call, tmp_path, a, "search-a.md", "# Checklist\n\nquartz alpha\n\n# Followup\n\nsecond section"
            )
            await upload(call, tmp_path, b, "search-b.md", "# Beta\n\nquartz beta")
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await select_library(frame, "Search A")
            await frame.get_by_role("tab", name="检索测试", exact=True).click()
            await frame.get_by_role("combobox", name="检索知识库").press("ArrowDown")
            await frame.get_by_text("Search B", exact=True).last.click()
            await frame.get_by_role("combobox", name="检索知识库").press("Escape")
            await frame.get_by_role("textbox", name="检索问题").fill("quartz")
            async with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/retrieve")
            ) as found:
                await frame.get_by_role("button", name="检索", exact=True).click()
            response = await found.value
            assert response.status == 200, await response.text()
            assert set(response.request.post_data_json["scope_ids"]) == {a["scope_id"], b["scope_id"]}
            hits = (await response.json())["hits"]
            assert {hit["scope_id"] for hit in hits} == {a["scope_id"], b["scope_id"]}
            await expect(frame.locator(".markdown-body")).to_have_count(len(hits))
            await frame.get_by_role("button", name="定位分块", exact=True).first.click()
            await expect(frame.locator(".reader-content .markdown-body")).to_have_count(1)
            await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("quartz")
            await frame.get_by_role("button", name="分块更多操作").click()
            await frame.get_by_role("menuitem", name="移除分块", exact=True).click()
            await frame.get_by_role("button", name="确认", exact=True).click()
            await expect(frame.get_by_role("combobox", name="文档版本")).to_be_visible()
            await expect(frame.get_by_text("v2 · 当前版本", exact=True)).to_be_visible()
            await frame.get_by_role("button", name="文档更多操作").click()
            await frame.get_by_role("menuitem", name="遗忘文档", exact=True).click()
            await frame.get_by_role("button", name="确认", exact=True).click()
            await expect(frame.get_by_role("alert")).to_contain_text("文档已遗忘")
            await frame.get_by_role("button", name="back", exact=True).click()
            await expect(frame.get_by_role("textbox", name="检索问题")).to_have_value("quartz")
            await expect(frame.locator(".markdown-body")).to_have_count(0)
            await expect(frame.get_by_role("alert")).to_contain_text("请重新检索")
            docs_a = (await call("GET", "/scopes/" + a["scope_id"] + "/documents"))["items"]
            docs_b = (await call("GET", "/scopes/" + b["scope_id"] + "/documents"))["items"]
            forgotten = [doc for doc in docs_a + docs_b if doc["lifecycle_state"] == "deprecated"]
            assert len(forgotten) == 1 and forgotten[0]["ref"]["revision"] == 2
            result = await call(
                "POST",
                "/retrieve",
                json={
                    "space_scope_id": space,
                    "scope_ids": [a["scope_id"], b["scope_id"]],
                    "query": "quartz",
                    "mode": "fts",
                },
            )
            assert all(hit["document"]["artifact_id"] != forgotten[0]["ref"]["artifact_id"] for hit in result["hits"])

    asyncio.run(scenario())
