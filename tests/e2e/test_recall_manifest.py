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

"""Metadata-only bounded recall over real SQLite storage."""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import Engine, event

from tests.e2e.test_access_control_regressions import _grant, _scope, _server


def test_manifest_is_bounded_metadata_and_refs_are_readable(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (app, client, _):
            scope = await _scope(client)
            empty = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert empty.status_code == 200, empty.text
            assert empty.json() == {"items": [], "next_cursor": None, "code": "empty_inventory"}
            for number in range(3):
                response = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={
                        "family": "atomic-memory",
                        "content": {
                            "kind": "decision",
                            "text": f"Full private body {number}",
                            "description": f"Summary {number}",
                        },
                    },
                )
                assert response.status_code == 201, response.text
            prepare_request = {
                "scope_id": scope,
                "query": "private body",
                "assembly": {"sections": [{"family": "memory", "limit": 8}]},
            }
            prepared_before = await client.post("/v1/context/prepare", json=prepare_request)
            assert prepared_before.status_code == 200, prepared_before.text
            statements = []

            def capture(connection, cursor, statement, parameters, context, executemany):
                statements.append(statement.lower())

            event.listen(Engine, "before_cursor_execute", capture)
            try:
                response = await client.post("/v1/recall/manifest", json={"scope_id": scope, "limit": 2})
            finally:
                event.remove(Engine, "before_cursor_execute", capture)
            assert any("pc_atomic_memory_current.description" in sql for sql in statements)
            assert not any(
                forbidden in sql
                for sql in statements
                for forbidden in ("pc_artifacts", "searchable_text", "pc_atomic_memory_current.text", ".embedding")
            )
            assert response.status_code == 200, response.text
            first = response.json()
            assert first["code"] == "page"
            assert len(first["items"]) == 2
            assert first["next_cursor"]
            assert "Full private body" not in response.text
            assert first["items"][0]["description"].startswith("Summary")
            import httpx
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            from powercontext.server.mcp import create_mcp_server

            mcp_app = create_mcp_server(app).http_app(path="/")
            app.mount("/manifest-test-mcp", mcp_app)

            def http_client_factory(headers=None, timeout=None, auth=None, **kwargs):
                return httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app),
                    base_url="http://test",
                    headers=headers,
                    timeout=timeout,
                    auth=auth,
                    follow_redirects=True,
                )

            transport = StreamableHttpTransport(
                "http://test/manifest-test-mcp/",
                httpx_client_factory=http_client_factory,
            )
            async with mcp_app.lifespan(mcp_app), Client(transport) as mcp:
                via_mcp = await mcp.call_tool("get_recall_manifest", {"scope_id": scope, "limit": 2})
                assert via_mcp.structured_content["items"] == first["items"]
                assert via_mcp.structured_content["code"] == "page"
            second = await client.post(
                "/v1/recall/manifest",
                json={
                    "scope_id": scope,
                    "limit": 2,
                    "cursor": first["next_cursor"],
                },
            )
            assert second.status_code == 200, second.text
            assert len(second.json()["items"]) == 1
            assert second.json()["next_cursor"] is None
            refs = [item["ref"]["artifact_id"] for item in first["items"] + second.json()["items"]]
            assert len(set(refs)) == 3
            ref = first["items"][0]["ref"]
            exact = await client.get(
                f"/v1/scopes/{scope}/artifacts/atomic-memory/{ref['artifact_id']}/revisions/{ref['revision']}"
            )
            assert exact.status_code == 200, exact.text
            assert exact.json()["content"]["description"] == first["items"][0]["description"]
            changed_filter = await client.post(
                "/v1/recall/manifest",
                json={
                    "scope_id": scope,
                    "kind": "fact",
                    "cursor": first["next_cursor"],
                },
            )
            assert changed_filter.status_code == 400, changed_filter.text
            denied = await client.post(
                "/v1/recall/manifest", json={"scope_id": scope}, headers={"Authorization": "Bearer stranger"}
            )
            assert denied.status_code == 403, denied.text
            bad = await client.post("/v1/recall/manifest", json={"scope_id": scope, "limit": 101})
            assert bad.status_code == 422
            assert bad.json()["error"]["code"]
            prepared_after = await client.post("/v1/context/prepare", json=prepare_request)
            assert prepared_after.status_code == 200, prepared_after.text
            assert prepared_after.content == prepared_before.content

    asyncio.run(scenario())


