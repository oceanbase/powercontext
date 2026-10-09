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

"""Search authorization and content share a snapshot after external inference."""

from __future__ import annotations

import asyncio
import importlib.util
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.engine import make_url

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference import EmbeddingResult
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.atomic_memory_security import AtomicMemoryExecutionContext, AtomicMemorySecurity
from powercontext.builtin.runtime.config import DatabaseConfig, RuntimeConfig
from powercontext.client import ForbiddenResponseError, PowerContextClient
from powercontext.http import (
    AtomicMemorySearchMode,
    CreateAccessBindingRequest,
    CreateScopeRequest,
    RememberMemoryRequest,
    RevokeAccessBindingRequest,
    SearchAtomicMemoryRequest,
)
from powercontext.server.authentication import (
    AuthenticationRejectedError,
    AuthenticationResult,
    ProviderReadiness,
)
from powercontext.server.authz import AccessAuditContext, AccessRole, CreateBinding, PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control, open_casbin_access_control
from powercontext.server.authz.service import AccessControlService
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, McpConfig, MetricsConfig, ServerSettings

ADMIN = PrincipalRef(type="service", id="admin")
VIEWER = PrincipalRef(type="user", id="viewer")
DEPLOYMENT_ID = "atomic-search-snapshot"


class _Authentication:
    async def authenticate(self, request) -> AuthenticationResult:
        principal = {f"Bearer {ADMIN.id}": ADMIN, f"Bearer {VIEWER.id}": VIEWER}.get(
            request.headers.get("authorization")
        )
        if principal is None:
            raise AuthenticationRejectedError
        return AuthenticationResult(subject=principal)

    async def readiness(self) -> ProviderReadiness:
        return ProviderReadiness(ready=True)


class _Embedding:
    profile = EmbeddingProfile(profile_id="snapshot", model="test", dimension=3, distance="l2", normalization="unit")

    def __init__(self) -> None:
        self.query_started = asyncio.Event()
        self.resume_query = asyncio.Event()
        self.pause_query = False

    async def embed(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        return EmbeddingResult(vectors=((1.0, 0.0, 0.0),) * len(texts))

    async def embed_query(self, texts: tuple[str, ...], /) -> EmbeddingResult:
        if self.pause_query:
            self.query_started.set()
            await self.resume_query.wait()
        return await self.embed(texts)


@asynccontextmanager
async def _isolated_database(backend: str, tmp_path: Path) -> AsyncIterator[DatabaseConfig]:
    if backend == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'atomic-search.db'}")
        return
    if backend == "seekdb":
        if importlib.util.find_spec("pylibseekdb") is None:
            pytest.skip("install powercontext[seekdb] for the real embedded backend")
        # Keep the embedded engine's Unix socket within sockaddr_un's limit.
        with TemporaryDirectory(prefix="pc-search-", dir="/tmp") as directory:
            yield SeekDBConfig(path=Path(directory) / "db")
        return
    configured_url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
    if not configured_url:
        pytest.skip("set POWERCONTEXT_TEST_OCEANBASE_URL with test database creation and deletion privileges")
    name = f"pc_search_{uuid4().hex}"
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
async def _server(database: DatabaseConfig, provider: str, tmp_path: Path, embedding: _Embedding):
    open_access = open_builtin_access_control if provider == "builtin" else open_casbin_access_control
    async with open_access(database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID) as access:
        app = create_server_app(
            settings=ServerSettings(
                database=database,
                runtime=RuntimeConfig(artifact_processing_families=()),
                access=AccessControlConfig(mode="enforced", deployment_id=DEPLOYMENT_ID),
                metrics=MetricsConfig(enabled=False),
                mcp=McpConfig(enabled=False),
            ),
            scheduler_path=tmp_path / "scheduler.db",
            access_control=access,
            authentication_provider=_Authentication(),
            embedding_model=embedding,
        )
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as transport,
            PowerContextClient(
                "http://testserver", token=ADMIN.id, http_client=transport, trust_transport_security=True
            ) as admin,
            PowerContextClient(
                "http://testserver", token=VIEWER.id, http_client=transport, trust_transport_security=True
            ) as viewer,
        ):
            yield app, admin, viewer


async def _seed(admin: PowerContextClient):
    scope_id = (
        await admin.create_scope(
            CreateScopeRequest(title="Search snapshot", summary="Concurrent access", idempotency_key="snapshot")
        )
    ).scope_id
    remembered = await admin.remember_memory(
        RememberMemoryRequest(scope_id=scope_id, kind="fact", text="Alpha original body.")
    )
    binding = await admin.create_access_binding(
        CreateAccessBindingRequest.model_validate({
            "subject": {"type": VIEWER.type, "id": VIEWER.id},
            "resource": {"type": "scope", "scope_id": scope_id},
            "role": "scope.viewer",
            "idempotency_key": "viewer-scope",
        })
    )
    return scope_id, remembered.records[0], binding


