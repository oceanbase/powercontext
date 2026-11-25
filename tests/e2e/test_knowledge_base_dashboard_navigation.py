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

"""The plugin follows host Scope navigation and exposes explicit processing retries."""

import asyncio

from tests.e2e.plugin_server import wait_for
from tests.e2e.test_knowledge_base_dashboard import dashboard, library, select_library, upload


def test_dashboard_keeps_plugin_navigation_separate_from_host_scope_picker(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            default = await call("GET", "/me/default-space")
            other = await call("POST", "/spaces", json={"title": "Host selection", "idempotency_key": "other"})
            a = await library(call, "Host A", default["space_scope_id"])
            b = await library(call, "Host B", other["space_scope_id"])
            await upload(call, tmp_path, a, "same.md", "# A\n\nOriginal A")
            await upload(call, tmp_path, b, "same.md", "# B\n\nOriginal B")
            await page.goto(origin + "/dashboard/plugins/knowledge-base?scope=" + a["scope_id"])
            await expect(frame.get_by_role("button", name="切换知识库")).to_contain_text("Host A")
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await frame.get_by_role("checkbox", name="选择分块 1", exact=True).check()
            await frame.get_by_role("button", name="编辑分块", exact=True).click()
            await frame.get_by_role("textbox", name="分块正文").fill("Unsaved draft for A")
            # Plugin Scopes are reachable by explicit URL, but are not ordinary host picker options.
            options = await page.request.get(origin + "/dashboard/scope-options?query=Host")
            assert options.ok
            assert not {a["scope_id"], b["scope_id"], other["space_scope_id"]} & {
                item["scope_id"] for item in (await options.json())["items"]
            }
            # Leaving the explicit Scope through the host closes the old draft before plugin navigation.
            await page.locator(".scope-picker .ts-control").click()
            await page.locator('.scope-picker .option[data-value=""]').click()
            # The editor can disappear while HTMX is still replacing the iframe. Wait for
            # the new host context before navigating inside the plugin's replacement frame.
            await expect(frame.get_by_role("button", name="切换知识库")).to_contain_text("选择知识库")
            await expect(frame.get_by_role("textbox", name="分块正文")).to_have_count(0)
            await frame.get_by_role("button", name="切换空间").click()
            await frame.get_by_role("menuitem", name="Host selection", exact=True).click()
            await expect(frame.get_by_role("button", name="切换空间")).to_contain_text("Host selection")
            await select_library(frame, "Host B")
            await expect(frame.get_by_role("button", name="切换知识库")).to_contain_text("Host B")
            await expect(frame.get_by_role("textbox", name="分块正文")).to_have_count(0)
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("Original B")
            await expect(frame.get_by_role("checkbox", name="选择分块 1", exact=True)).not_to_be_checked()
            assert await call("GET", "/me/default-space") == default
            # An ordinary host Scope must not silently masquerade as a selected knowledge base.
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await expect(frame.get_by_text("当前内容范围未关联可访问的知识库。", exact=False)).to_be_visible()
            await expect(frame.get_by_role("button", name="切换知识库")).to_contain_text("选择知识库")

    asyncio.run(scenario())


def test_dashboard_retry_can_explicitly_replace_edits_and_names_failed_files(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Retry library", space)
            receipt = await upload(call, tmp_path, kb, "same.md", "# Original\n\nOriginal text")
            base = "/scopes/" + kb["scope_id"]

            async def terminal(request):
                return await wait_for(
                    lambda: call("GET", base + "/imports/" + request["request_id"]),
                    lambda r: r["state"] in {"completed", "failed"},
                    log=tmp_path / "server.log",
                )

            broken = await call(
                "POST",
                base + "/uploads",
                params={"relative_path": "broken.md", "idempotency_key": "broken"},
                content=b"\xff",
            )
            failed = await terminal(broken)
            assert failed["state"] == "failed" and failed["relative_path"] == "broken.md"
            await page.goto(origin + "/dashboard/plugins/knowledge-base?scope=" + kb["scope_id"])
            await frame.get_by_role("button", name="处理记录", exact=False).click()
            await expect(frame.get_by_role("row").filter(has_text="broken.md")).to_contain_text("解析失败")
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await frame.get_by_role("button", name="编辑分块", exact=True).click()
            await frame.get_by_role("textbox", name="分块正文").fill("Manually edited text")
            await frame.get_by_role("button", name="保存新版本", exact=True).click()
            await expect(frame.get_by_text("v2 · 当前版本", exact=True)).to_be_visible()
            await frame.get_by_role("button", name="back", exact=True).click()
            await frame.get_by_role("button", name="导入文档", exact=True).click()
            await frame.locator("input[type=file]").set_input_files({
                "name": "same.md",
                "mimeType": "text/markdown",
                "buffer": b"# New source\n\nNew uploaded content",
            })
            async with page.expect_response(lambda r: r.request.method == "POST" and "/uploads?" in r.url) as response:
                await frame.get_by_role("button", name="开始导入", exact=True).click()
            imported = await (await response.value).json()
            await frame.get_by_role("button", name="完成", exact=True).click()
            assert (await terminal(imported))["state"] == "failed"
            await frame.get_by_role("button", name="刷新文档", exact=True).click()
            # The processing table may have been remounted when leaving the reader.
            if not await frame.get_by_text("近期处理记录", exact=True).is_visible():
                await frame.get_by_role("button", name="处理记录", exact=False).click()
            for replace in (False, True):
                row = frame.get_by_role("row").filter(has_text="Reprocessing would replace manual edits").first
                await row.get_by_role("button", name="重新提交解析", exact=True).click()
                choice = frame.get_by_role("checkbox", name="允许替换人工修改", exact=True)
                await expect(choice).not_to_be_checked()
                if replace:
                    await choice.check()
                async with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/retry")
                ) as response:
                    await frame.get_by_role("button", name="确认重新解析", exact=True).click()
                accepted = await (await response.value).json()
                assert accepted["request_id"] != imported["request_id"]
                state = await terminal(accepted)
                assert state["state"] == ("completed" if replace else "failed")
                await frame.get_by_role("button", name="刷新文档", exact=True).click()
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await expect(frame.get_by_text("v3 · 当前版本", exact=True)).to_be_visible()
            await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("New uploaded content")
            history = await call("GET", base + "/documents/" + receipt["document_id"] + "/revisions/2/chunks")
            old = await call(
                "GET",
                base
                + "/documents/"
                + receipt["document_id"]
                + "/revisions/2/chunks/"
                + history["items"][0]["chunk_id"],
            )
            assert old["text"] == "Manually edited text"

    asyncio.run(scenario())


def test_dashboard_top_navigation_creates_in_selected_space_and_clears_reader(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            default = await call("GET", "/me/default-space")
            a = await library(call, "Original library", default["space_scope_id"])
            await upload(call, tmp_path, a, "original.md", "# Original\n\nOriginal library content")
            b = await call("POST", "/spaces", json={"title": "Empty space", "idempotency_key": "empty"})
            await page.goto(origin + "/dashboard/plugins/knowledge-base?scope=" + a["scope_id"])
            await expect(frame.get_by_role("button", name="切换知识库")).to_contain_text("Original library")
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("Original library content")
            await frame.get_by_role("button", name="切换空间").click()
            await frame.get_by_role("menuitem", name="Empty space", exact=True).click()
            await expect(frame.locator(".reader-content .markdown-body")).to_have_count(0)
            await expect(frame.get_by_role("button", name="切换知识库")).to_be_disabled()
            await expect(frame.get_by_text("此空间还没有知识库", exact=False)).to_be_visible()
            await frame.get_by_role("button", name="新建知识库", exact=True).click()
            await frame.get_by_role("textbox", name="知识库名称", exact=True).fill("Created in empty space")
            async with page.expect_response(
                lambda r: r.request.method == "POST" and r.url.endswith("/knowledge-bases")
            ) as created:
                await frame.get_by_role("button", name="创建", exact=True).click()
            response = await created.value
            assert response.status == 200
            new_library = await response.json()
            assert new_library["space_scope_id"] == b["space_scope_id"]
            await expect(frame.get_by_role("dialog")).to_have_count(0)
            await select_library(frame, "Created in empty space")
            await expect(frame.get_by_role("tab", name="文档", exact=True)).to_be_visible()
            await expect(frame.get_by_role("button", name="导入文档", exact=True)).to_be_visible()
            await expect(frame.get_by_role("heading", name="Created in empty space", exact=True)).to_have_count(0)
            original_libraries = await call(
                "GET", "/knowledge-bases", params={"space_scope_id": default["space_scope_id"]}
            )
            assert [item["scope_id"] for item in original_libraries["items"]] == [a["scope_id"]]
            assert await call("GET", "/me/default-space") == default

    asyncio.run(scenario())


def test_dashboard_long_document_names_stay_compact_and_remain_accessible(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Long names", space)
            name = "documentation-" + "a" * 90 + ".md"
            path = "guides/" + name
            await upload(call, tmp_path, kb, path, "# Guide\n\nLong filename content")
            await upload(call, tmp_path, kb, "short.md", "# Short\n\nShort filename content")
            await page.goto(origin + "/dashboard/plugins/knowledge-base?scope=" + kb["scope_id"])
            for width in (1024, 1440):
                await page.set_viewport_size({"width": width, "height": 1000})
                link = frame.get_by_role("button", name=name, exact=True)
                await expect(link).to_be_visible()
                label = link.get_by_text(name, exact=True)
                # Verify rendered height, not the choice of CSS truncation mechanism.
                metrics = await label.evaluate(
                    """e => {
                        const box = e.getBoundingClientRect(), cell = e.closest('td').getBoundingClientRect();
                        return {height:box.height, line:parseFloat(getComputedStyle(e).lineHeight),
                                right:box.right, cellRight:cell.right, fullHeight:e.scrollHeight};
                    }"""
                )
                assert 0 < metrics["height"] <= metrics["line"] * 2 + 1, metrics
                assert metrics["right"] <= metrics["cellRight"], metrics
                if width == 1024:
                    assert metrics["fullHeight"] > metrics["height"], metrics
                await link.hover()
                await expect(frame.get_by_role("tooltip")).to_have_text(name)
                await frame.get_by_role("checkbox", name="选择文档 " + path, exact=True).check()
                await expect(frame.get_by_text("已选择 1 篇文档", exact=True)).to_be_visible()
                await frame.get_by_role("button", name="批量重新解析", exact=True).click()
                await expect(frame.get_by_role("dialog")).to_contain_text(path)
                await frame.get_by_role("dialog").get_by_role("button", name="取消", exact=True).click()
                await expect(frame.get_by_role("dialog")).not_to_be_visible()
                await frame.get_by_role("button", name="清空选择", exact=True).click()
                await link.focus()
                await expect(frame.get_by_role("tooltip")).to_have_text(name)
                await link.press("Enter")
                await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("Long filename content")
                await expect(frame.get_by_role("tooltip")).to_have_count(0)
                await frame.get_by_role("button", name="back", exact=True).click()
                await frame.get_by_role("button", name="short.md", exact=True).click()
                await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("Short filename content")
                await frame.get_by_role("button", name="back", exact=True).click()

    asyncio.run(scenario())
