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

"""Observable contracts for portable logical bundles."""

from __future__ import annotations

import asyncio
import hashlib
import json
import tracemalloc
import zipfile
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import delete, insert, select

from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_CANDIDATE_HEADS_TABLE,
    ARTIFACT_CANDIDATE_VERSIONS_TABLE,
    ARTIFACT_HEADS_TABLE,
    ARTIFACT_PUBLICATIONS_TABLE,
    ARTIFACTS_TABLE,
    BUILTIN_TABLES,
    PORTABLE_RESTORE_RECEIPTS_TABLE,
    SCOPE_CONTEXT_REFERENCES_TABLE,
    SCOPE_CREATION_REQUESTS_TABLE,
    SCOPES_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.portability import BundleConflictError, BundleFormatError, PortableBundleService


async def _authorize_export(_scopes: tuple[str, ...], /) -> None:
    pass


async def _insert_scope(connection, scope_id: str = "project:one") -> None:
    await connection.execute(
        insert(SCOPES_TABLE).values(
            scope_id=scope_id,
            title="Portable fixture",
            summary="Portable fixture",
            scope_id_search=scope_id,
            title_search="Portable fixture",
            summary_search="Portable fixture",
            parent_scope_id=None,
            version=1,
        )
    )


def test_bundle_round_trips_authoritative_scope_data_and_excludes_projections(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        source_path = tmp_path / "source.db"
        target_path = tmp_path / "target.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{source_path}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="source-1",
                        payload=b'{"name":"source-1"}',
                        journal_position=1,
                    )
                )
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id="project:one",
                        family="handoff",
                        artifact_id="handoff",
                        revision=1,
                        content=b'{"content":{"summary":"hello"}}',
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_HEADS_TABLE).values(
                        scope_id="project:one",
                        family="handoff",
                        artifact_id="handoff",
                        revision=1,
                        searchable_text="not portable",
                    )
                )
            receipt = await PortableBundleService(source.database).export(
                ["project:one"], archive, authorize=_authorize_export
            )
            assert receipt.record_count == 5

        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{target_path}"), tables=BUILTIN_TABLES
        ) as target:
            service = PortableBundleService(target.database)
            inspection = await service.validate(archive, supported_source_types=("content",))
            assert inspection.scopes == ("project:one",)
            restored = await service.restore(archive, supported_source_types=("content",))
            assert restored.inserted == 5
            repeated = await service.restore(archive, supported_source_types=("content",))
            assert repeated.already_present == 5
            async with target.database.transaction() as connection:
                assert await connection.scalar(select(ARTIFACT_HEADS_TABLE.c.searchable_text)) is None

    asyncio.run(scenario())


