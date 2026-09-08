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


"""Profile and subject convenience operations through the real HTTP stack."""

import asyncio
import sqlite3

import httpx
import pytest
from pydantic import SecretStr

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings


class Generator:
    async def generate(self, value):
        return "# Profile\n\n- Prefers Chinese."


@pytest.mark.parametrize("enforced", [False, True])
def test_subject_dual_write_preserves_single_scope_api_and_authorization(tmp_path, enforced):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'subject.db'}"),
            auth=BearerAuthConfig(enabled=enforced, token=SecretStr("test-subject-token") if enforced else None),
            access=AccessControlConfig(mode="enforced" if enforced else "disabled"),
            mcp=McpConfig(enabled=False),
        )
    )

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test-subject-token"} if enforced else {},
            ) as client,
        ):
            sid = (await client.get("/v1/scopes/default")).json()["scope_id"]
            path = f"/v1/scopes/{sid}"
            rejected = await client.post(path + "/sources", json={"content": "one", "subject_key": "U1"})
            assert rejected.status_code == 422
            response = await client.post(
                path + "/subject-sources", json={"subject_key": "U1", "content": {"speaker": "U1", "text": "Chinese"}}
            )
            assert response.status_code == 201, response.text
            body = response.json()
            target = body["subject_scope_id"]
            first, second = body["sources"]
            assert first["source_id"] == second["source_id"]
            assert first["content_digest"] == second["content_digest"]
            assert first["scope_id"] == sid and second["scope_id"] == target != sid
            policy = await client.get(f"/v1/scopes/{target}/profile-policy")
            assert policy.status_code == 200, policy.text
            assert policy.json()["generation_enabled"] is True
            conflict = await client.post(
                path + "/subject-sources", json={"subject_key": "U1", "subject_scope_id": sid, "content": "conflict"}
            )
            assert conflict.status_code == 409
            assert conflict.json()["error"]["code"] == "subject_scope_conflict"
            exact = await client.post(
                "/v1/scope-bindings/resolve",
                json={
                    "binding_keys": [{"integration": "subject", "kind": "user", "external_id": "missing"}],
                    "allow_default": False,
                },
            )
            assert exact.status_code == 404
            if enforced:
                explicit = await client.post(
                    "/v1/scopes",
                    json={"title": "Explicit subject", "summary": "Explicit subject", "idempotency_key": "explicit"},
                )
                assert explicit.status_code == 201, explicit.text
                explicit_scope = explicit.json()["scope_id"]
                direct = await client.post(
                    path + "/subject-sources",
                    json={"subject_key": "U2", "subject_scope_id": explicit_scope, "content": "Direct"},
                )
                assert direct.status_code == 201, direct.text

    asyncio.run(run())


@pytest.mark.parametrize("explicit_target", [False, True])
def test_subject_dual_write_rejects_revoked_target_contribution(tmp_path, explicit_target):
    database_path = tmp_path / "revoked-subject.db"
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path}"),
            auth=BearerAuthConfig(enabled=True, token=SecretStr("test-subject-token")),
            access=AccessControlConfig(mode="enforced"),
            mcp=McpConfig(enabled=False),
        )
    )

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer test-subject-token"},
            ) as client,
        ):
            group = (await client.get("/v1/scopes/default")).json()["scope_id"]
            payload = {"subject_key": "U1", "content": "initial evidence"}
            if explicit_target:
                created = await client.post(
                    "/v1/scopes",
                    json={"title": "Subject", "summary": "Subject", "idempotency_key": "subject"},
                )
                assert created.status_code == 201, created.text
                payload["subject_scope_id"] = created.json()["scope_id"]
            path = f"/v1/scopes/{group}/subject-sources"
            first = await client.post(path, json=payload)
            assert first.status_code == 201, first.text
            target = first.json()["subject_scope_id"]
            # Materialize the static preset before revoking every contribution grant.
            scope = await client.get(f"/v1/scopes/{target}")
            assert scope.status_code == 200, scope.text
            bindings = await client.post(
                "/v1/access/bindings/list",
                json={"management_resource": {"type": "scope", "scope_id": target}, "role": "scope.contributor"},
            )
            assert bindings.status_code == 200, bindings.text
            assert bindings.json()["items"]
            for binding in bindings.json()["items"]:
                revoked = await client.post(
                    "/v1/access/bindings/revoke",
                    json={
                        "binding_id": binding["binding_id"],
                        "expected_version": binding["version"],
                        "idempotency_key": "revoke-" + binding["binding_id"],
                    },
                )
                assert revoked.status_code == 200, revoked.text
            direct = await client.post(f"/v1/scopes/{target}/sources", json={"content": "forbidden direct"})
            assert direct.status_code == 403, direct.text
            with sqlite3.connect(database_path) as connection:
                before = connection.execute("SELECT * FROM pc_sources ORDER BY scope_id, source_id").fetchall()
                journal_before = connection.execute(
                    "SELECT * FROM pc_source_journal_heads ORDER BY scope_id"
                ).fetchall()
            denied = await client.post(path, json={**payload, "content": "forbidden dual write"})
            assert denied.status_code == 403, denied.text
            with sqlite3.connect(database_path) as connection:
                after = connection.execute("SELECT * FROM pc_sources ORDER BY scope_id, source_id").fetchall()
                journal_after = connection.execute("SELECT * FROM pc_source_journal_heads ORDER BY scope_id").fetchall()
            assert after == before
            assert journal_after == journal_before

    asyncio.run(run())


