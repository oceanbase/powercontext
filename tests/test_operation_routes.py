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

"""The durable operation endpoints promised by the contract and the docs.

The runtime facade is covered by ``tests/e2e/test_operation_api.py``. This file
covers the transport in front of it: the four routes, the Scope ownership check
that keeps another Scope's identifiers unprobeable, the maintenance-work
whitelist, and the optimistic-concurrency contract of cancel and retry.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import WorkRepository, WorkSpec, WorkStatus
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import OperationsConfig, WorkerConfig
from powercontext.builtin.runtime.operations import OperationManager
from powercontext.builtin.runtime.work_handlers import EXPERIENCE_WORK_KIND, MEMORY_WORK_KIND
from powercontext.server.app import create_app

SCOPE = "project:operations"


def _spec(kind: str, scope_id: str, suffix: str) -> WorkSpec:
    return WorkSpec(
        kind=kind,
        payload_version=1,
        scope_id=scope_id,
        lane_key=f"{suffix:0<64}"[:64],
        logical_key=f"{suffix:1<64}"[:64],
        payload={"through": 1},
    )


def test_operation_routes_expose_only_scope_owned_public_work(tmp_path) -> None:
    async def scenario() -> None:
        config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'operations.db'}"))
        async with open_builtin_contexts(config) as contexts:
            manager = OperationManager(
                contexts=contexts,
                operations=OperationsConfig(),
                worker=WorkerConfig(),
                local_worker=None,
                payload_version=1,
            )
            repository = WorkRepository()
            async with contexts.database.transaction() as connection:
                memory = await repository.enqueue(connection, _spec(MEMORY_WORK_KIND, SCOPE, "a"))
                experience = await repository.enqueue(connection, _spec(EXPERIENCE_WORK_KIND, SCOPE, "b"))
                foreign = await repository.enqueue(connection, _spec(MEMORY_WORK_KIND, "project:other", "c"))
                maintenance = await repository.enqueue(
                    connection,
                    _spec("powercontext.maintenance.operations", SCOPE, "d"),
                )

            app = create_app(application=SimpleNamespace(operations=manager))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
                listed = await client.get(f"/v1/scopes/{SCOPE}/operations")
                assert listed.status_code == 200, listed.text
                body = listed.json()
                # Both rows share a creation timestamp at this resolution, so the
                # page order is only guaranteed by the (created_at, work_id) key.
                assert {item["operation_id"] for item in body["items"]} == {
                    experience.work.work_id,
                    memory.work.work_id,
                }
                assert body["next_cursor"] is None
                assert {item["status"] for item in body["items"]} == {"queued"}
                assert {item["kind"] for item in body["items"]} == {MEMORY_WORK_KIND, EXPERIENCE_WORK_KIND}

                # A page is bounded by limit and continued by the opaque cursor.
                first = await client.get(f"/v1/scopes/{SCOPE}/operations", params={"limit": 1})
                assert first.status_code == 200, first.text
                page = first.json()
                assert len(page["items"]) == 1
                assert page["next_cursor"]
                second = await client.get(
                    f"/v1/scopes/{SCOPE}/operations",
                    params={"limit": 1, "cursor": page["next_cursor"]},
                )
                assert second.status_code == 200, second.text
                assert len(second.json()["items"]) == 1
                assert second.json()["items"][0]["operation_id"] != page["items"][0]["operation_id"]

                # Kind and status filters narrow the same page.
                filtered = await client.get(
                    f"/v1/scopes/{SCOPE}/operations",
                    params={"kind": MEMORY_WORK_KIND, "status": "queued"},
                )
                assert filtered.status_code == 200, filtered.text
                assert [item["operation_id"] for item in filtered.json()["items"]] == [memory.work.work_id]
                assert (
                    await client.get(f"/v1/scopes/{SCOPE}/operations", params={"status": "no-such-state"})
                ).status_code == 422
                assert (await client.get(f"/v1/scopes/{SCOPE}/operations", params={"unknown": "1"})).status_code == 422

                # Retrieval answers for this Scope and hides everything else.
                detail = await client.get(f"/v1/scopes/{SCOPE}/operations/{memory.work.work_id}")
                assert detail.status_code == 200, detail.text
                assert detail.json()["operation_id"] == memory.work.work_id
                assert (
                    await client.get(f"/v1/scopes/project:other/operations/{memory.work.work_id}")
                ).status_code == 404
                assert (await client.get(f"/v1/scopes/{SCOPE}/operations/{foreign.work.work_id}")).status_code == 404
                assert (
                    await client.get(f"/v1/scopes/{SCOPE}/operations/{maintenance.work.work_id}")
                ).status_code == 404

                # Cancel is optimistic: the stale version loses and reports 409.
                stale = await client.post(
                    f"/v1/scopes/{SCOPE}/operations/{memory.work.work_id}/cancel",
                    json={"expected_version": memory.work.state_version + 1},
                )
                assert stale.status_code == 409, stale.text
                assert stale.json()["error"]["code"] == "operation_conflict"
                cancelled = await client.post(
                    f"/v1/scopes/{SCOPE}/operations/{memory.work.work_id}/cancel",
                    json={"expected_version": memory.work.state_version},
                )
                assert cancelled.status_code == 200, cancelled.text
                assert cancelled.json()["status"] == "cancelled"
                assert cancelled.json()["cancel_requested"] is True
                assert cancelled.json()["state_version"] == memory.work.state_version + 1

                # A cancelled operation cannot be retried; a failed one can.
                assert (
                    await client.post(
                        f"/v1/scopes/{SCOPE}/operations/{memory.work.work_id}/retry",
                        json={"expected_version": cancelled.json()["state_version"]},
                    )
                ).status_code == 409

                async with contexts.database.transaction() as connection:
                    claims = await repository.claim(
                        connection,
                        worker_id="test-worker",
                        supported={EXPERIENCE_WORK_KIND: frozenset({1})},
                        lease_seconds=30,
                        limit=1,
                    )
                    assert len(claims) == 1
                    failed = await repository.fail(
                        connection,
                        claims[0],
                        _failure(),
                        retry_delay_seconds=0,
                    )
                    assert failed.status is WorkStatus.FAILED
                retried = await client.post(
                    f"/v1/scopes/{SCOPE}/operations/{experience.work.work_id}/retry",
                    json={"expected_version": failed.state_version},
                )
                assert retried.status_code == 200, retried.text
                assert retried.json()["status"] == "queued"
                assert retried.json()["error_category"] is None
                assert retried.json()["error_code"] is None

    asyncio.run(scenario())


def _failure():
    from powercontext.builtin.persistence.work import WorkFailure

    return WorkFailure(category="generation", code="provider_unavailable", retryable=False)


def test_operation_routes_require_a_composed_ledger() -> None:
    app = create_app(application=SimpleNamespace(operations=None))
    transport = httpx.ASGITransport(app=app)

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=transport, base_url="http://server") as client:
            response = await client.get(f"/v1/scopes/{SCOPE}/operations")
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "runtime_not_ready"

    asyncio.run(scenario())


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__]))