def test_export_is_byte_deterministic_and_preserves_scope_relationships(tmp_path: Path) -> None:
    async def scenario() -> None:
        first = tmp_path / "first.pcb"
        second = tmp_path / "second.pcb"
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection, "project:parent")
                await connection.execute(
                    insert(SCOPES_TABLE).values(
                        scope_id="workstream:child",
                        title="Child",
                        summary="Child scope",
                        scope_id_search="workstream:child",
                        title_search="Child",
                        summary_search="Child scope",
                        parent_scope_id="project:parent",
                        version=1,
                    )
                )
                await connection.execute(
                    insert(SCOPE_CONTEXT_REFERENCES_TABLE).values(
                        scope_id="workstream:child", referenced_scope_id="project:parent"
                    )
                )
                await connection.execute(
                    insert(SCOPE_CREATION_REQUESTS_TABLE).values(
                        idempotency_key="child-request",
                        request_digest="digest",
                        scope_id="workstream:child",
                    )
                )
                await connection.execute(
                    insert(ARTIFACTS_TABLE),
                    [
                        {
                            "scope_id": "project:parent",
                            "family": "experience",
                            "artifact_id": "source-experience",
                            "revision": 1,
                            "content": b"source",
                        },
                        {
                            "scope_id": "workstream:child",
                            "family": "experience",
                            "artifact_id": "published-experience",
                            "revision": 1,
                            "content": b"published",
                        },
                    ],
                )
                await connection.execute(
                    insert(ARTIFACT_PUBLICATIONS_TABLE).values(
                        target_scope_id="workstream:child",
                        target_family="experience",
                        target_artifact_id="published-experience",
                        target_revision=1,
                        source_scope_id="project:parent",
                        source_family="experience",
                        source_artifact_id="source-experience",
                        source_revision=1,
                        content_digest="d" * 64,
                        idempotency_key="publish-experience",
                    )
                )
            service = PortableBundleService(source.database)
            one = await service.export(["workstream:child", "project:parent"], first, authorize=_authorize_export)
            two = await service.export(["project:parent", "workstream:child"], second, authorize=_authorize_export)
        assert one.bundle_id == two.bundle_id
        assert first.read_bytes() == second.read_bytes()

        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as target:
            restored = await PortableBundleService(target.database).restore(first)
            async with target.database.transaction() as connection:
                parent = await connection.scalar(
                    select(SCOPES_TABLE.c.parent_scope_id).where(SCOPES_TABLE.c.scope_id == "workstream:child")
                )
                context_count = await connection.scalar(select(SCOPE_CONTEXT_REFERENCES_TABLE.c.scope_id))
                creation_scope = await connection.scalar(select(SCOPE_CREATION_REQUESTS_TABLE.c.scope_id))
                publication_source = await connection.scalar(select(ARTIFACT_PUBLICATIONS_TABLE.c.source_scope_id))
        assert restored.record_count == 7
        assert parent == "project:parent"
        assert context_count == "workstream:child"
        assert creation_scope == "workstream:child"
        assert publication_source == "project:parent"

    asyncio.run(scenario())


def test_validation_accepts_a_clean_target_without_initializing_schema(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)
        async with SQLiteProfile.open(SQLiteConfig(), tables=()) as clean_target:
            validation = await PortableBundleService(
                clean_target.database,
                projection_rebuilder=lambda _scopes: asyncio.sleep(0),
            ).validate(archive)
        assert validation.compatible is True
        assert validation.already_present == 0
        assert validation.conflicts == 0

    asyncio.run(scenario())


def test_restore_rejects_divergent_immutable_identity_without_overwrite(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        source_path = tmp_path / "source.db"
        target_path = tmp_path / "target.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{source_path}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="source-1",
                        payload=b"original",
                        journal_position=1,
                    )
                )
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{target_path}"), tables=BUILTIN_TABLES
        ) as target:
            async with target.database.transaction() as connection:
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="source-1",
                        payload=b"different",
                        journal_position=1,
                    )
                )
            service = PortableBundleService(target.database)
            validation = await service.validate(archive)
            assert validation.compatible is False
            assert validation.conflicts == 1
            with pytest.raises(BundleConflictError):
                await service.restore(archive)
            async with target.database.transaction() as connection:
                assert await connection.scalar(select(SOURCES_TABLE.c.payload)) == b"different"

    asyncio.run(scenario())


def test_failed_restore_rolls_back_earlier_records_and_can_be_retried(tmp_path: Path) -> None:
    """A write-time constraint conflict must not strand earlier bundle rows."""

    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        source_path = tmp_path / "source.db"
        target_path = tmp_path / "target.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{source_path}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="source-1",
                        payload=b"new source",
                        journal_position=1,
                    )
                )
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)

        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{target_path}"), tables=BUILTIN_TABLES
        ) as target:
            async with target.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="target-only",
                        payload=b"existing",
                        journal_position=1,
                    )
                )
            service = PortableBundleService(target.database)
            validation = await service.validate(archive)
            assert validation.conflicts == 0
            with pytest.raises(BundleConflictError, match="target constraint conflict"):
                await service.restore(archive)

            async with target.database.transaction() as connection:
                source_ids = tuple((await connection.execute(select(SOURCES_TABLE.c.source_id))).scalars())
                assert source_ids == ("target-only",)
                await connection.execute(delete(SOURCES_TABLE))

            retried = await service.restore(archive)
            assert retried.inserted == 1
            assert retried.already_present == 2

    asyncio.run(scenario())


