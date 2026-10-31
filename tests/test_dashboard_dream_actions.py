# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#     http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Exercise Dashboard actions against durable Dream and recovery APIs."""

import asyncio
import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig
from powercontext.server.app import create_app
from powercontext.server.authz import PrincipalRef
from powercontext.server.dashboard.routes import router
from tests.builtin.runtime.test_topic_memory_terminal_frontier import _seed
from tests.e2e.dream_support import atomic_memory_pipeline, open_dream_runtime, process_pending
from tests.e2e.test_artifact_dreaming import Generator, MemoryPipeline, seed
from tests.test_dashboard import create_scope
from tests.test_dashboard import dashboard as dashboard
from tests.test_dashboard_scope_selection import _grant


def test_dashboard_dream_runs_are_durable_idempotent_and_finish_in_review(tmp_path):
    async def scenario():
        generator = Generator()
        async with open_dream_runtime(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/dream.db")),
            candidate_pipeline=atomic_memory_pipeline(MemoryPipeline()),
            dream_generator=generator,
        ) as runtime:
            scope, _, memory = await seed(runtime)
            app = create_app(application=runtime)
            app.include_router(router, prefix="/dashboard")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                ref = memory.model_dump(mode="json")
                for index, family in enumerate(("experience", "skill")):
                    kind = "atomic-memory" if family == "experience" else "experience"
                    panel = await client.get(
                        "/dashboard/methods",
                        params={
                            "scope": scope,
                            "kind": family,
                            "generate": "1",
                            "lang": "en",
                        },
                    )
                    assert panel.status_code == 200, panel.text
                    assert '<option value="source"' not in panel.text
                    assert 'name="reason"' not in panel.text
                    assert ">Start task</button>" in panel.text
                    assert ">Generate asynchronously</button>" not in panel.text
                    assert "data-selected-count" in panel.text
                    assert 'class="record-list"' not in panel.text
                    html_match = re.search(r'name="idempotency_key" value="([^"]+)"', panel.text)
                    assert html_match is not None
                    key = unescape(html_match[1])
                    payload = {
                        "scope": scope,
                        "kind": family,
                        "evidence_type": kind,
                        "evidence": json.dumps(ref),
                        "idempotency_key": key,
                    }
                    response = await client.post(
                        "/dashboard/methods/dream?lang=en", data=payload, headers={"Origin": "http://testserver"}
                    )
                    assert response.status_code == 303, response.text
                    location = response.headers["location"]
                    assert parse_qs(urlsplit(location).query)["record_type"] == ["dreams"]
                    duplicate = await client.post(
                        "/dashboard/methods/dream?lang=en", data=payload, headers={"Origin": "http://testserver"}
                    )
                    assert duplicate.headers["location"] == location
                    assert len(generator.inputs) == index
                    queued = await client.get(location)
                    assert 'hx-trigger="every 3s"' in queued.text
                    directory = await client.get(f"/v1/scopes/{scope}/dream")
                    assert len(directory.json()["runs"]) == index + 1
                    await process_pending(runtime)
                    completed = await client.get(location)
                    assert completed.status_code == 200, completed.text
                    assert 'hx-trigger="every 3s"' not in completed.text
                    assert "View candidate and review status" in completed.text
                    runs = (await client.get(f"/v1/scopes/{scope}/dream")).json()["runs"]
                    assert runs[0]["status"] == "succeeded" and runs[0]["candidate"] is not None
                    assert len(generator.inputs) == index + 1
                    if family == "experience":
                        candidate = runs[0]["candidate"]
                        approved = await client.post(
                            "/v1/candidates/approve",
                            json={
                                "scope_id": scope,
                                "candidate_id": candidate["candidate_id"],
                                "expected_version": candidate["version"],
                            },
                        )
                        assert approved.status_code == 200, approved.text
                        ref = approved.json()["result_artifact"]

    asyncio.run(scenario())


@pytest.mark.parametrize("family,kind", [("experience", "source"), ("skill", "atomic-memory")])
def test_dream_action_rejects_unsupported_evidence_without_creating_tasks(dashboard, family, kind):
    scope = create_scope(dashboard, "Unsupported Dream evidence")["scope_id"]
    response = dashboard.post(
        "/dashboard/methods/dream",
        data={"scope": scope, "kind": family, "evidence_type": kind, "evidence": "{}", "idempotency_key": "invalid"},
        headers={"Origin": "http://testserver"},
    )
    assert response.status_code == 422
    assert dashboard.get(f"/v1/scopes/{scope}/dream").json()["runs"] == []