@pytest.mark.parametrize("enforced", [False, True])
def test_profile_http_policy_crud_review_and_rollback(tmp_path, enforced):
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'profile.db'}"),
            auth=BearerAuthConfig(enabled=enforced, token=SecretStr("profile-test-token") if enforced else None),
            access=AccessControlConfig(mode="enforced" if enforced else "disabled"),
            mcp=McpConfig(enabled=False),
        )
    )

    async def run():
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://test",
                headers={"Authorization": "Bearer profile-test-token"} if enforced else {},
            ) as client,
        ):
            sid = (await client.get("/v1/scopes/default")).json()["scope_id"]
            path = f"/v1/scopes/{sid}"
            policy = await client.put(
                path + "/profile-policy",
                json={
                    "generation_enabled": True,
                    "activation_mode": "review_required",
                    "expected_version": 0,
                },
            )
            assert policy.status_code == 200, policy.text
            assert policy.json()["version"] == 1
            app.state.application.profiles.generator = Generator()
            await client.post(path + "/sources", json={"content": "Chinese please"})
            pending = await client.post("/v1/profile/flush", json={"scope_id": sid})
            assert pending.status_code == 200, pending.text
            candidate_id = pending.json()["candidate_id"]
            assert candidate_id
            approved = await client.post(
                "/v1/artifact-candidates/approve",
                json={
                    "scope_id": sid,
                    "candidate_id": candidate_id,
                    "expected_version": 1,
                },
            )
            assert approved.status_code == 200, approved.text
            artifact_path = path + "/artifacts/profile/profile"
            first = await client.get(artifact_path)
            assert first.status_code == 200
            assert first.json()["content"]["generation"]["mode"] == "review_approved"
            if enforced:
                resource = {
                    "type": "artifact",
                    "scope_id": sid,
                    "identity": {"family": "profile", "artifact_id": "profile"},
                    "selector": None,
                }
                granted = await client.post(
                    "/v1/access/bindings/create",
                    json={
                        "subject": {"type": "user", "id": "profile-reader"},
                        "resource": resource,
                        "role": "artifact.viewer",
                        "idempotency_key": "profile-reader",
                    },
                )
                assert granted.status_code == 201, granted.text
                for payload in (
                    {"action": "artifact.read", "resource_type": "artifact", "family": "profile"},
                    {"action": "artifact.read", "resource_type": "artifact"},
                ):
                    discovered = await client.post("/v1/access/resources/list", json=payload)
                    assert discovered.status_code == 200, discovered.text
                    assert resource in discovered.json()["items"]
            duplicate = await client.post(
                path + "/artifacts", json={"family": "profile", "content": {"content": "# Duplicate"}}
            )
            assert duplicate.status_code == 409, duplicate.text
            replaced = await client.put(
                artifact_path, headers={"If-Match": first.headers["ETag"]}, json={"content": {"content": "# Manual"}}
            )
            assert replaced.status_code == 200, replaced.text
            rollback = await client.put(
                artifact_path,
                headers={"If-Match": replaced.headers["ETag"]},
                json={
                    "content": {"content": first.json()["content"]["content"], "restored_from_revision": 1},
                },
            )
            assert rollback.status_code == 200, rollback.text
            assert rollback.json()["revision"] == 3
            assert rollback.json()["content"]["generation"]["mode"] == "rollback"
            assert (await client.get(artifact_path + "/revisions/1")).json()["content"] == first.json()["content"]

    asyncio.run(run())
