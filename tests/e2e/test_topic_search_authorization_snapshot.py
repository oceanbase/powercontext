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

"""Real backend HTTP searches authorize and read one snapshot after inference."""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult, InferenceUnavailableError
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.topic_memory import TopicMemoryRepository
from powercontext.builtin.runtime.config import DatabaseConfig, RuntimeConfig
from powercontext.server.authentication import AuthenticationRejectedError, AuthenticationResult, ProviderReadiness
from powercontext.server.authz import AccessAction, AccessDecision, PrincipalRef
from powercontext.server.authz.authzen import AuthZenAuthorizationProvider
from powercontext.server.authz.composition import open_builtin_access_control, open_casbin_access_control
from powercontext.server.authz.repository import RelationalAccessRepository
from powercontext.server.authz.service import (
    AccessControlService,
    AccessProviderCapabilities,
    BuiltinAuthorizationProvider,
)
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings

ADMIN = PrincipalRef(type="service", id="admin")
VIEWER = PrincipalRef(type="user", id="viewer")
VIEWER_HEADERS = {"Authorization": "Bearer viewer"}


class _CustomBuiltin(BuiltinAuthorizationProvider):
    def __init__(self, repository, policy):
        super().__init__(repository, deployment_id="topic-snapshot")
        self._policy = policy

    async def check_batch(self, requests, /):
        decisions = await super().check_batch(requests)
        return tuple(
            AccessDecision(False, "custom-denial", decision.policy_revision)
            if request.subject == VIEWER and request.action is AccessAction.SCOPE_READ and not self._policy["allowed"]
            else decision
            for request, decision in zip(requests, decisions, strict=True)
        )


class _IndependentAudit:
    def __init__(self, repository):
        self._repository = repository

    async def append_audit(self, event, /):
        return await self._repository.append_audit(event)

    async def list_audit(self, **kwargs):
        return await self._repository.list_audit(**kwargs)


class _CustomPolicyRepository(RelationalAccessRepository):
    def __init__(self, database, policy):
        super().__init__(database)
        self._policy = policy

    async def decision_snapshot(self, subjects, **kwargs):
        state = await super().decision_snapshot(subjects, **kwargs)
        return replace(state, bindings=()) if VIEWER in subjects and not self._policy["allowed"] else state


class _Authentication:
    async def authenticate(self, request):
        principal = {"Bearer admin": ADMIN, "Bearer viewer": VIEWER}.get(request.headers.get("authorization"))
        if principal is None:
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=principal)

    async def readiness(self):
        return ProviderReadiness(ready=True)


class _Embedding:
    profile = EmbeddingProfile(profile_id="topic-snapshot", model="test", dimension=3, normalization="unit")

    def __init__(self):
        self.started = asyncio.Event()
        self.resume = asyncio.Event()
        self.finished = asyncio.Event()
        self.pause_query = False
        self.unavailable = False

    async def embed(self, texts, /):
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))

    async def embed_query(self, texts, /):
        if self.pause_query:
            self.started.set()
            await self.resume.wait()
        if self.unavailable:
            raise InferenceUnavailableError("embed", "temporarily unavailable")
        result = await self.embed(texts)
        self.finished.set()
        return result


@asynccontextmanager
async def _isolated_database(backend: str, tmp_path: Path) -> AsyncIterator[DatabaseConfig]:
    if backend == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topics.db'}")
        return
    if backend == "seekdb":
        if importlib.util.find_spec("pylibseekdb") is None:
            pytest.skip("install powercontext[seekdb] for the real embedded backend")
        with TemporaryDirectory(prefix="pc-topic-", dir="/tmp") as directory:
            yield SeekDBConfig(path=Path(directory) / "db")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not configured_url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges")
    name = f"pc_topic_{uuid4().hex}"
    async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(configured_url)), tables=()) as profile:
        async with profile.database.transaction() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE `{name}`")
        try:
            url = make_url(configured_url).set(database=name).render_as_string(hide_password=False)
            yield OceanBaseConfig(url=SecretStr(url))
        finally:
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(f"DROP DATABASE `{name}`")


