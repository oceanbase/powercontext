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

"""Publication and bulk editing must keep the reader and retrieval context consistent."""

import asyncio

import pytest

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, wait_for
from tests.e2e.test_knowledge_base_dashboard import dashboard, library, select_library, upload


@pytest.mark.parametrize(("method", "other_scope"), [("upload", False), ("local", False), ("upload", True)])
def test_dashboard_import_completion_invalidates_retrieval_after_tab_navigation(tmp_path, method, other_scope):
    root = tmp_path / "documents"
    root.mkdir()
    original = "# Quartz\n\nOriginal instructions before import"
    updated = "# Quartz\n\nUpdated instructions after import"
    (root / "same.md").write_text(original)
    hold = tmp_path / "hold"
    gate = f"""
from pathlib import Path
import time
_original_parse = _service.parse
def _gated_parse(*args, **kwargs):
    while Path({str(hold)!r}).exists():
        time.sleep(0.05)
    return _original_parse(*args, **kwargs)
_service.parse = _gated_parse
"""

    async def scenario():
        async with dashboard(tmp_path, gate, extra_env={"POWERCONTEXT_KB_IMPORT_ROOTS": str(root)}) as (
            call,
            page,
            frame,
            origin,
            expect,
        ):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Import publication", space)
            scope = kb["scope_id"]
            if other_scope:
                other = await library(call, "Other library", space)
                await upload(call, tmp_path, other, "other.md", "# Quartz\n\nIndependent instructions")
            if method == "upload":
                await upload(call, tmp_path, kb, "same.md", original)
            else:
                initial = await call(
                    "POST",
                    f"/scopes/{scope}/imports",
                    json={"root": str(root), "paths": ["same.md"], "idempotency_key": "original"},
                )
                await wait_for(
                    lambda: call("GET", f"/scopes/{scope}/imports/{initial['items'][0]['request_id']}"),
                    lambda result: result["state"] == "completed",
                    log=tmp_path / "server.log",
                )
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await select_library(frame, "Import publication")
            await frame.get_by_role("button", name="导入文档", exact=True).click()
            if method == "upload":
                await frame.locator('input[type="file"]').set_input_files([
                    {"name": "same.md", "mimeType": "text/markdown", "buffer": updated.encode()},
                    {"name": "invalid.txt", "mimeType": "text/plain", "buffer": b"unsupported"},
                ])
            else:
                (root / "same.md").write_text(updated)
                await frame.get_by_text("Server 本地目录", exact=True).click()
                await frame.get_by_role("textbox", name="Server 目录").fill(str(root))
                await frame.get_by_role("textbox", name="相对文件路径").fill("same.md\nmissing.md")
            hold.touch()
            try:
                async with page.expect_response(
                    lambda response: (
                        response.request.method == "POST"
                        and response.url.split("?")[0].endswith("/uploads" if method == "upload" else "/imports")
                    )
                ) as response:
                    await frame.get_by_role("button", name="开始导入", exact=True).click()
                receipt = await (await response.value).json()
                if method == "local":
                    receipt = receipt["items"][0]
                assert receipt["accepted"]
                await expect(frame.get_by_role("alert")).to_contain_text("已接受 1 / 2 个文件")
                await frame.get_by_role("button", name="完成", exact=True).click()
                if other_scope:
                    await select_library(frame, "Other library")
                await frame.get_by_role("tab", name="检索测试", exact=True).click()
                await frame.get_by_role("textbox", name="检索问题").fill("Quartz")
                await frame.get_by_role("button", name="检索", exact=True).click()
                expected = "Independent instructions" if other_scope else "Original instructions"
                await expect(frame.locator(".markdown-body")).to_contain_text(expected)
                # Observe the terminal status at the browser bridge, independently of the API polling below.
                await page.evaluate(
                    """requestId => {
                    const fetch = window.fetch;
                    window.fetch = async (...args) => {
                        const response = await fetch(...args);
                        if (response.url.endsWith("/imports/" + requestId) &&
                            (await response.clone().json()).state === "completed") window.importCompleted = true;
                        return response;
                    };
                }""",
                    receipt["request_id"],
                )
            finally:
                hold.unlink(missing_ok=True)
            result = await wait_for(
                lambda: call("GET", f"/scopes/{scope}/imports/{receipt['request_id']}"),
                lambda result: result["state"] == "completed",
                log=tmp_path / "server.log",
            )
            assert result["current_revision"] == 2
            if other_scope:
                await page.wait_for_function("window.importCompleted")
                # Allow the bridge response and query notifications to reach the plugin frame.
                await frame.locator("body").evaluate(
                    "() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))"
                )
                await expect(frame.locator(".markdown-body")).to_contain_text(expected)
                await expect(frame.get_by_text("请重新检索", exact=False)).to_have_count(0)
                return
            await expect(frame.get_by_text("内容或检索条件已变化，请重新检索。", exact=True)).to_be_visible(  # noqa: RUF001
                timeout=12000
            )
            await expect(frame.locator(".markdown-body")).to_have_count(0)
            await expect(frame.get_by_role("textbox", name="检索问题")).to_have_value("Quartz")
            await frame.get_by_role("button", name="检索", exact=True).click()
            await expect(frame.locator(".markdown-body")).to_contain_text("Updated instructions")
            await expect(frame.locator(".markdown-body")).not_to_contain_text("Original instructions")

    asyncio.run(scenario())