@pytest.mark.parametrize("configured_model", [False, True])
def test_explicit_writes_preserve_descriptions_without_model_requests(tmp_path, monkeypatch, configured_model):
    from pydantic_ai.messages import ModelResponse, TextPart
    from pydantic_ai.models.function import FunctionModel

    from powercontext.builtin.runtime import InferenceConfig, composition

    writing = False

    def unexpected_generation(messages, info):
        if writing:
            pytest.fail("Explicit memory writes must not request model generation")
        return ModelResponse(parts=[TextPart("OK")])

    model = FunctionModel(unexpected_generation)

    async def open_local_model(*args, **kwargs):
        return model, model

    monkeypatch.setattr(composition, "_open_pydantic_ai_model", open_local_model)

    async def scenario():
        nonlocal writing
        inference = InferenceConfig(generation_model="test") if configured_model else InferenceConfig()
        async with _server(tmp_path, inference=inference) as (app, client, _):
            writing = True
            scope = await _scope(client)
            created = await client.post(
                "/v1/memory/remember",
                json={
                    "scope_id": scope,
                    "kind": "decision",
                    "text": "Use JWT for login.",
                },
            )
            assert created.status_code == 200, created.text
            manifest = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert manifest.status_code == 200, manifest.text
            entry = manifest.json()["items"][0]
            assert entry["description"] == "Use JWT for login."
            ref = entry["ref"]
            path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{ref['artifact_id']}"
            current = await client.get(path)
            revised = await client.put(
                path,
                headers={"If-Match": current.headers["ETag"]},
                json={
                    "content": {"kind": "decision", "text": "Use a shorter JWT expiry."},
                },
            )
            assert revised.status_code == 200, revised.text
            assert revised.json()["content"]["description"] == "Use a shorter JWT expiry."
            assert revised.json()["content"]["text"] == "Use a shorter JWT expiry."
            old = await client.get(path + "/revisions/1")
            assert old.status_code == 200
            assert old.json()["content"]["text"] == "Use JWT for login."
            assert old.json()["content"]["description"] == "Use JWT for login."
            stale = await client.put(
                path,
                headers={"If-Match": current.headers["ETag"]},
                json={"content": {"kind": "decision", "text": "Stale change"}},
            )
            assert stale.status_code in {409, 412}, stale.text

            other = await client.post(
                "/v1/memory/remember",
                json={
                    "scope_id": scope,
                    "kind": "decision",
                    "text": "Keep refresh tokens.",
                    "description": "Refresh token policy",
                },
            )
            assert other.status_code == 200, other.text
            from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent

            generic = app.state.application.artifact_merge.for_scope(scope, "atomic-memory")
            first = await generic.get(ref["artifact_id"])
            second = await generic.get(other.json()["records"][0]["artifact"]["artifact_id"])
            assert second.artifact.content.description == "Refresh token policy"
            merged = await generic.merge(
                (first.as_read(), second.as_read()),
                AtomicMemoryContent(kind="decision", text="Use JWT and retain refresh tokens."),
            )
            assert merged.primary.artifact.content.description == "Use JWT and retain refresh tokens."
            final_manifest = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert len(final_manifest.json()["items"]) == 1
            assert final_manifest.json()["items"][0]["description"] == "Use JWT and retain refresh tokens."

    asyncio.run(scenario())


def test_projection_rebuild_copies_authoritative_description(tmp_path):
    import sqlite3

    from powercontext.builtin.runtime.atomic_memory_rebuild import rebuild_atomic_memory_projection

    async def scenario():
        async with _server(tmp_path) as (app, client, _):
            scope = await _scope(client)
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={
                    "family": "atomic-memory",
                    "content": {
                        "kind": "fact",
                        "text": "Historical authoritative body",
                        "description": "Stored summary",
                    },
                },
            )
            assert created.status_code == 201, created.text
            identifier = created.json()["artifact_id"]
            exact_path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{identifier}/revisions/1"
            before = await client.get(exact_path)
            assert before.status_code == 200
            with sqlite3.connect(tmp_path / "regressions.db") as connection:
                connection.execute("UPDATE pc_atomic_memory_current SET description = NULL")
            manifest = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert manifest.status_code == 200, manifest.text
            assert manifest.json()["items"][0]["description"] is None
            application = app.state.application.atomic_memory._application
            report = await rebuild_atomic_memory_projection(
                application.database,
                application.index,
                maintenance_confirmed=True,
            )
            assert report.ready, report
            rebuilt = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert rebuilt.json()["items"][0]["description"] == "Stored summary"
            after = await client.get(exact_path)
            assert after.content == before.content
        async with _server(tmp_path) as (_, client, _):
            manifest = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert manifest.json()["items"][0]["description"] == "Stored summary"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ("登录采用 JWT。\n\t刷新令牌有效期为 7 天。", "登录采用 JWT。 刷新令牌有效期为 7 天。"),
        ("记忆" * 120, "记忆" * 99 + "记…"),
        ("x" * 200, "x" * 200),
    ],
    ids=["multiline", "truncated-unicode", "exact-limit"],
)
def test_default_description_is_a_bounded_single_line_persisted_on_write(tmp_path, body, expected):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope = await _scope(client)
            created = await client.post(
                f"/v1/scopes/{scope}/artifacts",
                json={"family": "atomic-memory", "content": {"kind": "fact", "text": body}},
            )
            assert created.status_code == 201, created.text
            identifier = created.json()["artifact_id"]
        async with _server(tmp_path) as (_, client, _):
            exact = await client.get(f"/v1/scopes/{scope}/artifacts/atomic-memory/{identifier}/revisions/1")
            assert exact.status_code == 200, exact.text
            assert exact.json()["content"]["description"] == expected
            assert exact.json()["content"]["text"] == body
            manifest = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert manifest.json()["items"][0]["description"] == expected

    asyncio.run(scenario())