def test_restore_reports_projection_readiness_from_runtime_rebuilder(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        source_path = tmp_path / "source.db"
        target_path = tmp_path / "target.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{source_path}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)
        rebuilt: list[tuple[str, ...]] = []
        progress = []

        async def rebuild(scopes: tuple[str, ...]) -> None:
            rebuilt.append(scopes)

        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{target_path}"), tables=BUILTIN_TABLES
        ) as target:
            receipt = await PortableBundleService(target.database, projection_rebuilder=rebuild).restore(
                archive, progress=progress.append
            )
        assert rebuilt == [("project:one",)]
        assert receipt.projections_ready is True
        assert receipt.status == "ready"
        assert {event.phase for event in progress} >= {"validate", "target", "restore", "projections"}

    asyncio.run(scenario())


def test_export_authorization_precedes_database_access(tmp_path: Path) -> None:
    class _ForbiddenDatabase:
        def transaction(self):
            raise AssertionError("database was accessed before authorization")  # noqa: TRY003

    async def scenario() -> None:
        async def deny(_scopes: tuple[str, ...]) -> None:
            raise PermissionError("denied")

        service = PortableBundleService(cast(AsyncDatabase, _ForbiddenDatabase()))
        with pytest.raises(PermissionError, match="denied"):
            await service.export(["project:one"], tmp_path / "denied.pcb", authorize=deny)
        assert not (tmp_path / "denied.pcb").exists()

    asyncio.run(scenario())


def test_projection_failure_leaves_a_durable_not_ready_receipt(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)

        async def fail_rebuild(_scopes: tuple[str, ...]) -> None:
            raise RuntimeError("projection failed")  # noqa: TRY003

        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as target:
            with pytest.raises(RuntimeError, match="projection failed"):
                await PortableBundleService(target.database, projection_rebuilder=fail_rebuild).restore(archive)
            async with target.database.transaction() as connection:
                row = (await connection.execute(select(PORTABLE_RESTORE_RECEIPTS_TABLE))).mappings().one()
            assert row["status"] == "authoritative_restored"
            assert row["projections_ready"] is False

    asyncio.run(scenario())


def test_export_rejects_secret_fields_without_echoing_values(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "secret.pcb"
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="source-1",
                        payload=b'{"metadata":{"api_key":"do-not-echo"}}',
                        journal_position=1,
                    )
                )
            with pytest.raises(BundleFormatError) as caught:
                await PortableBundleService(source.database).export(
                    ["project:one"], archive, authorize=_authorize_export
                )
            assert "credential-shaped" in str(caught.value)
            assert "do-not-echo" not in str(caught.value)
            assert not archive.exists()

    asyncio.run(scenario())


def test_restore_accepts_a_valid_bundle_with_records_in_a_different_physical_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "ordered.pcb"
        reordered = tmp_path / "reordered.pcb"
        database = tmp_path / "source.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1))
                await connection.execute(
                    insert(SOURCES_TABLE).values(
                        scope_id="project:one",
                        source_type="content",
                        source_id="source-1",
                        payload=b"source",
                        journal_position=1,
                    )
                )
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id="project:one",
                        family="handoff",
                        artifact_id="handoff",
                        revision=1,
                        content=b"artifact",
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_HEADS_TABLE).values(
                        scope_id="project:one", family="handoff", artifact_id="handoff", revision=1
                    )
                )
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)

        with zipfile.ZipFile(archive) as input_archive:
            manifest = json.loads(input_archive.read("manifest.json"))
            records = list(reversed(input_archive.read("records.ndjson").splitlines()))
        digests = [json.loads(record)["digest"] for record in records]
        manifest["total_digest"] = "sha256:" + hashlib.sha256("\n".join(digests).encode()).hexdigest()
        with zipfile.ZipFile(reordered, "w") as output_archive:
            output_archive.writestr("manifest.json", json.dumps(manifest))
            output_archive.writestr("records.ndjson", b"\n".join(records) + b"\n")

        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as target:
            receipt = await PortableBundleService(target.database).restore(reordered)
        assert receipt.inserted == 5

    asyncio.run(scenario())


