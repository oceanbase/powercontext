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

from tests.e2e.plugin_server import ADMIN_AUTHORIZATION_VALUE, wait_for
from tests.e2e.test_knowledge_base_dashboard import dashboard, library, select_library, upload


async def edit_first_chunk(call, page, origin, kb, receipt, revision, text):
    scope, document = kb["scope_id"], receipt["document_id"]
    chunks = await call("GET", f"/scopes/{scope}/documents/{document}/revisions/{revision}/chunks")
    response = await page.context.request.put(
        origin + f"/v1/scopes/{scope}/artifacts/document/{document}",
        headers={"Authorization": f"Bearer {ADMIN_AUTHORIZATION_VALUE}", "If-Match": f'"revision:{revision}"'},
        data={"content": {"operation": "edit_chunk", "chunk_id": chunks["items"][0]["chunk_id"], "text": text}},
    )
    assert response.status == 200, await response.text()


def test_dashboard_batch_forget_filters_selection_and_preserves_conflicting_documents(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Batch", space)
            other = await library(call, "Other", space)
            a = await upload(call, tmp_path, kb, "a.md", "# A\n\nOriginal A")
            b = await upload(call, tmp_path, kb, "b.md", "# B\n\nOriginal B")
            await upload(call, tmp_path, other, "a.md", "# Other\n\nKeep other library")
            await page.goto(origin + "/dashboard/plugins/knowledge-base?scope=" + kb["scope_id"])
            await frame.get_by_role("checkbox", name="选择文档 a.md", exact=True).check()
            await expect(frame.get_by_text("已选择 1 篇文档", exact=True)).to_be_visible()
            await frame.get_by_role("textbox", name="筛选文档").fill("b.md")
            await expect(frame.get_by_text("已选择 0 篇文档", exact=True)).to_be_visible()
            await (
                frame
                .get_by_role("columnheader")
                .filter(has=frame.get_by_role("checkbox"))
                .get_by_role("checkbox")
                .check()
            )
            await expect(frame.get_by_text("已选择 1 篇文档", exact=True)).to_be_visible()
            await frame.get_by_role("textbox", name="筛选文档").fill("")
            await expect(frame.get_by_text("已选择 0 篇文档", exact=True)).to_be_visible()
            await frame.get_by_role("checkbox", name="选择文档 a.md", exact=True).check()
            await select_library(frame, "Other")
            await expect(frame.get_by_text("已选择 0 篇文档", exact=True)).to_be_visible()
            await expect(frame.get_by_role("button", name="批量遗忘", exact=True)).to_be_disabled()
            await select_library(frame, "Batch")
            await frame.get_by_role("checkbox", name="选择文档 a.md", exact=True).check()
            await frame.get_by_role("checkbox", name="选择文档 b.md", exact=True).check()
            await frame.get_by_role("button", name="批量遗忘", exact=True).click()
            dialog = frame.get_by_role("dialog")
            await expect(dialog).to_contain_text("选中的 2 篇文档")
            # Editing after confirmation opens must not make the batch forget a newer version.
            await edit_first_chunk(call, page, origin, kb, b, 1, "Concurrent edit")
            await dialog.get_by_role("button", name="确认执行", exact=True).click()
            await expect(dialog).to_contain_text("成功 1 篇")
            await expect(dialog).to_contain_text("失败 1 篇")
            await expect(dialog.get_by_role("row").filter(has_text="b.md")).to_contain_text("失败")
            await dialog.get_by_role("button", name="关闭", exact=True).click()
            await expect(dialog).not_to_be_visible()
            await expect(frame.get_by_role("checkbox", name="选择文档 a.md", exact=True)).to_be_disabled()
            await expect(frame.get_by_text("已选择 0 篇文档", exact=True)).to_be_visible()
            docs = (await call("GET", f"/scopes/{kb['scope_id']}/documents"))["items"]
            states = {doc["ref"]["artifact_id"]: doc for doc in docs}
            assert states[a["document_id"]]["lifecycle_state"] == "deprecated"
            assert states[b["document_id"]]["lifecycle_state"] == "active"
            assert states[b["document_id"]]["ref"]["revision"] == 2
            others = (await call("GET", f"/scopes/{other['scope_id']}/documents"))["items"]
            assert others[0]["lifecycle_state"] == "active" and others[0]["ref"]["revision"] == 1
            search = await call(
                "POST",
                "/retrieve",
                json={"space_scope_id": space, "scope_ids": [kb["scope_id"]], "query": "Original", "mode": "fts"},
            )
            assert all(hit["document"]["artifact_id"] != a["document_id"] for hit in search["hits"])

    asyncio.run(scenario())


def test_dashboard_batch_reprocess_requires_explicit_edit_replacement_and_current_revision(tmp_path):
    async def scenario():
        async with dashboard(tmp_path) as (call, page, frame, origin, expect):
            space = (await call("GET", "/me/default-space"))["space_scope_id"]
            kb = await library(call, "Reparse", space)
            a = await upload(call, tmp_path, kb, "edited.md", "# A\n\nOriginal A")
            b = await upload(call, tmp_path, kb, "plain.md", "# B\n\nOriginal B")
            await edit_first_chunk(call, page, origin, kb, a, 1, "Manual content")
            await page.goto(origin + "/dashboard/plugins/knowledge-base?scope=" + kb["scope_id"])
            for name in ("edited.md", "plain.md"):
                await frame.get_by_role("checkbox", name="选择文档 " + name, exact=True).check()
            await frame.get_by_role("button", name="批量重新解析", exact=True).click()
            dialog = frame.get_by_role("dialog")
            await expect(dialog.get_by_role("checkbox", name="允许覆盖所选文档的人工修改")).not_to_be_checked()
            await dialog.get_by_role("button", name="确认执行", exact=True).click()
            await expect(dialog).to_contain_text("成功 1 篇")
            await expect(dialog).to_contain_text("失败 1 篇")
            await expect(dialog.get_by_role("row").filter(has_text="plain.md")).to_contain_text("已提交解析")
            await expect(dialog.get_by_role("row").filter(has_text="edited.md")).to_contain_text("失败")
            await dialog.get_by_role("button", name="关闭", exact=True).click()
            await expect(dialog).not_to_be_visible()

            async def documents():
                return (await call("GET", f"/scopes/{kb['scope_id']}/documents"))["items"]

            docs = await wait_for(
                documents,
                lambda items: any(
                    d["ref"]["artifact_id"] == b["document_id"] and d["ref"]["revision"] == 2 for d in items
                ),
                log=tmp_path / "server.log",
            )
            assert next(d for d in docs if d["ref"]["artifact_id"] == a["document_id"])["ref"]["revision"] == 2
            await frame.get_by_role("checkbox", name="选择文档 edited.md", exact=True).check()
            await frame.get_by_role("button", name="批量重新解析", exact=True).click()
            await dialog.get_by_role("checkbox", name="允许覆盖所选文档的人工修改").check()
            await edit_first_chunk(call, page, origin, kb, a, 2, "Newer manual content")
            await dialog.get_by_role("button", name="确认执行", exact=True).click()
            await expect(dialog).to_contain_text("成功 0 篇")
            await expect(dialog).to_contain_text("失败 1 篇")
            await expect(dialog).to_contain_text("revision changed")
            await dialog.get_by_role("button", name="关闭", exact=True).click()
            await expect(dialog).not_to_be_visible()
            await expect(frame.get_by_role("row").filter(has_text="edited.md")).to_contain_text("v3")
            await frame.get_by_role("checkbox", name="选择文档 edited.md", exact=True).check()
            await frame.get_by_role("button", name="批量重新解析", exact=True).click()
            await expect(dialog.get_by_role("checkbox", name="允许覆盖所选文档的人工修改")).not_to_be_checked()
            await dialog.get_by_role("checkbox", name="允许覆盖所选文档的人工修改").check()
            await dialog.get_by_role("button", name="确认执行", exact=True).click()
            await expect(dialog).to_contain_text("成功 1 篇")
            await expect(dialog).to_contain_text("失败 0 篇")
            await dialog.get_by_role("button", name="关闭", exact=True).click()
            await expect(dialog).not_to_be_visible()
            await expect(frame.get_by_role("row").filter(has_text="edited.md")).to_contain_text("v4", timeout=20000)
            chunks = await call("GET", f"/scopes/{kb['scope_id']}/documents/{a['document_id']}/revisions/4/chunks")
            chunk = await call(
                "GET",
                f"/scopes/{kb['scope_id']}/documents/{a['document_id']}/revisions/4/chunks/{chunks['items'][0]['chunk_id']}",
            )
            assert "Original A" in chunk["text"] and not chunk["manually_modified"]

    asyncio.run(scenario())