@pytest.mark.parametrize("description", [" ", "first\nsecond", "x" * 201])
def test_invalid_supplied_description_is_rejected(tmp_path, description):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope = await _scope(client)
            response = await client.post(
                "/v1/memory/remember",
                json={"scope_id": scope, "kind": "fact", "text": "A new fact", "description": description},
            )
            assert response.status_code in {400, 422}, response.text
            manifest = await client.post("/v1/recall/manifest", json={"scope_id": scope})
            assert manifest.json()["items"] == []

    asyncio.run(scenario())


def test_manifest_cursor_rejects_a_different_unicode_kind(tmp_path):
    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope = await _scope(client)
            kind = "café"
            for number in range(2):
                created = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={"family": "atomic-memory", "content": {"kind": kind, "text": f"Rule {number}."}},
                )
                assert created.status_code == 201, created.text
            request = {"scope_id": scope, "kind": kind, "limit": 1}
            first = await client.post("/v1/recall/manifest", json=request)
            assert first.status_code == 200, first.text
            cursor = first.json()["next_cursor"]
            assert cursor is not None
            second = await client.post("/v1/recall/manifest", json={**request, "cursor": cursor})
            assert second.status_code == 200, second.text
            assert len(second.json()["items"]) == 1
            assert second.json()["items"][0]["ref"] != first.json()["items"][0]["ref"]
            assert second.json()["next_cursor"] is None
            different_kind = "cafe\u0301"
            empty = await client.post("/v1/recall/manifest", json={**request, "kind": different_kind})
            assert empty.status_code == 200, empty.text
            assert empty.json()["code"] == "empty_inventory"
            rejected = await client.post(
                "/v1/recall/manifest", json={**request, "kind": different_kind, "cursor": cursor}
            )
            assert rejected.status_code == 400, rejected.text
            assert rejected.json()["error"]["code"] == "invalid_cursor"

    asyncio.run(scenario())


@pytest.mark.parametrize("long_tags", [False, True], ids=["short-tag", "max-unicode-tags"])
def test_manifest_cursors_round_trip_with_maximum_filters(tmp_path, long_tags):
    from powercontext.http import RecallManifestRequest

    async def scenario():
        async with _server(tmp_path) as (_, client, _):
            scope = await _scope(client)
            tags = ["界" * 63 + chr(97 + index) for index in range(16)] if long_tags else ["release"]
            kind = "类" * 128
            identifiers = set()
            for number in range(2):
                created = await client.post(
                    f"/v1/scopes/{scope}/artifacts",
                    json={"family": "atomic-memory", "content": {"kind": kind, "text": f"Release rule {number}."}},
                )
                assert created.status_code == 201, created.text
                identifier = created.json()["artifact_id"]
                identifiers.add(identifier)
                path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{identifier}/tags"
                current = await client.get(path)
                tagged = await client.put(path, json={"tags": tags[:1]}, headers={"If-Match": current.headers["ETag"]})
                assert tagged.status_code == 200, tagged.text
            request = {"scope_id": scope, "kind": kind, "tags": tags, "tag_match": "any", "limit": 1}
            first = await client.post("/v1/recall/manifest", json=request)
            assert first.status_code == 200, first.text
            cursor = first.json()["next_cursor"]
            assert cursor is not None and len(cursor) <= 4096
            next_request = RecallManifestRequest.model_validate({**request, "cursor": cursor})
            second = await client.post(
                "/v1/recall/manifest", json=next_request.model_dump(mode="json", exclude_none=True)
            )
            assert second.status_code == 200, second.text
            assert second.json()["next_cursor"] is None
            found = [item["ref"]["artifact_id"] for item in first.json()["items"] + second.json()["items"]]
            assert len(found) == len(set(found)) == 2 and set(found) == identifiers
            other = await client.post(
                "/v1/scopes",
                json={"title": "Other scope", "summary": "Cursor isolation", "idempotency_key": "cursor-other-scope"},
            )
            assert other.status_code == 201, other.text
            for changed in (
                {"tags": ["different"]},
                {"tag_match": "all"},
                {"kind": "fact"},
                {"scope_id": other.json()["scope_id"]},
            ):
                rejected = await client.post("/v1/recall/manifest", json={**request, "cursor": cursor, **changed})
                assert rejected.status_code == 400, rejected.text
            await _grant(client, scope, "stranger", "scope.viewer")
            rejected_subject = await client.post(
                "/v1/recall/manifest", json={**request, "cursor": cursor}, headers={"Authorization": "Bearer stranger"}
            )
            assert rejected_subject.status_code == 400, rejected_subject.text
            tampered = await client.post("/v1/recall/manifest", json={**request, "cursor": cursor + "tampered"})
            assert tampered.status_code == 400, tampered.text
            wrong_endpoint = await client.post("/v1/atomic-memory/list", json={**request, "cursor": cursor})
            assert wrong_endpoint.status_code == 400, wrong_endpoint.text

    asyncio.run(scenario())