def test_dashboard_reprocess_tracks_committed_revision(tmp_path):
    gate = f"""
from pathlib import Path
import time
_original_parse = _service.parse
def _gated_parse(*args, **kwargs):
    while Path({str(tmp_path / "hold")!r}).exists():
        time.sleep(0.05)
    return _original_parse(*args, **kwargs)
_service.parse = _gated_parse
"""

    async def scenario():
        async with dashboard(tmp_path, gate) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Rechunk", space)
            await upload(call, tmp_path, kb, "long.md", "# Guide\n\n" + "Quartz operational instructions. " * 30)
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await select_library(frame, "Rechunk")
            await frame.get_by_role("tab", name="设置", exact=True).click()
            await frame.get_by_role("spinbutton", name="最大分块字符数").fill("120")
            await frame.get_by_role("spinbutton", name="重叠字符数").fill("0")
            await frame.get_by_role("button", name="保存配置", exact=True).click()
            await expect(frame.get_by_text("配置已保存", exact=True)).to_be_visible()
            await frame.get_by_role("tab", name="文档", exact=True).click()
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await expect(frame.locator(".reader-content .markdown-body")).to_be_visible()
            before = await call("GET", f"/scopes/{kb['scope_id']}/documents")
            (tmp_path / "hold").touch()
            try:
                await frame.get_by_role("button", name="文档更多操作").click()
                await frame.get_by_role("menuitem", name="重新解析", exact=True).click()
                async with page.expect_response(
                    lambda r: r.request.method == "POST" and r.url.endswith("/reprocess")
                ) as response:
                    await frame.get_by_role("button", name="确认", exact=True).click()
                accepted = await (await response.value).json()
                await expect(frame.get_by_role("dialog")).to_have_count(0)
                await page.wait_for_timeout(300)
            finally:
                (tmp_path / "hold").unlink(missing_ok=True)
            await wait_for(
                lambda: call("GET", f"/scopes/{kb['scope_id']}/imports/{accepted['request_id']}"),
                lambda r: r["state"] == "completed",
                log=tmp_path / "server.log",
            )
            after = await call("GET", f"/scopes/{kb['scope_id']}/documents")
            assert after["items"][0]["ref"]["revision"] == 2
            assert after["items"][0]["content"]["chunk_count"] > before["items"][0]["content"]["chunk_count"]
            await expect(frame.get_by_text("v2 · 当前版本", exact=True)).to_be_visible(timeout=12000)

    asyncio.run(scenario())


