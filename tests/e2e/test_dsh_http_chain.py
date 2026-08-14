from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    CaptureContentSourceRequest,
    PrepareContextRequest,
    ReadinessStatus,
    RememberMemoryRequest,
    SearchMemoryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings

SCOPE_ID = "project:dsh-e2e"
TEXT = "Keep the DSH plugin on the public HTTP contract."


def test_dsh_http_paths_work_without_a_model(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'dsh.db'}"),
            mcp=McpConfig(enabled=False),
        ),
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport)
            live = await client.get_liveness()
            ready = await client.get_readiness()
            remembered = await client.remember_memory(
                RememberMemoryRequest(scope_id=SCOPE_ID, kind="decision", text=TEXT),
            )
            found = await client.search_memory(
                SearchMemoryRequest(scope_id=SCOPE_ID, query="DSH plugin HTTP contract"),
            )
            prepared = await client.prepare_context(
                PrepareContextRequest(scope_id=SCOPE_ID, query="DSH plugin HTTP contract"),
            )
            captured = await client.capture_content_source(
                CaptureContentSourceRequest(
                    scope_id=SCOPE_ID,
                    source_id="dsh-e2e-turn-1",
                    content="Call through the plugin client without a model.",
                    metadata={"origin": "dsh", "event": "e2e"},
                ),
            )

        assert live.status == "ok"
        assert ready.status in {ReadinessStatus.READY, ReadinessStatus.DEGRADED}
        assert remembered.entry is not None
        assert remembered.entry.text == TEXT
        assert found.hits
        assert {hit.text for hit in found.hits} == {TEXT}
        assert prepared.schema_ == "powercontext.prepared-context.v1"
        assert captured.position >= 1

    asyncio.run(scenario())