def test_restore_keeps_large_bundle_payloads_out_of_process_memory(tmp_path: Path) -> None:
    """The import budget is independent of aggregate NDJSON payload size."""

    async def scenario() -> None:
        archive = tmp_path / "large.pcb"
        source_path = tmp_path / "source.db"
        target_path = tmp_path / "target.db"
        payload = b"x" * 8_192
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{source_path}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(
                    insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="project:one", position=1_500)
                )
                await connection.execute(
                    insert(SOURCES_TABLE),
                    [
                        {
                            "scope_id": "project:one",
                            "source_type": "content",
                            "source_id": f"source-{number}",
                            "payload": payload,
                            "journal_position": number,
                        }
                        for number in range(1, 1_501)
                    ],
                )
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)

        tracemalloc.start()
        try:
            async with SQLiteProfile.open(
                SQLiteConfig(url=f"sqlite+aiosqlite:///{target_path}"), tables=BUILTIN_TABLES
            ) as target:
                receipt = await PortableBundleService(target.database).restore(archive)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()

        assert receipt.inserted == 1_502
        assert peak < 10 * 1024 * 1024

    asyncio.run(scenario())


def test_validate_rejects_an_artifact_family_the_target_runtime_cannot_restore(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        source_path = tmp_path / "source.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{source_path}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(
                    insert(ARTIFACTS_TABLE).values(
                        scope_id="project:one",
                        family="unavailable-family",
                        artifact_id="record-1",
                        revision=1,
                        content=b"payload",
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_HEADS_TABLE).values(
                        scope_id="project:one",
                        family="unavailable-family",
                        artifact_id="record-1",
                        revision=1,
                    )
                )
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as target:
            validation = await PortableBundleService(
                target.database,
                supported_artifact_families=("handoff",),
            ).validate(archive)
            assert validation.compatible is False
            assert validation.unsupported_artifact_families == ("unavailable-family",)

    asyncio.run(scenario())


def test_validate_rejects_candidate_evidence_that_is_not_in_the_bundle(tmp_path: Path) -> None:
    async def scenario() -> None:
        archive = tmp_path / "scope.pcb"
        database = tmp_path / "source.db"
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{database}"), tables=BUILTIN_TABLES
        ) as source:
            async with source.database.transaction() as connection:
                await _insert_scope(connection)
                await connection.execute(
                    insert(ARTIFACT_CANDIDATE_VERSIONS_TABLE).values(
                        scope_id="project:one",
                        candidate_id="candidate-1",
                        version=1,
                        family="handoff",
                        proposal=b"{}",
                        source_refs=b'[{"source_type":"content","source_id":"missing"}]',
                        artifact_refs=b"[]",
                        target_family=None,
                        target_artifact_id=None,
                        target_revision=None,
                        reason=None,
                    )
                )
                await connection.execute(
                    insert(ARTIFACT_CANDIDATE_HEADS_TABLE).values(
                        scope_id="project:one",
                        candidate_id="candidate-1",
                        family="handoff",
                        version=1,
                        status="pending",
                    )
                )
            await PortableBundleService(source.database).export(["project:one"], archive, authorize=_authorize_export)
        async with SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as target:
            with pytest.raises(BundleFormatError, match="missing source"):
                await PortableBundleService(target.database).validate(archive)

    asyncio.run(scenario())