@asynccontextmanager
async def _server(
    tmp_path, provider, embedding, *, database=None, external_policy=None, access_factory=None, access_database=None
):
    database = database or SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'topics.db'}")
    opener = open_casbin_access_control if provider == "casbin" else open_builtin_access_control

    async def evaluate(request):
        payload = json.loads(request.content)

        def allowed(evaluation):
            return external_policy is None or evaluation["subject"]["id"] != VIEWER.id or external_policy["allowed"]

        if request.url.path.endswith("/evaluations"):
            return httpx.Response(200, json={"evaluations": [{"decision": allowed(p)} for p in payload["evaluations"]]})
        decision = allowed(payload)
        if (
            external_policy is not None
            and external_policy.get("pause_point")
            and payload["subject"]["id"] == VIEWER.id
            and payload["action"]["name"] == "scope.read"
            and embedding.finished.is_set()
        ):
            external_policy["decision_started"].set()
            await external_policy["resume_decision"].wait()
        return httpx.Response(200, json={"decision": decision})

    async with (
        opener(
            access_database or database, bootstrap_administrators=(ADMIN,), deployment_id="topic-snapshot"
        ) as access,
        httpx.AsyncClient(transport=httpx.MockTransport(evaluate)) as pdp,
    ):
        if external_policy is not None:
            access = AccessControlService(
                AuthZenAuthorizationProvider("http://127.0.0.1:9876", http_client=pdp),
                relationships=None,
                audit=access.audit,
                deployment_id="topic-snapshot",
                provider_capabilities=AccessProviderCapabilities(
                    safe_resource_filtering=False, multi_requirement_check=True, relationship_management=False
                ),
            )
        if access_factory is not None:
            access = await access_factory(access)
        app = create_server_app(
            settings=ServerSettings(
                database=database,
                runtime=RuntimeConfig(artifact_processing_families=()),
                access=AccessControlConfig(mode="enforced", deployment_id="topic-snapshot"),
                metrics=MetricsConfig(enabled=False),
                mcp=McpConfig(enabled=False),
            ),
            scheduler_path=tmp_path / "scheduler.db",
            authentication_provider=_Authentication(),
            access_control=access,
            embedding_model=embedding,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
                headers={"Authorization": "Bearer admin"},
            ) as client,
        ):
            yield client


def _content(label):
    return {"title": f"Alpha {label}", "summary": f"Alpha {label} summary", "detail": f"Alpha {label} complete detail."}


async def _create(client, scope_id, label):
    result = await client.post(
        f"/v1/scopes/{scope_id}/artifacts", json={"family": "topic-memory", "content": _content(label)}
    )
    assert result.status_code == 201, result.text
    return result


async def _seed(client, *, grant=True):
    created_scope = await client.post(
        "/v1/scopes", json={"title": "Snapshot", "summary": "Snapshot", "idempotency_key": "snapshot"}
    )
    assert created_scope.status_code == 201, created_scope.text
    scope_id = created_scope.json()["scope_id"]
    original = await _create(client, scope_id, "original")
    if not grant:
        return scope_id, original, None
    binding = await client.post(
        "/v1/access/bindings/create",
        json={
            "subject": {"type": VIEWER.type, "id": VIEWER.id},
            "resource": {"type": "scope", "scope_id": scope_id},
            "role": "scope.viewer",
            "idempotency_key": "viewer-scope",
        },
    )
    assert binding.status_code == 201, binding.text
    return scope_id, original, binding.json()


