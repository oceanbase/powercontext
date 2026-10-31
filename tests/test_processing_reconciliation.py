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

"""Recorded processing results agree with the artifacts they name."""

import asyncio
import json

from sqlalchemy import delete, select, update

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import (
    ARTIFACT_LINEAGE_SOURCES_TABLE,
    MEMORY_PROCESSING_ARTIFACTS_TABLE,
    MEMORY_PROCESSING_RECORDS_TABLE,
)
from powercontext.builtin.processing_reconciliation import reconcile
from powercontext.builtin.runtime import BuiltinConfig, CaptureSource
from powercontext.builtin.runtime.composition import open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from tests.e2e.test_memory_processing_records import TEXT, pipeline


def test_reconciliation_detects_each_recorded_claim(tmp_path):
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path}/reconcile.db"))

    async def scenario():
        async with open_builtin_runtime(config, candidate_pipeline=pipeline()) as runtime:
            assert runtime.scopes is not None and runtime.atomic_memory is not None
            scope = await runtime.scopes.create(ScopeDraft(title="Reconcile", summary="Claims", idempotency_key="r"))
            await runtime.sources.for_scope(scope.scope_id).capture(
                CaptureSource(source_id="input", content=TEXT, metadata={})
            )
            await runtime.atomic_memory.for_scope(scope.scope_id).flush(limit=1)

            async with runtime.primary_database.transaction() as connection:
                assert await reconcile(connection) == []

            async with runtime.primary_database.transaction() as connection:
                await connection.execute(
                    delete(ARTIFACT_LINEAGE_SOURCES_TABLE).where(
                        ARTIFACT_LINEAGE_SOURCES_TABLE.c.scope_id == scope.scope_id
                    )
                )
            async with runtime.primary_database.transaction() as connection:
                lineage = await reconcile(connection)
            assert [problem.check for problem in lineage] == ["result_source_not_in_lineage"]
            assert lineage[0].scope_id == scope.scope_id

            async with runtime.primary_database.transaction() as connection:
                await connection.execute(
                    delete(MEMORY_PROCESSING_ARTIFACTS_TABLE).where(
                        MEMORY_PROCESSING_ARTIFACTS_TABLE.c.scope_id == scope.scope_id
                    )
                )
            async with runtime.primary_database.transaction() as connection:
                missing_index = await reconcile(connection)
            assert {problem.check for problem in missing_index} == {"result_source_not_in_lineage", "index_mismatch"}

            async with runtime.primary_database.transaction() as connection:
                row = (await connection.execute(select(MEMORY_PROCESSING_RECORDS_TABLE))).mappings().one()
                payload = json.loads(bytes(row["payload"]).decode("utf-8"))
                payload["results"][0]["artifact"]["artifact_id"] = "missing"
                await connection.execute(
                    update(MEMORY_PROCESSING_RECORDS_TABLE)
                    .where(MEMORY_PROCESSING_RECORDS_TABLE.c.attempt_id == row["attempt_id"])
                    .values(payload=json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                )
            async with runtime.primary_database.transaction() as connection:
                vanished = await reconcile(connection)
            checks = {(problem.check, problem.attempt_id) for problem in vanished}
            assert ("result_missing_artifact", row["attempt_id"]) in checks

    asyncio.run(scenario())
