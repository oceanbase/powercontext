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

"""Concurrent publication remains valid across actual backend startup boundaries."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.dialects import mysql
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import create_async_engine

from powercontext.builtin.persistence.migrations import connections
from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.oceanbase import profile as oceanbase_profile
from powercontext.builtin.persistence.seekdb import profile as seekdb_profile
from powercontext.builtin.runtime import BuiltinConfig, atomic_memory_rebuild, open_builtin_contexts
from powercontext.builtin.runtime.config import RuntimeConfig
from powercontext.server import cli
from powercontext.server.factory import create_server_app
from powercontext.server.settings import AccessControlConfig, BearerAuthConfig, McpConfig, ServerSettings
from powercontext.server.tracing import ServerTracing
from tests.builtin.persistence.test_server_vector_migrations import ControlledEmbedding, profile
from tests.e2e.server_storage import prepare_server_database


# Only select the supported session isolation; all SQL, publication and
# validation use the real selected backend. Native cases require the opt-in
# fixtures and run without skips in the isolated CI backend harness.
def _read_committed_engine(*args, **kwargs):
    engine = create_async_engine(*args, **kwargs)
    if engine.dialect.name == "mysql":

        @event.listens_for(engine.sync_engine, "connect")
        def configure(dbapi_connection, _record):
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("SET SESSION TRANSACTION ISOLATION LEVEL READ COMMITTED")
            finally:
                cursor.close()

    return engine


@contextmanager
def _nonlocking_reads():
    checking_record = ContextVar("checking_record", default=False)

    def reject_locking_read(_connection, statement, _multiparams, _params, _options):
        if checking_record.get():
            # SQLite omits locking clauses. Check the native SQL contract
            # as well so local runs catch locks before native concurrency CI.
            sql = str(statement.compile(dialect=mysql.dialect())).upper()
            assert "FOR UPDATE" not in sql and "LOCK IN SHARE MODE" not in sql

    token = checking_record.set(True)
    event.listen(Engine, "before_execute", reject_locking_read)
    try:
        yield
    finally:
        event.remove(Engine, "before_execute", reject_locking_read)
        checking_record.reset(token)


@pytest.mark.parametrize("entry,vector", [("server", False), ("sdk", True), ("background", False)])
def test_concurrent_publication_does_not_reject_managed_startup(migration_backend, monkeypatch, entry, vector):
    for module in (connections, oceanbase_profile, seekdb_profile):
        monkeypatch.setattr(module, "create_async_engine", _read_committed_engine)
    embedding = ControlledEmbedding(profile(3)) if vector else None
    settings = ServerSettings(
        database=migration_backend.config,
        runtime=RuntimeConfig(artifact_processing_families=()),
        mcp=McpConfig(enabled=False),
        access=AccessControlConfig(mode="disabled"),
        auth=BearerAuthConfig(enabled=False),
    )
    evidence = migration_backend.directory / "migration-evidence"
    prepare_server_database(
        settings, evidence_dir=evidence, embedding_profile=None if embedding is None else embedding.profile
    )
    app = create_server_app(settings=settings, embedding_model=embedding)
    with TestClient(app) as client:
        created = client.post(
            "/v1/scopes",
            json={"title": "Concurrent startup", "summary": "Read consistency", "idempotency_key": "snapshot"},
        )
        assert created.status_code == 201, created.text
        scope = created.json()["scope_id"]
        remembered = client.post(
            "/v1/memory/remember", json={"scope_id": scope, "kind": "fact", "text": "Before concurrent revision."}
        )
        assert remembered.status_code == 200, remembered.text
        artifact = remembered.json()["records"][0]["artifact"]
        path = f"/v1/scopes/{scope}/artifacts/atomic-memory/{artifact['artifact_id']}"
        etag = client.get(path).headers["ETag"]
        original = atomic_memory_rebuild._record
        updated = False

        async def interleaved_record(*args, **kwargs):
            nonlocal updated
            with _nonlocking_reads():
                record = await original(*args, **kwargs)
            if not updated:
                updated = True
                connection = args[0]
                if connection.dialect.name == "mysql":
                    # A one-transaction snapshot must leave ordinary business
                    # transactions at the configured session default.
                    isolation = (await connection.exec_driver_sql("SELECT @@session.tx_isolation")).scalar_one()
                    assert str(isolation).replace("-", " ").upper() == "READ COMMITTED"
                replacement = client.put(
                    path,
                    headers={"If-Match": etag},
                    json={"content": {"kind": "fact", "text": "After concurrent revision."}},
                )
                assert replacement.status_code == 200, replacement.text
            return record

        async def start_sdk():
            async with open_builtin_contexts(
                BuiltinConfig(database=settings.database, runtime=settings.runtime), embedding_model=embedding
            ) as contexts:
                if settings.database.kind != "sqlite":
                    async with contexts.database.transaction() as connection:
                        isolation = (await connection.exec_driver_sql("SELECT @@session.tx_isolation")).scalar_one()
                        assert str(isolation).replace("-", " ").upper() == "READ COMMITTED"

        async def serve_metrics(application, _settings, stopped):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=application), base_url="http://testserver"
            ) as transport:
                response = await transport.get("/metrics")
                assert response.status_code == 200, response.text
                assert 'powercontext_server_readiness_state{state="ready"} 1.0' in response.text
            stopped.set()

        monkeypatch.setattr(atomic_memory_rebuild, "_record", interleaved_record)
        try:
            if entry == "sdk":
                asyncio.run(start_sdk())
            elif entry == "background":
                # Exercise the full background runtime and readiness; replace
                # only the listener lifetime so this test does not need signals.
                monkeypatch.setattr(cli, "serve_background_metrics", serve_metrics)
                asyncio.run(cli._run_background_async(settings, ServerTracing.context_only(role="background")))
            else:
                with TestClient(
                    create_server_app(
                        settings=settings,
                        embedding_model=embedding,
                    )
                ) as second:
                    assert second.get("/health/ready").status_code == 200
        finally:
            monkeypatch.setattr(atomic_memory_rebuild, "_record", original)
        assert updated
        assert client.get(path).json()["content"]["text"] == "After concurrent revision."
        runner = deployment_runner(
            settings,
            evidence_dir=evidence,
            lock_coordination="single-host" if settings.database.kind == "oceanbase" else None,
            embedding_profile=None if embedding is None else embedding.profile,
        )
        assert runner.verify().state == "ready"
