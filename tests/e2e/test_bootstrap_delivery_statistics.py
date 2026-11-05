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

"""Delivery statistics count persisted receipts, not inferred Agent adoption."""

from __future__ import annotations

import asyncio

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    BootstrapContextRequest,
    CreateScopeRequest,
    RecordBootstrapDeliveryRequest,
    RememberMemoryRequest,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings
from tests.e2e.test_bootstrap_context import _tag_for_bootstrap


def test_receipt_statistics_are_scope_safe_deduplicated_and_content_free(tmp_path):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'delivery.db'}"), mcp=McpConfig(enabled=False)
        )
    )

    async def scenario():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scopes = []
            for name in ("alpha", "beta"):
                scope = await client.create_scope(
                    CreateScopeRequest(title=name, summary="Fixture", idempotency_key=name)
                )
                scopes.append(scope.scope_id)
                memory = await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope.scope_id, kind="preference", text="private delivery statistics sentinel"
                    )
                )
                await _tag_for_bootstrap(client, scope.scope_id, memory.records[0].artifact.artifact_id)
            requests = []
            for scope, event, integration in [
                (scopes[0], "first", "codex"),
                (scopes[0], "pending", "codex"),
                (scopes[1], "failed", "claude-code"),
            ]:
                prepared = await client.prepare_bootstrap_context(
                    BootstrapContextRequest.model_validate({
                        "scope_id": scope,
                        "event_id": event,
                        "integration": integration,
                        "lifecycle": "startup",
                        "enabled": True,
                    })
                )
                assert prepared.status == "ready"
                requests.append(prepared)
            delivered = await client.record_bootstrap_delivery(
                RecordBootstrapDeliveryRequest.model_validate({
                    "scope_id": scopes[0],
                    "receipt_id": requests[0].receipt.receipt_id,
                    "outcome": "injected",
                })
            )
            assert delivered.state == "injected"
            replay = await transport.post(
                "/v1/context/bootstrap/receipts",
                json={"scope_id": scopes[0], "receipt_id": requests[0].receipt.receipt_id, "outcome": "injected"},
            )
            assert replay.status_code == 422
            await client.record_bootstrap_delivery(
                RecordBootstrapDeliveryRequest.model_validate({
                    "scope_id": scopes[1],
                    "receipt_id": requests[2].receipt.receipt_id,
                    "outcome": "failed",
                })
            )
            response = await transport.post(
                "/v1/stats", json={"selection": {"mode": "exact", "scope_ids": [scopes[0]]}, "period": "30d"}
            )
            assert response.status_code == 200
            stats = response.json()["by_scope"][0]["bootstrap_delivery"]
            assert stats["actual_adoption"] == "unobserved"
            assert stats["retention_days"] == 30
            readings = stats["by_integration"]
            assert {(r["integration"], r["state"], r["receipts"]) for r in readings} == {
                ("codex", "pending", 1),
                ("codex", "injected", 1),
            }
            assert next(r for r in readings if r["state"] == "injected")["content_bytes"] > 0
            assert "private delivery statistics sentinel" not in response.text
            assert requests[0].receipt.receipt_id not in response.text
            beta = await transport.post("/v1/stats", json={"selection": {"mode": "exact", "scope_ids": [scopes[1]]}})
            assert [
                (r["state"], r["receipts"]) for r in beta.json()["by_scope"][0]["bootstrap_delivery"]["by_integration"]
            ] == [("failed", 1)]

    asyncio.run(scenario())
