# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import asyncio
from typing import Any, cast

import httpx

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import WorkRepository, WorkSpec
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import OperationsConfig, WorkerConfig
from powercontext.builtin.runtime.operations import OperationManager
from powercontext.builtin.sources import ContentCapture
from powercontext.server.app import ServerApplication, create_app


def test_operation_http_api_is_stateless_and_uses_optimistic_mutations(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}"))
        async with open_builtin_contexts(config) as contexts:
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="queued work"))
            manager = OperationManager(
                contexts=contexts,
                operations=OperationsConfig(),
                worker=WorkerConfig(),
                local_worker=None,
                payload_version=1,
                memory_window_limit=100,
            )
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                internal = await repository.enqueue(
                    connection,
                    WorkSpec(
                        kind="powercontext.maintenance.operations",
                        payload_version=1,
                        scope_id="system:operations",
                        lane_key="a" * 64,
                        logical_key="b" * 64,
                        payload={},
                    ),
                )
            app = create_app(application=cast(ServerApplication, cast(Any, object())))
            app.state.operation_manager = manager
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as client:
                accepted = await client.post(
                    "/v1/memory/flush",
                    headers={"Prefer": "respond-async"},
                    json={"scope_id": "project"},
                )
                assert accepted.status_code == 202
                operation_id = accepted.json()["operation_id"]
                assert accepted.headers["location"] == f"/v1/operations/{operation_id}"

                stored = await client.get(f"/v1/operations/{operation_id}")
                assert stored.status_code == 200
                assert stored.json()["status"] == "queued"

                listed = await client.get("/v1/operations", params={"scope_id": "project", "limit": 1})
                assert listed.status_code == 200
                assert [item["operation_id"] for item in listed.json()["items"]] == [operation_id]

                all_public = await client.get("/v1/operations")
                assert [item["operation_id"] for item in all_public.json()["items"]] == [operation_id]
                hidden = await client.get(f"/v1/operations/{internal.work.work_id}")
                assert hidden.status_code == 404

                cancelled = await client.post(
                    f"/v1/operations/{operation_id}/cancel",
                    json={"expected_version": stored.json()["state_version"]},
                )
                assert cancelled.status_code == 200
                assert cancelled.json()["status"] == "cancelled"

                conflict = await client.post(
                    f"/v1/operations/{operation_id}/cancel",
                    json={"expected_version": stored.json()["state_version"]},
                )
                assert conflict.status_code == 409
                assert conflict.json()["error"]["code"] == "operation_conflict"

    asyncio.run(scenario())
