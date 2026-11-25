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

"""Undoing a merge on an Atomic Memory that no merge created is a caller mistake, not corrupted data."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import RuntimeConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import McpConfig, ServerSettings


@pytest.mark.parametrize(
    ("target", "message"),
    (("plain", "was not created by a merge"), ("input", "is a merge input, so undo the merge on its result")),
)
def test_undo_merge_on_a_target_that_is_not_a_merge_result_is_an_invalid_state(
    tmp_path: Path, target: str, message: str
) -> None:
    database = tmp_path / "undo-merge-target.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"),
            runtime=RuntimeConfig(
                atomic_memory_preview_signing_secret=SecretStr("x" * 32), atomic_memory_preview_signing_key_id="test"
            ),
            mcp=McpConfig(enabled=False),
        )
    )

    def snapshot() -> dict[str, list[tuple[object, ...]]]:
        with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
            return {
                table: connection.execute(f"SELECT * FROM {table} ORDER BY 1, 2, 3").fetchall()  # noqa: S608
                for table in ("pc_artifacts", "pc_artifact_heads", "pc_atomic_memory_current")
            }

    async def scenario() -> None:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client,
        ):
            scope_id = (await client.get("/v1/scopes/default")).json()["scope_id"]
            records = {}
            for name in ("first", "second", "plain"):
                response = await client.post(
                    "/v1/memory/remember", json={"scope_id": scope_id, "kind": "fact", "text": f"The {name} fact."}
                )
                assert response.status_code == 200, response.text
                records[name] = response.json()["records"][0]
            merged = await client.post(
                "/v1/atomic-memory/merges",
                json={
                    "scope_id": scope_id,
                    "inputs": [
                        {"artifact": records[name]["artifact"], "state_version": records[name]["state_version"]}
                        for name in ("first", "second")
                    ],
                    "content": {"kind": "fact", "text": "The first and second facts."},
                },
            )
            assert merged.status_code == 200, merged.text
            result_id = next(item for item in merged.json()["records"] if item["state"] == "active")["artifact"][
                "artifact_id"
            ]
            identity = records["plain" if target == "plain" else "first"]["artifact"]["artifact_id"]
            body = {"scope_id": scope_id, "operation": "undo_merge", "target": {"artifact_id": identity}}
            before = snapshot()
            for response in (
                await client.post("/v1/atomic-memory/restoration-previews", json=body),
                await client.post("/v1/atomic-memory/restorations", json=body),
                await client.post(
                    f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{identity}/restorations",
                    json={"operation": "undo_merge"},
                ),
            ):
                assert response.status_code == 409, response.text
                assert response.json()["error"]["code"] == "invalid_memory_state"
                assert message in response.json()["error"]["message"]
            assert snapshot() == before
            undone = await client.post(
                "/v1/atomic-memory/restorations",
                json={**body, "target": {"artifact_id": result_id}},
            )
            assert undone.status_code == 200, undone.text
            assert undone.json()["undo_merge_results"] == [result_id]

    asyncio.run(scenario())