async def _revoke_and_create(admin: PowerContextClient, scope_id: str, binding):
    revoked = await admin.revoke_access_binding(
        RevokeAccessBindingRequest(
            binding_id=binding.binding_id, expected_version=binding.version, idempotency_key="revoke-viewer"
        )
    )
    assert revoked.state == "revoked"
    remembered = await admin.remember_memory(
        RememberMemoryRequest(scope_id=scope_id, kind="fact", text="Alpha new secret body.")
    )
    return remembered.records[0]


async def _assert_current_access_revoked(viewer: PowerContextClient, scope_id: str, created) -> None:
    current = await viewer.search_atomic_memory(SearchAtomicMemoryRequest(scope_id=scope_id, query="alpha"))
    assert current.hits == []
    with pytest.raises(ForbiddenResponseError):
        await viewer.get_artifact(scope_id, "atomic-memory", created.artifact.artifact_id)


@pytest.mark.parametrize("backend", ["sqlite", "oceanbase", "seekdb"])
@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_search_rechecks_access_after_query_embedding(backend: str, provider: str, mode: str, tmp_path: Path) -> None:
    async def scenario() -> None:
        embedding = _Embedding()
        async with (
            _isolated_database(backend, tmp_path) as database,
            _server(database, provider, tmp_path, embedding) as (_, admin, viewer),
        ):
            scope_id, _, binding = await _seed(admin)
            embedding.pause_query = True
            pending = asyncio.create_task(
                viewer.search_atomic_memory(
                    SearchAtomicMemoryRequest(scope_id=scope_id, query="alpha", mode=AtomicMemorySearchMode(mode))
                )
            )
            try:
                await asyncio.wait_for(embedding.query_started.wait(), timeout=20)
                # No read transaction may hold a writer while a model request is pending.
                created = await asyncio.wait_for(_revoke_and_create(admin, scope_id, binding), timeout=20)
            finally:
                embedding.resume_query.set()
                result = await asyncio.wait_for(pending, timeout=20)
            assert result.hits == []
            await _assert_current_access_revoked(viewer, scope_id, created)

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("revision", [None, 2])
def test_artifact_get_rechecks_access_after_route_authorization(tmp_path, monkeypatch, provider, revision):
    async def scenario():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'artifact-get.db'}")
        async with _server(database, provider, tmp_path, _Embedding()) as (app, admin, _):
            scope_id, original, binding = await _seed(admin)
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{original.artifact.artifact_id}"
            if revision is not None:
                path += f"/revisions/{revision}"
            authorized, resume = asyncio.Event(), asyncio.Event()
            require = AccessControlService.require

            async def pause_route(self, principal, *args, **kwargs):
                result = await require(self, principal, *args, **kwargs)
                if principal == VIEWER and not authorized.is_set():
                    authorized.set()
                    await resume.wait()
                return result

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                with monkeypatch.context() as patch:
                    patch.setattr(AccessControlService, "require", pause_route)
                    pending = asyncio.create_task(client.get(path, headers={"Authorization": "Bearer viewer"}))
                    try:
                        await asyncio.wait_for(authorized.wait(), timeout=20)
                        await admin.revoke_access_binding(
                            RevokeAccessBindingRequest(
                                binding_id=binding.binding_id,
                                expected_version=binding.version,
                                idempotency_key="revoke-get",
                            )
                        )
                        revised = await client.put(
                            f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{original.artifact.artifact_id}",
                            headers={"Authorization": "Bearer admin", "If-Match": '"revision:1"'},
                            json={"content": {"kind": "fact", "text": "New private revision after revocation."}},
                        )
                        assert revised.status_code == 200, revised.text
                        assert revised.json()["revision"] == 2
                    finally:
                        resume.set()
                        result = await asyncio.wait_for(pending, timeout=20)
                assert result.status_code == 403, result.text
                assert "New private revision" not in result.text
                fresh = await client.get(path, headers={"Authorization": "Bearer viewer"})
                assert fresh.status_code == 403

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("mode", ["text", "hybrid"])
def test_separate_policy_database_pins_content_before_allow(tmp_path, monkeypatch, provider, mode):
    async def scenario():
        content_database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'content.db'}")
        policy_database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'policy.db'}")
        open_access = open_builtin_access_control if provider == "builtin" else open_casbin_access_control
        audit = AccessAuditContext(transport="local", operation="atomic-search", request_id="separate-databases")
        async with (
            open_builtin_contexts(BuiltinConfig(database=content_database), embedding_model=_Embedding()) as contexts,
            open_access(policy_database, bootstrap_administrators=(ADMIN,), deployment_id=DEPLOYMENT_ID) as access,
        ):
            await contexts.get("project")
            (original,) = await contexts.records.create_atomic_memories(
                "project", ({"kind": "fact", "text": "Alpha original body."},)
            )
            binding = await access.create_binding(
                ADMIN,
                CreateBinding(
                    subject=VIEWER,
                    resource=ResourceRef.scope("project"),
                    role=AccessRole.SCOPE_VIEWER,
                    idempotency_key="separate-viewer",
                ),
                context=audit,
            )
            context = AtomicMemoryExecutionContext(VIEWER, access, audit)
            memory = contexts.atomic_memory.for_scope("project")
            filters = AtomicMemorySecurity.filters
            authorized, resume = asyncio.Event(), asyncio.Event()

            async def pause_after_allow(*args, **kwargs):
                result = await filters(*args, **kwargs)
                authorized.set()
                await resume.wait()
                return result

            with monkeypatch.context() as patch:
                patch.setattr(AtomicMemorySecurity, "filters", pause_after_allow)
                pending = asyncio.create_task(memory.search("alpha", mode=mode, context=context))
                try:
                    await asyncio.wait_for(authorized.wait(), timeout=20)
                    await access.revoke_binding(
                        ADMIN,
                        binding.binding_id,
                        expected_version=binding.version,
                        idempotency_key="separate-revoke",
                        context=audit,
                    )
                    await contexts.records.create_atomic_memories(
                        "project", ({"kind": "fact", "text": "Alpha new private body."},)
                    )
                finally:
                    resume.set()
                    result = await asyncio.wait_for(pending, timeout=20)
            assert [hit.hit.artifact_ref.artifact_id for hit in result.hits] == [original.artifact_id]
            assert [hit.text for hit in result.hits] == ["Alpha original body."]
            assert (await memory.search("alpha", mode=mode, context=context)).hits == ()

    asyncio.run(scenario())