def test_dashboard_refresh_invalidates_search_and_shows_hit_library(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            await library(call, "Review A", space)
            b = await library(call, "Review B", space)
            receipt = await upload(call, tmp_path, b, "same.md", "# Quartz\n\nContent from library B")
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await select_library(frame, "Review A")
            await frame.get_by_role("tab", name="检索测试", exact=True).click()
            await frame.get_by_role("combobox", name="检索知识库").press("ArrowDown")
            await frame.get_by_text("Review B", exact=True).last.click()
            await frame.get_by_role("combobox", name="检索知识库").press("Escape")
            await frame.get_by_role("textbox", name="检索问题").fill("Quartz")
            await frame.get_by_role("button", name="检索", exact=True).click()
            await frame.get_by_role("button", name="定位分块", exact=True).click()
            await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("library B")
            # The navigation must identify the library actually being read/edited.
            await expect(frame.get_by_role("button", name="切换知识库")).to_contain_text("Review B")
            await frame.get_by_role("button", name="back", exact=True).click()
            artifact = f"/v1/scopes/{b['scope_id']}/artifacts/document/{receipt['document_id']}"
            response = await page.context.request.get(
                origin + artifact + "/governance", headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"}
            )
            state = await response.json()
            response = await page.context.request.post(
                origin + artifact + "/forget",
                headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"},
                data={"revision": 1, "state_version": state["state_version"]},
            )
            assert response.status == 200, await response.text()
            await frame.get_by_role("button", name="刷新知识库").click()
            await expect(frame.locator(".markdown-body")).to_have_count(0)

    asyncio.run(scenario())


def test_dashboard_bulk_chunk_removal_is_one_atomic_revision(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Bulk chunks", space)
            receipt = await upload(
                call,
                tmp_path,
                kb,
                "bulk.md",
                "# First\n\nquartz alpha\n\n# Second\n\nquartz beta\n\n# Third\n\nzircon gamma",
            )
            scope, document = kb["scope_id"], receipt["document_id"]
            base = f"/scopes/{scope}/documents/{document}/revisions"
            original = await call("GET", base + "/1/chunks")
            assert original["total"] == 3
            ids = [chunk["chunk_id"] for chunk in original["items"]]
            artifact = f"/v1/scopes/{scope}/artifacts/document/{document}"
            headers = {"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}"}
            # Invalid selections fail before publishing any change, including a mixed valid/missing selection.
            for selection in ([], [ids[0], "missing"], [ids[0], ids[0]]):
                response = await page.context.request.put(
                    origin + artifact,
                    headers={**headers, "If-Match": '"revision:1"'},
                    data={"content": {"operation": "remove_chunks", "chunk_ids": selection}},
                )
                assert response.status == 422, await response.text()
                response = await page.context.request.get(origin + artifact, headers=headers)
                assert (await response.json())["revision"] == 1
            await page.goto(origin + "/dashboard/plugins/knowledge-base")
            await select_library(frame, "Bulk chunks")
            await frame.get_by_role("button", name="浏览", exact=True).click()
            await frame.get_by_role("checkbox", name="选择分块 1", exact=True).check()
            await frame.get_by_role("checkbox", name="选择分块 2", exact=True).check()
            await frame.get_by_role("button", name="移除选中分块 (2)", exact=True).click()
            writes = []
            page.on(
                "request",
                lambda request: (
                    writes.append(request) if request.method == "PUT" and "/artifacts/" in request.url else None
                ),
            )
            await frame.get_by_role("button", name="确认移除", exact=True).click()
            await expect(frame.get_by_text("v2 · 当前版本", exact=True)).to_be_visible()
            assert len(writes) == 1
            assert writes[0].post_data_json["content"] == {"operation": "remove_chunks", "chunk_ids": ids[:2]}
            current = await call("GET", base + "/2/chunks")
            assert current["total"] == 1 and current["items"][0]["chunk_id"] == ids[2]
            assert (await call("GET", base + "/1/chunks")) == original
            response = await page.context.request.get(origin + artifact + "/revisions/2", headers=headers)
            revision = await response.json()
            evidence = next(item for item in revision["sources"] if item["source_type"] == "content")
            response = await page.context.request.get(
                origin + f"/v1/scopes/{scope}/sources/content/{evidence['source_id']}", headers=headers
            )
            assert (await response.json())["content"] == {"operation": "remove_chunks", "chunk_ids": ids[:2]}
            result = await call(
                "POST",
                "/retrieve",
                json={"space_scope_id": space, "scope_ids": [scope], "query": "quartz", "mode": "fts"},
            )
            assert not result["hits"]
            response = await page.context.request.put(
                origin + artifact,
                headers={**headers, "If-Match": '"revision:1"'},
                data={"content": {"operation": "remove_chunks", "chunk_ids": [ids[2]]}},
            )
            assert response.status == 412
            await page.evaluate("""() => {
                const original = window.fetch;
                window.oldChunk = {held: false};
                window.fetch = async (...args) => {
                    const response = await original(...args);
                    if (!window.oldChunk.held && String(args[0]).includes('/revisions/1/chunks/')) {
                        window.oldChunk.held = true;
                        await new Promise(resolve => { window.oldChunk.release = resolve; });
                    }
                    return response;
                };
            }""")
            await frame.get_by_role("combobox", name="文档版本").press("ArrowDown")
            await frame.get_by_text("v1 · 历史版本", exact=True).click()
            await page.wait_for_function("window.oldChunk.held")
            await expect(frame.get_by_role("checkbox", name="选择分块 1", exact=True)).to_be_disabled()
            await expect(frame.get_by_role("button", name="移除选中分块", exact=True)).to_be_disabled()
            await frame.get_by_role("combobox", name="文档版本").press("ArrowDown")
            await frame.get_by_text("v2 · 当前版本", exact=True).last.click()
            await expect(frame.get_by_role("checkbox", name="选择分块 1", exact=True)).not_to_be_checked()
            await expect(frame.locator(".reader-content .markdown-body")).to_contain_text("zircon gamma")
            await page.evaluate("window.oldChunk.release()")
            await page.wait_for_timeout(200)
            await expect(frame.locator(".reader-content .markdown-body")).not_to_contain_text("quartz alpha")
            await frame.get_by_role("checkbox", name="选择已加载分块").check()
            await frame.get_by_role("button", name="移除选中分块 (1)", exact=True).click()
            await frame.get_by_role("button", name="确认移除", exact=True).click()
            await expect(frame.get_by_text("v3 · 当前版本", exact=True)).to_be_visible()
            assert (await call("GET", base + "/3/chunks"))["total"] == 0

    asyncio.run(scenario())
