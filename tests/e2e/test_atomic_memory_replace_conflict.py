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

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.client import PowerContextClient
from powercontext.http import (
    AtomicMemoryInput,
    AtomicMemoryWriteContent,
    MergeAtomicMemoryRequest,
    RememberMemoryRequest,
)
from powercontext.http._generated.operations import REPLACE_ARTIFACT
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


def test_merged_atomic_memory_replace_returns_documented_conflict(tmp_path: Path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'merge.db'}"), mcp=McpConfig(enabled=False)
        )
    )

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
        ):
            client = PowerContextClient("http://testserver", http_client=transport, trust_transport_security=True)
            scope_id = (await client.get_default_scope()).scope_id
            inputs = []
            for text in ("Use strict transport models.", "Validate transport input."):
                remembered = await client.remember_memory(
                    RememberMemoryRequest(scope_id=scope_id, kind="rule", text=text)
                )
                record = remembered.records[0]
                inputs.append(AtomicMemoryInput(artifact=record.artifact, state_version=record.state_version))
            original = inputs[0].artifact
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{original.artifact_id}"
            head = await transport.get(path)
            assert head.status_code == 200

            merged = await client.merge_atomic_memories(
                MergeAtomicMemoryRequest(
                    scope_id=scope_id,
                    inputs=inputs,
                    content=AtomicMemoryWriteContent(kind="rule", text="Validate input with strict transport models."),
                )
            )
            successor = next(record for record in merged.records if record.state == "active")
            successor_path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{successor.artifact.artifact_id}"
            successor_head = await transport.get(successor_path)
            merged_head = await transport.get(path)
            assert merged_head.headers["ETag"] == head.headers["ETag"]

            response = await transport.put(
                path,
                headers={"If-Match": head.headers["ETag"]},
                json={"content": {"kind": "rule", "text": "Edit the original memory."}},
            )
            assert response.status_code == 409
            assert response.json()["error"]["code"] == "invalid_memory_state"
            assert (await transport.get(path)).json() == head.json()
            assert (await transport.get(successor_path)).json() == successor_head.json()

            contract_response = await transport.get("/openapi.json")
            assert contract_response.status_code == 200
            operation = contract_response.json()["paths"][REPLACE_ARTIFACT.path]["put"]
            assert operation["responses"].get(str(response.status_code)) == {"$ref": "#/components/responses/Conflict"}

    asyncio.run(scenario())
