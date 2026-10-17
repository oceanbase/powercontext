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

"""Jev injection through persisted Atomic Memory, HTTP authorization, and preparation."""

from __future__ import annotations

import asyncio
import json
from typing import cast
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from starlette.middleware import Middleware

from powercontext.builtin.artifacts.memory import JevMemoryReranker, JevRerankConfig
from powercontext.builtin.inference.errors import InferenceTimeoutError, InferenceUnavailableError
from powercontext.builtin.runtime import BuiltinConfig, RuntimeConfig, open_builtin_runtime
from powercontext.server.app import ServerApplication, create_app
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.middleware import AuthenticationMiddleware
from tests.e2e.test_access_control_regressions import ADMIN, _Authentication, _grant
from tests.e2e.test_atomic_memory_read_snapshot import database as jev_database

database = jev_database


@pytest.mark.parametrize("failure", [None, "unavailable", "timeout"])
def test_jev_search_and_prepare_preserve_identity_authorization_and_budget(database, tmp_path, failure):  # noqa: C901
    calls = []
    efforts = []

    async def respond(request):
        text = json.loads(request.content)["state"]["candidate"]
        calls.append(text)
        if failure == "timeout":
            await asyncio.Event().wait()
        if failure == "unavailable":
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "model": "jev-1.13.0",
                "answers": {"useful": {"type": "noul", "noul": 0.9 if "exact" in text else 0.2}},
                "usage": {"input_tokens": 10, "output_tokens": 1},
            },
        )

    async def record_effort(effort):
        efforts.append(effort)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as provider:
            reranker = JevMemoryReranker(
                JevRerankConfig(
                    endpoint="https://api.typesafe.ai/v1/systemone",
                    model="jev-1.13.0",
                    api_key=SecretStr("test-key"),
                    timeout_seconds=0.03 if failure == "timeout" else 30,
                ),
                provider,
            )
            async with (
                open_builtin_access_control(database, bootstrap_administrators=(ADMIN,), deployment_id="jev") as access,
                open_builtin_runtime(
                    BuiltinConfig(
                        database=database,
                        runtime=RuntimeConfig(
                            artifact_processing_families=(),
                            memory_rerank_candidate_limit=3,
                            recall_gate_enabled=True,
                        ),
                    ),
                    memory_reranker=reranker,
                    recall_effort_sink=record_effort,
                    scheduler_path=tmp_path / "jev-scheduler.db",
                ) as runtime,
            ):
                assert runtime.atomic_memory is not None
                authentication = _Authentication()
                app = create_app(
                    application=cast(ServerApplication, runtime),
                    access_control=access,
                    access_mode="enforced",
                    authentication_provider=authentication,
                    middleware=(Middleware(AuthenticationMiddleware, provider=authentication),),
                )
                async with (
                    app.router.lifespan_context(app),
                    httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client,
                ):
                    scopes = []
                    for index in range(2):
                        response = await client.post(
                            "/v1/scopes",
                            json={
                                "title": f"Jev scope {index}",
                                "summary": "Evidence isolation",
                                "idempotency_key": str(uuid4()),
                            },
                        )
                        assert response.status_code == 201, response.text
                        scopes.append(response.json()["scope_id"])
                    scope, other = scopes
                    for scope_id, text in (
                        (scope, "project broad compatibility note"),
                        (scope, "project exact compatibility constraint"),
                        (other, "project other scope private body"),
                    ):
                        response = await client.post(
                            "/v1/memory/remember",
                            json={
                                "scope_id": scope_id,
                                "kind": "fact",
                                "text": text,
                            },
                        )
                        assert response.status_code == 200, response.text
                    await _grant(client, scope, "viewer", "scope.viewer")
                    before = await runtime.atomic_memory.for_scope(scope).list()
                    selected = None
                    if failure is None:
                        page = await runtime.atomic_memory.for_scope(scope).search("project", mode="text", limit=1)
                        assert page.rerank is not None and not page.rerank.used_fallback
                        assert len(page.hits) == 1
                        selected = page.hits[0]
                        assert selected in page.rerank.candidate_hits
                        exact = await runtime.atomic_memory.for_scope(scope).get(
                            selected.hit.artifact_ref.artifact_id,
                            revision=selected.hit.artifact_ref.revision,
                        )
                        assert exact.ref == selected.hit.artifact_ref
                        assert exact.artifact.content.text == selected.text
                        assert selected.text == "project exact compatibility constraint"
                    else:
                        with pytest.raises(
                            InferenceTimeoutError if failure == "timeout" else InferenceUnavailableError
                        ):
                            await runtime.atomic_memory.for_scope(scope).search("project", mode="text", limit=1)
                    viewer = {"Authorization": "Bearer viewer"}
                    count = len(calls)
                    denied = await client.post(
                        "/v1/memory/search",
                        headers=viewer,
                        json={
                            "scope_id": other,
                            "query": "project",
                            "mode": "fts",
                        },
                    )
                    assert denied.status_code == 403 and len(calls) == count
                    if failure is not None:
                        failed_search = await client.post(
                            "/v1/memory/search",
                            headers=viewer,
                            json={"scope_id": scope, "query": "project", "mode": "fts", "limit": 1},
                        )
                        assert failed_search.status_code == 503, failed_search.text
                        assert failed_search.json()["error"]["code"] == f"inference_{failure}"
                    prepared = await client.post(
                        "/v1/context/prepare",
                        headers=viewer,
                        json={
                            "scope_id": scope,
                            "query": "project",
                            "max_bytes": 1024,
                            "assembly": {"sections": [{"family": "memory", "limit": 1}]},
                        },
                    )
                    if failure is None:
                        assert prepared.status_code == 200, prepared.text
                        assert selected is not None
                        body = prepared.json()
                        assert body["content"] is not None
                        assert body["content_bytes"] == len(body["content"].encode("utf-8")) <= 1024
                        assert "other scope private" not in body["content"]
                        assert selected.hit.artifact_ref.artifact_id in body["content"]
                        assert len(efforts) == 1 and efforts[0].rounds == 1
                        assert efforts[0].assessment == "rerank-enabled"
                    else:
                        assert prepared.status_code == 503, prepared.text
                        assert prepared.json()["error"]["code"] == f"inference_{failure}"
                    after = await runtime.atomic_memory.for_scope(scope).list()
                    assert before.items == after.items
                    assert all("other scope private" not in text for text in calls)

    asyncio.run(scenario())