async def _revoke_and_write(client, scope_id, binding, original=None):
    revoked = await client.post(
        "/v1/access/bindings/revoke",
        json={
            "binding_id": binding["binding_id"],
            "expected_version": binding["version"],
            "idempotency_key": "revoke-viewer",
        },
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["state"] == "revoked"
    created = await _create(client, scope_id, "new secret")
    if original is not None:
        revised = await client.put(
            original.headers["Location"],
            headers={"If-Match": original.headers["ETag"]},
            json={"content": _content("revised secret")},
        )
        assert revised.status_code == 200, revised.text
        assert revised.json()["revision"] > original.json()["revision"]
    return created


async def _search(client, scope_id, surface, mode):
    if surface == "legacy":
        return await client.post(
            "/v1/topic-memory/search", headers=VIEWER_HEADERS, json={"scope_id": scope_id, "query": "alpha"}
        )
    return await client.post(
        f"/v1/scopes/{scope_id}/artifacts/topic-memory/search",
        headers=VIEWER_HEADERS,
        json={"query": "alpha", **({} if mode in {None, "fallback"} else {"mode": mode})},
    )


async def _assert_current_access_revoked(client, scope_id, surface, mode, created):
    fresh = await _search(client, scope_id, surface, mode)
    assert fresh.status_code == 403, fresh.text
    exact = await client.get(created.headers["Location"], headers=VIEWER_HEADERS)
    assert exact.status_code == 403, exact.text


def _injected_access(configuration, control):
    async def configure(original):
        provider = original.provider
        if configuration == "custom-provider":
            provider = _CustomBuiltin(original.relationships, control)
        elif configuration == "custom-repository":
            provider = BuiltinAuthorizationProvider(
                _CustomPolicyRepository(original.relationships._database, control), deployment_id="topic-snapshot"
            )
        return AccessControlService(
            provider,
            relationships=None if configuration == "no-relationships" else original.relationships,
            audit=_IndependentAudit(original.audit) if configuration == "independent-audit" else original.audit,
            deployment_id="topic-snapshot",
        )

    return configure


def _assert_original(result, surface, original, original_read):
    assert result.status_code == 200, result.text
    if surface == "artifact":
        results = result.json()["results"]
        assert len(results) == 1, result.text
        assert results[0]["artifact_id"] == original.json()["artifact_id"]
        assert results[0]["revision"] == original.json()["revision"]
        assert results[0]["content"] == original_read.json()["content"]
        assert results[0]["lineage"]["sources"] == original_read.json()["sources"]
        assert results[0]["lineage"]["artifacts"] == original_read.json()["artifacts"]
    else:
        hits = result.json()["hits"]
        assert len(hits) == 1, result.text
        assert hits[0]["artifact"]["artifact_id"] == original.json()["artifact_id"]
        assert hits[0]["artifact"]["revision"] == original.json()["revision"]
        assert hits[0]["title"] == _content("original")["title"]
        assert hits[0]["summary"] == _content("original")["summary"]


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("backend", ["sqlite", "oceanbase", "seekdb"])
@pytest.mark.parametrize(
    "surface,mode",
    [("artifact", "vector"), ("artifact", "hybrid"), ("artifact", None), ("artifact", "fallback"), ("legacy", None)],
)
def test_topic_search_rechecks_scope_access_after_embedding(tmp_path, provider, backend, surface, mode):
    async def scenario():
        embedding = _Embedding()
        async with (
            _isolated_database(backend, tmp_path) as database,
            _server(tmp_path, provider, embedding, database=database) as client,
        ):
            scope_id, _, binding = await _seed(client)
            embedding.pause_query = True
            embedding.unavailable = mode == "fallback"
            pending = asyncio.create_task(_search(client, scope_id, surface, mode))
            try:
                await asyncio.wait_for(embedding.started.wait(), timeout=20)
                # Both commits must finish while inference is still paused.
                created = await asyncio.wait_for(_revoke_and_write(client, scope_id, binding), timeout=20)
            finally:
                embedding.resume.set()
                result = await asyncio.wait_for(pending, timeout=20)
            assert result.status_code == 403, result.text
            await _assert_current_access_revoked(client, scope_id, surface, mode, created)

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["artifact", "legacy"])
def test_topic_search_preserves_authzen_point_reads_and_rechecks_after_embedding(tmp_path, surface):
    async def scenario():
        policy = {"allowed": True}
        embedding = _Embedding()
        async with _server(tmp_path, "authzen", embedding, external_policy=policy) as client:
            scope_id, _, _ = await _seed(client, grant=False)
            allowed = await _search(client, scope_id, surface, "hybrid")
            assert allowed.status_code == 200, allowed.text
            embedding.pause_query = True
            pending = asyncio.create_task(_search(client, scope_id, surface, "hybrid"))
            try:
                await asyncio.wait_for(embedding.started.wait(), timeout=20)
                policy["allowed"] = False
                created = await asyncio.wait_for(_create(client, scope_id, "new secret"), timeout=20)
            finally:
                embedding.resume.set()
                result = await asyncio.wait_for(pending, timeout=20)
            assert result.status_code == 403, result.text
            await _assert_current_access_revoked(client, scope_id, surface, "hybrid", created)

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["artifact", "legacy"])
@pytest.mark.parametrize("configuration", ["custom-provider", "custom-repository"])
def test_topic_search_preserves_injected_custom_point_denials(tmp_path, surface, configuration):
    async def scenario():
        control = {"allowed": True}
        embedding = _Embedding()
        async with _server(tmp_path, "builtin", embedding) as admin:
            scope_id, _, _ = await _seed(admin)
            async with _server(
                tmp_path, "builtin", embedding, access_factory=_injected_access(configuration, control)
            ) as client:
                embedding.pause_query = True
                pending = asyncio.create_task(_search(client, scope_id, surface, "hybrid"))
                try:
                    await asyncio.wait_for(embedding.started.wait(), timeout=20)
                    control["allowed"] = False
                    created = await asyncio.wait_for(_create(admin, scope_id, "new secret"), timeout=20)
                finally:
                    embedding.resume.set()
                    result = await asyncio.wait_for(pending, timeout=20)
                assert result.status_code == 403, result.text
                await _assert_current_access_revoked(client, scope_id, surface, "hybrid", created)

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["artifact", "legacy"])
@pytest.mark.parametrize("configuration", ["no-relationships", "independent-audit", "different-database"])
def test_topic_search_preserves_injected_builtin_point_reads(tmp_path, surface, configuration):
    async def scenario():
        control = {}
        embedding = _Embedding()
        access_database = (
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'separate-access.db'}")
            if configuration == "different-database"
            else None
        )
        async with _server(tmp_path, "builtin", embedding, access_database=access_database) as admin:
            scope_id, _, binding = await _seed(admin)
            async with _server(
                tmp_path,
                "builtin",
                embedding,
                access_factory=_injected_access(configuration, control),
                access_database=access_database,
            ) as client:
                allowed = await _search(client, scope_id, surface, "hybrid")
                assert allowed.status_code == 200, allowed.text
                embedding.pause_query = True
                pending = asyncio.create_task(_search(client, scope_id, surface, "hybrid"))
                try:
                    await asyncio.wait_for(embedding.started.wait(), timeout=20)
                    created = await asyncio.wait_for(_revoke_and_write(admin, scope_id, binding), timeout=20)
                finally:
                    embedding.resume.set()
                    result = await asyncio.wait_for(pending, timeout=20)
                assert result.status_code == 403, result.text
                await _assert_current_access_revoked(client, scope_id, surface, "hybrid", created)

    asyncio.run(scenario())


