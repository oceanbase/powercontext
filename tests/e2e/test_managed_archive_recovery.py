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

"""Registered storage keeps interrupted archive recovery separate from normal startup."""

import asyncio

import pytest
from pydantic import JsonValue

from powercontext.builtin.persistence.migrations.deployment import deployment_runner
from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.portability import BundleRecoveryRequiredError, PortableBundleService
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings
from tests.builtin.persistence.test_server_vector_migrations import ControlledEmbedding, profile
from tests.e2e.server_storage import prepare_server_database


@pytest.mark.parametrize("family", ["atomic-memory", "experience", "topic-memory"])
@pytest.mark.parametrize("vector", [False, True])
def test_managed_archive_restore_resumes_after_projection_failure(tmp_path, family, vector):
    embedding = ControlledEmbedding(profile(3)) if vector else None
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"))
    prepare_server_database(settings, embedding_profile=None if embedding is None else embedding.profile)
    config = BuiltinConfig(database=settings.database)
    archive = tmp_path / "recovery.pcb"
    contents: dict[str, dict[str, JsonValue]] = {
        "atomic-memory": {"kind": "fact", "text": "Keep exact recovery evidence."},
        "experience": {"situation": "Recovery", "action": "Retry", "outcome": "Ready", "lesson": "Verify recovery."},
        "topic-memory": {"title": "Recovery", "summary": "Retry", "detail": "Keep exact recovery evidence."},
    }
    content = contents[family]

    async def authorize(_scopes):
        pass

    async def interrupted(_scopes):
        raise RuntimeError("interrupted projection rebuild")  # noqa: TRY003

    async def scenario():
        async with open_builtin_contexts(BuiltinConfig(), embedding_model=embedding) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Recovery", summary="Registered restore", idempotency_key="recovery")
            )
            artifact = await source.records.create_artifact(scope.scope_id, family, ArtifactWrite(content=content))
            authoritative = await source.records.get_artifact(scope.scope_id, family, artifact.artifact_id)
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        async with open_builtin_contexts(config, embedding_model=embedding) as target:
            with pytest.raises(RuntimeError, match="interrupted projection rebuild"):
                await PortableBundleService(
                    target.database,
                    projection_rebuilder=interrupted,
                    source_decoder=target.repositories.sources.decode_source,
                ).restore(archive)
        with pytest.raises(BundleRecoveryRequiredError):
            async with open_builtin_contexts(config, embedding_model=embedding):
                pass
        with pytest.raises(BundleRecoveryRequiredError):
            async with open_builtin_contexts(
                config, embedding_model=embedding, _topic_memory_worker=True, _archive_recovery=True
            ):
                pass
        other_embedding = None if vector else ControlledEmbedding(profile(3))
        with pytest.raises(MigrationError, match=r"embedding_profile_mismatch|unsupported_configuration"):
            async with open_builtin_contexts(config, embedding_model=other_embedding, _archive_recovery=True):
                pass
        async with open_builtin_contexts(config, embedding_model=embedding, _archive_recovery=True) as target:
            restored = await target.portability.restore(archive)
            assert restored.projections_ready
            assert (await target.portability.restore(archive)).inserted == 0
        async with open_builtin_contexts(config, embedding_model=embedding) as target:
            restored = await target.records.get_artifact(scope.scope_id, family, artifact.artifact_id)
            assert restored.content == authoritative.content

    asyncio.run(scenario())
    assert (
        deployment_runner(settings, embedding_profile=None if embedding is None else embedding.profile).verify().state
        == "ready"
    )


@pytest.mark.parametrize("corruption", ["schema", "projection"])
def test_archive_recovery_does_not_bypass_unrelated_managed_validation(tmp_path, corruption):
    settings = ServerSettings(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'target.db'}"))
    prepare_server_database(settings)
    config = BuiltinConfig(database=settings.database)

    async def scenario():
        async with open_builtin_contexts(config) as target:
            scope = await target.scopes.create(
                ScopeDraft(title="Validation", summary="No recovery pending", idempotency_key="validation")
            )
            await target.records.create_artifact(
                scope.scope_id, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Retain this record."})
            )
            async with target.database.transaction() as connection:
                if corruption == "schema":
                    await connection.exec_driver_sql("DROP TABLE pc_portable_restore_receipts")
                else:
                    await connection.exec_driver_sql("DELETE FROM pc_atomic_memory_current")
        with pytest.raises(MigrationError):
            async with open_builtin_contexts(config, _archive_recovery=True):
                pass

    asyncio.run(scenario())