@pytest.mark.parametrize("provider", ["builtin", "casbin"])
def test_authorized_artifact_reads_preserve_exact_revision_digest_and_etags(tmp_path, provider):
    async def scenario():
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'exact-read.db'}")
        async with _server(database, provider, tmp_path, _Embedding()) as (app, admin, _):
            scope_id, original, _ = await _seed(admin)
            path = f"/v1/scopes/{scope_id}/artifacts/atomic-memory/{original.artifact.artifact_id}"
            headers = {"Authorization": "Bearer viewer"}
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://testserver"
            ) as client:
                head = await client.get(path, headers=headers)
                exact = await client.get(f"{path}/revisions/1", headers=headers)
                assert head.status_code == exact.status_code == 200
                assert head.headers["ETag"] == '"revision:1"'
                assert "ETag" not in exact.headers
                assert exact.json() == head.json()
                unchanged = await client.get(path, headers={**headers, "If-None-Match": head.headers["ETag"]})
                assert unchanged.status_code == 304 and unchanged.content == b""
                revised = await client.put(
                    path,
                    headers={"Authorization": "Bearer admin", "If-Match": head.headers["ETag"]},
                    json={"content": {"kind": "fact", "text": "Alpha revised body."}},
                )
                assert revised.status_code == 200, revised.text
                new_head = await client.get(path, headers={**headers, "If-None-Match": head.headers["ETag"]})
                assert new_head.status_code == 200
                assert new_head.headers["ETag"] == '"revision:2"'
                assert new_head.json() == revised.json()
                assert new_head.json()["content_digest"] != head.json()["content_digest"]
                historical = await client.get(f"{path}/revisions/1", headers=headers)
                assert historical.status_code == 200 and historical.json() == head.json()
                missing = await client.get(f"{path}/revisions/99", headers=headers)
                assert missing.status_code == 404

    asyncio.run(scenario())


@pytest.mark.parametrize("backend", ["sqlite", "oceanbase", "seekdb"])
@pytest.mark.parametrize("provider", ["builtin", "casbin"])
@pytest.mark.parametrize("mode", ["text", "vector", "hybrid"])
def test_search_pins_access_and_content_without_blocking_writers(
    backend: str, provider: str, mode: str, tmp_path: Path, monkeypatch
) -> None:
    async def scenario() -> None:
        async with (
            _isolated_database(backend, tmp_path) as database,
            _server(database, provider, tmp_path, _Embedding()) as (_, admin, viewer),
        ):
            scope_id, original, binding = await _seed(admin)
            filters = AtomicMemorySecurity.filters
            authorized = asyncio.Event()
            resume = asyncio.Event()

            async def pause_after_access(*args, **kwargs):
                result = await filters(*args, **kwargs)
                authorized.set()
                await resume.wait()
                return result

            with monkeypatch.context() as patch:
                patch.setattr(AtomicMemorySecurity, "filters", pause_after_access)
                pending = asyncio.create_task(
                    viewer.search_atomic_memory(
                        SearchAtomicMemoryRequest(scope_id=scope_id, query="alpha", mode=AtomicMemorySearchMode(mode))
                    )
                )
                try:
                    await asyncio.wait_for(authorized.wait(), timeout=20)
                    # The authorized read snapshot stays open until after both writes commit.
                    created = await asyncio.wait_for(_revoke_and_create(admin, scope_id, binding), timeout=20)
                finally:
                    resume.set()
                    result = await asyncio.wait_for(pending, timeout=20)
            assert [hit.memory.artifact for hit in result.hits] == [original.artifact]
            assert [hit.memory.text for hit in result.hits] == ["Alpha original body."]
            await _assert_current_access_revoked(viewer, scope_id, created)

    asyncio.run(scenario())