@pytest.mark.parametrize("surface", ["artifact", "legacy"])
def test_topic_search_pins_data_before_awaiting_authzen_point_read(tmp_path, surface):
    async def scenario():
        policy = {
            "allowed": True,
            "pause_point": True,
            "decision_started": asyncio.Event(),
            "resume_decision": asyncio.Event(),
        }
        embedding = _Embedding()
        async with _server(tmp_path, "authzen", embedding, external_policy=policy) as client:
            scope_id, original, _ = await _seed(client, grant=False)
            original_read = await client.get(original.headers["Location"], headers=VIEWER_HEADERS)
            assert original_read.status_code == 200, original_read.text
            embedding.pause_query = True
            pending = asyncio.create_task(_search(client, scope_id, surface, "hybrid"))
            try:
                await asyncio.wait_for(embedding.started.wait(), timeout=20)
                embedding.resume.set()
                await asyncio.wait_for(policy["decision_started"].wait(), timeout=20)
                policy["allowed"] = False
                created = await asyncio.wait_for(_create(client, scope_id, "new secret"), timeout=20)
                revised = await asyncio.wait_for(
                    client.put(
                        original.headers["Location"],
                        headers={"If-Match": original.headers["ETag"]},
                        json={"content": _content("revised secret")},
                    ),
                    timeout=20,
                )
                assert revised.status_code == 200, revised.text
            finally:
                embedding.resume.set()
                policy["resume_decision"].set()
                result = await asyncio.wait_for(pending, timeout=20)
            _assert_original(result, surface, original, original_read)
            await _assert_current_access_revoked(client, scope_id, surface, "hybrid", created)

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("backend", ["sqlite", "oceanbase", "seekdb"])
@pytest.mark.parametrize(
    "surface,mode", [("artifact", "text"), ("artifact", "vector"), ("artifact", "hybrid"), ("legacy", None)]
)
def test_topic_search_pins_authority_candidates_and_complete_content(
    tmp_path, provider, backend, surface, mode, monkeypatch
):
    async def scenario():
        async with (
            _isolated_database(backend, tmp_path) as database,
            _server(tmp_path, provider, _Embedding(), database=database) as client,
        ):
            scope_id, original, binding = await _seed(client)
            original_read = await client.get(original.headers["Location"], headers=VIEWER_HEADERS)
            assert original_read.status_code == 200, original_read.text
            authorized = asyncio.Event()
            resume = asyncio.Event()
            search = TopicMemoryRepository.search

            async def pause_candidates(*args, **kwargs):
                authorized.set()
                await resume.wait()
                return await search(*args, **kwargs)

            with monkeypatch.context() as patch:
                patch.setattr(TopicMemoryRepository, "search", pause_candidates)
                pending = asyncio.create_task(_search(client, scope_id, surface, mode))
                try:
                    await asyncio.wait_for(authorized.wait(), timeout=20)
                    # Writers commit after read authority is pinned, before candidate retrieval resumes.
                    created = await asyncio.wait_for(_revoke_and_write(client, scope_id, binding, original), timeout=20)
                finally:
                    resume.set()
                    result = await asyncio.wait_for(pending, timeout=20)
            _assert_original(result, surface, original, original_read)
            await _assert_current_access_revoked(client, scope_id, surface, mode, created)

    asyncio.run(scenario())
