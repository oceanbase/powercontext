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

"""Compact orientation preserves full continuation through Client, HTTP, and persistence."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastmcp import Client

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    CaptureContentSourceRequest,
    CommitHandoffRequest,
    ContinueHandoffRequest,
    FinalizeHandoffRequest,
    HandoffDraft,
    HandoffSelection,
    PrepareHandoffHintRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.mcp import create_mcp_server
from powercontext.server.settings import BearerAuthConfig, McpConfig, ServerSettings


def test_hint_delivery_preserves_full_prepared_and_committed_handoffs(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'hints.db'}"),
            auth=BearerAuthConfig(enabled=False),
            mcp=McpConfig(enabled=False),
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            empty = await client.prepare_handoff_hint(
                PrepareHandoffHintRequest(scope_id=scope_id, selection=HandoffSelection.LATEST)
            )
            assert empty.status == "empty" and empty.content is None and empty.content_bytes == 0
            source = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=scope_id,
                    source_id="hints-evidence",
                    content="Raw transcript-like evidence must not be injected.",
                )
            )
            citation = {"kind": "source", "source_ref": source.source.model_dump()}
            draft = HandoffDraft.model_validate({
                "objective": "修复解析器",
                "state": [{"text": "Verified error mapping.", "citations": [citation]}],
                "disposition": "continuable",
                "next_action": {"text": "运行回归测试。", "citations": [citation]},
                "omissions": [{"text": "Production is not checked.", "citation": None}],
            })
            prepared = await client.finalize_handoff(FinalizeHandoffRequest(scope_id=scope_id, draft=draft))
            request = PrepareHandoffHintRequest(
                scope_id=scope_id, selection=HandoffSelection.PREPARED, prepared=prepared
            )
            hint = await client.prepare_handoff_hint(request)
            assert hint.status == "ready" and hint.content is not None
            assert hint.content_bytes == len(hint.content.encode("utf-8")) <= 2000
            assert "Raw transcript-like" not in hint.content
            assert "hints-evidence" in hint.content
            hint_data = json.loads(
                "\n".join(
                    line.removeprefix(">     ") for line in hint.content.splitlines() if line.startswith(">     ")
                )
            )
            assert hint_data["selection"] == "prepared"
            assert hint_data["evidence_scope_id"] == scope_id
            assert hint_data["selected_evidence"][0]["source_ref"]["source_id"] == source.source.source_id
            assert set(hint.model_dump(by_alias=True)) == {"schema", "status", "content", "content_bytes"}
            assert (
                await client.prepare_handoff_hint(request.model_copy(update={"max_bytes": hint.content_bytes}))
            ) == hint
            skipped = await client.prepare_handoff_hint(
                request.model_copy(update={"max_bytes": hint.content_bytes - 1})
            )
            assert skipped.status == "empty" and skipped.content is None
            full = await client.continue_handoff(
                ContinueHandoffRequest(scope_id=scope_id, selection=HandoffSelection.PREPARED, prepared=prepared)
            )
            assert full.content == prepared.content
            committed = await client.commit_handoff(CommitHandoffRequest(scope_id=scope_id, handoff=prepared))
            changed = draft.model_copy(update={"objective": "New work objective."})
            newer = await client.finalize_handoff(FinalizeHandoffRequest(scope_id=scope_id, draft=changed))
            await client.commit_handoff(CommitHandoffRequest(scope_id=scope_id, handoff=newer))
            exact_request = PrepareHandoffHintRequest(
                scope_id=scope_id, selection=HandoffSelection.EXACT, revision=committed.reference
            )
            exact = await client.prepare_handoff_hint(exact_request)
            assert exact.content is not None and "修复解析器" in exact.content
            exact_data = json.loads(
                "\n".join(
                    line.removeprefix(">     ") for line in exact.content.splitlines() if line.startswith(">     ")
                )
            )
            assert exact_data["selected_revision"]["revision"] == 1
            assert exact_data["current_revision"]["revision"] == 2
            assert (
                await client.continue_handoff(
                    ContinueHandoffRequest(
                        scope_id=scope_id, selection=HandoffSelection.EXACT, revision=committed.reference
                    )
                )
            ).content == committed.content
            for payload in (
                {"max_bytes": 0},
                {"max_bytes": 4001},
                {"extra": True},
                {"selection": "exact"},
                {"selection": "latest", "prepared": prepared.model_dump(mode="json", by_alias=True)},
                {
                    "selection": "prepared",
                    "prepared": prepared.model_dump(mode="json", by_alias=True),
                    "revision": committed.reference.model_dump(),
                },
            ):
                response = await transport.post(
                    "/v1/handoff/hint", json={"scope_id": scope_id, "selection": "latest"} | payload
                )
                assert response.status_code == 422, response.text
            async with Client(create_mcp_server(app)) as mcp:
                tools = {tool.name: tool for tool in await mcp.list_tools()}
                assert tools["prepare_handoff_hint"].annotations.readOnlyHint is True
                delivered = await mcp.call_tool("prepare_handoff_hint", exact_request.model_dump(mode="json"))
                assert not delivered.is_error
                assert delivered.structured_content == exact.model_dump(mode="json", by_alias=True)

    asyncio.run(scenario())
