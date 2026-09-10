"""Dify's typed bridge against the actual PowerContext HTTP app and SQLite runtime."""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.artifacts.memory import MemoryCandidateRequest, MemoryEntryInput
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import InferenceConfig
from powercontext.builtin.sources import ContentSource
from powercontext.client import PowerContextClient
from powercontext.http import CreateScopeRequest, SetScopeBindingRequest
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


class TrajectoryPipeline:
    async def extract(self, request: MemoryCandidateRequest, /) -> tuple[MemoryEntryInput, ...]:
        return tuple(
            MemoryEntryInput(
                kind="agent-trajectory", text=source.content, sources=(source,), reason="Dify tool evidence"
            )
            for source in request.sources
            if isinstance(source, ContentSource) and source.metadata.get("event") == "tool_result"
        )


def test_capture_flush_and_recall_stay_in_the_bound_scope(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "integrations/dify/powercontext"))
    bridge = importlib.import_module("bridge")
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'memory.db'}"),
            inference=InferenceConfig(),
            mcp=McpConfig(enabled=False),
        ),
        candidate_pipeline=TrajectoryPipeline(),
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app)) as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            connection = bridge.Connection(
                base_url="http://testserver",
                token="unused-local-test-token",  # noqa: S106
                namespace="deployment",
            )
            first = bridge.MemoryIdentity(app_id="app", subject_id="first")
            second = bridge.MemoryIdentity(app_id="app", subject_id="second")
            for identity in (first, second):
                scope = await client.create_scope(
                    CreateScopeRequest(
                        title=identity.subject_id, summary="Dify test scope", idempotency_key=identity.subject_id
                    )
                )
                await client.set_scope_binding(
                    SetScopeBindingRequest.model_validate({
                        "key": bridge.binding_key(connection.namespace, identity).model_dump(),
                        "scope_id": scope.scope_id,
                    })
                )
            event = {
                "event_id": "run:1",
                "event": "tool_result",
                "sequence": 1,
                "payload": {"result": "The project codename is silverorchard.", "api_key": "never-store-this"},
            }
            await bridge.execute(client, connection, first, "capture_event", event)
            # Captured Sources become Memory through the configured extraction pipeline.
            await bridge.execute(client, connection, first, "flush_memory", {})
            memory = await bridge.execute(
                client, connection, first, "prepare_context", {"query": "silverorchard", "max_bytes": 2000}
            )
            assert memory["status"] == "ready"
            assert "silverorchard" in memory["content"]
            assert "never-store-this" not in memory["content"]
            other = await bridge.execute(
                client, connection, second, "prepare_context", {"query": "silverorchard", "max_bytes": 2000}
            )
            assert other["status"] == "empty"

    asyncio.run(scenario())