def test_dream_action_rejects_cross_origin_and_scope_viewer(dashboard, monkeypatch):
    scope = create_scope(dashboard, "Dream permission")["scope_id"]
    memory = dashboard.post(
        "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Saved evidence."}
    ).json()["records"][0]["artifact"]
    data = {
        "scope": scope,
        "kind": "experience",
        "evidence_type": "atomic-memory",
        "evidence": json.dumps(memory),
        "idempotency_key": "viewer",
    }
    assert (
        dashboard.post("/dashboard/methods/dream", data=data, headers={"Origin": "https://foreign.example"}).status_code
        == 403
    )
    reader = {"type": "user", "id": "dream-reader"}
    _grant(dashboard, reader, {"type": "scope", "scope_id": scope}, "scope.viewer")
    monkeypatch.setattr(dashboard.app.state.authentication_provider, "_principal", PrincipalRef(**reader))
    assert (
        dashboard.post("/dashboard/methods/dream", data=data, headers={"Origin": "http://testserver"}).status_code
        == 403
    )


def test_dashboard_retry_uses_observed_block_and_preserves_permission_and_idempotency(tmp_path, monkeypatch):
    import threading

    from pydantic import AnyHttpUrl

    from powercontext.builtin.runtime import InferenceConfig
    from tests.e2e.test_access_control_regressions import ADMIN, _scope
    from tests.e2e.test_access_control_regressions import _grant as grant
    from tests.e2e.test_topic_memory_recovery_access import _recovery_server
    from tests.e2e.test_topic_memory_terminal_frontier import _Handler, _Provider

    monkeypatch.setenv("OPENAI_API_KEY", "local-test-provider")
    provider = _Provider(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        async with _recovery_server(
            tmp_path,
            "builtin",
            inference=InferenceConfig(
                generation_model="openai-chat:local-test",
                generation_base_url=AnyHttpUrl(f"http://127.0.0.1:{provider.server_port}/v1"),
            ),
        ) as (app, client):
            app.include_router(router, prefix="/dashboard")
            scope = await _scope(client)
            for role in ("scope.admin", "scope.contributor"):
                bound = await client.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": ADMIN.type, "id": ADMIN.id},
                        "resource": {"type": "scope", "scope_id": scope},
                        "role": role,
                        "idempotency_key": f"background-{role}",
                    },
                )
                assert bound.status_code == 201
            captured = await client.post(
                "/v1/sources/content", json={"scope_id": scope, "source_id": "blocked", "content": "retained evidence"}
            )
            assert captured.status_code == 202
            await _seed(app.state.application._provider.database, scope_id=scope)
            observed = await client.post("/v1/topic-memory/processing/status", json={"scope_id": scope})
            block = observed.json()["block"]
            assert block is not None
            sources = await client.get(
                "/dashboard/processing", params={"scope": scope, "record_type": "sources", "lang": "en"}
            )
            assert f'name="block_id" value="{block["block_id"]}"' not in sources.text
            attempts = await client.get(
                "/dashboard/processing", params={"scope": scope, "record_type": "attempts", "lang": "en"}
            )
            assert f'name="block_id" value="{block["block_id"]}"' in attempts.text
            data = {"scope": scope, "block_id": block["block_id"]}
            assert (
                await client.post(
                    "/dashboard/processing/retry", data=data, headers={"Origin": "https://foreign.example"}
                )
            ).status_code == 403
            await grant(client, scope, "reader", "scope.viewer")
            assert (
                await client.post(
                    "/dashboard/processing/retry",
                    data=data,
                    headers={"Origin": "http://test", "Authorization": "Bearer reader"},
                )
            ).status_code == 403
            response = await client.post(
                "/dashboard/processing/retry?lang=en", data=data, headers={"Origin": "http://test"}
            )
            assert response.status_code == 303, response.text
            assert "record_type=attempts" in response.headers["location"]
            duplicate = await client.post(
                "/dashboard/processing/retry?lang=en", data=data, headers={"Origin": "http://test"}
            )
            assert duplicate.status_code == 303 and duplicate.headers["location"] == response.headers["location"]
            opened = await client.get(response.headers["location"])
            assert "Retry accepted." in opened.text
            assert 'id="processing-status-heading"' in opened.text
            record = (
                await client.post(
                    "/v1/topic-memory/processing/blocks/get", json={"scope_id": scope, "block_id": block["block_id"]}
                )
            ).json()
            assert record["retried_at"] is not None and record["attempts"] == 3
            stale = await client.post(
                "/dashboard/processing/retry",
                data={"scope": scope, "block_id": "missing"},
                headers={"Origin": "http://test"},
            )
            assert stale.status_code == 404

    try:
        asyncio.run(scenario())
    finally:
        provider.shutdown()
        thread.join(timeout=5)
        provider.server_close()
