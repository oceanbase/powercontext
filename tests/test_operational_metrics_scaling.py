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

"""Measure passive metric collection against real SQLite processing backlogs."""

from __future__ import annotations

import asyncio
from time import perf_counter
from types import SimpleNamespace
from typing import cast

from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import event, select

from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    ARTIFACT_PROCESSING_INTENTS_TABLE,
    ARTIFACT_PROCESSING_REQUESTS_TABLE,
    SHARED_TABLES,
)
from powercontext.builtin.runtime import BuiltinRuntime
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding, ArtifactProcessingSupervisor
from powercontext.builtin.runtime.readiness import RuntimeReadinessSnapshot
from powercontext.server.metrics import ServerMetrics


def test_real_backlog_collection_is_passive_and_cardinality_bounded(tmp_path):  # noqa: C901 - real backlog lifecycle
    async def scenario(count):
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / f'backlog-{count}.db'}"), tables=SHARED_TABLES
        ) as profile:
            intents = ArtifactProcessingIntentRepository()
            async with profile.database.transaction() as connection:
                for index in range(count):
                    await intents.request(connection, f"private-scope-{index}", "binding")
            started = asyncio.Event()

            class Handle:
                async def wait(self):
                    started.set()
                    await asyncio.Event().wait()

                async def terminate(self):
                    pass

            class Launcher:
                async def start(self, assignment):
                    return Handle()

            supervisor = ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=[ArtifactProcessingBinding("binding", "memory", Launcher(), max_workers=1)],
                lease_mode="single-process",
                pending_observation_interval_seconds=0.01,
            )

            async def durable_rows():
                async with profile.database.transaction(consistent_snapshot=True) as connection:
                    rows = []
                    for table in (ARTIFACT_PROCESSING_INTENTS_TABLE, ARTIFACT_PROCESSING_REQUESTS_TABLE):
                        rows.append(tuple((await connection.execute(select(table).order_by(table.c.scope_id))).all()))
                    return tuple(rows)

            try:
                await supervisor.start()
                await asyncio.wait_for(started.wait(), 5)
                async with asyncio.timeout(5):
                    while supervisor.family_status["memory"]["unacknowledged_requests"] != count:  # noqa: ASYNC110
                        await asyncio.sleep(0.001)
                status = supervisor.family_status["memory"]
                assert status["used_workers"] == 1
                assert 0 < int(status["observed_pending_scopes"]) <= min(count, 1000)
                before = await durable_rows()
                statements = []

                def statement_observed(_connection, _cursor, statement, _parameters, _context, _many):
                    statements.append(statement)

                # The runtime facade supplies only readiness; processing snapshots come
                # from the real active Supervisor, never a fabricated backlog mapping.
                runtime = cast(
                    BuiltinRuntime,
                    SimpleNamespace(
                        readiness_snapshot=lambda: RuntimeReadinessSnapshot("unobserved", None, ()),
                        artifact_processing_supervisor=supervisor,
                    ),
                )
                metrics = ServerMetrics(role="background")
                event.listen(profile.database.engine.sync_engine, "before_cursor_execute", statement_observed)
                try:
                    started_at = perf_counter()
                    for _ in range(50):
                        metrics.set_processing_families(supervisor.family_status)
                        metrics.operational.replace(runtime)
                        rendered = metrics.render().decode()
                    duration = perf_counter() - started_at
                finally:
                    event.remove(profile.database.engine.sync_engine, "before_cursor_execute", statement_observed)
                assert statements == []
                assert await durable_rows() == before
                assert supervisor.family_status["memory"]["completed"] == 0
                assert "private-scope" not in rendered
                samples = {
                    (sample.name, tuple(sorted(sample.labels.items()))): sample.value
                    for family in text_string_to_metric_families(rendered)
                    for sample in family.samples
                    if family.name.startswith("powercontext_server_")
                }
                assert (
                    samples[
                        ("powercontext_server_artifact_processing_unacknowledged_requests", (("family", "memory"),))
                    ]
                    == count
                )
                assert (
                    samples[
                        ("powercontext_server_artifact_processing_observed_pending_scopes", (("family", "memory"),))
                    ]
                    == status["observed_pending_scopes"]
                )
                print(f"backlog={count}: 50 collections={duration:.6f}s; series={len(samples)}; SQL=0")
                return set(samples)
            finally:
                await supervisor.close()

    small = asyncio.run(scenario(1))
    large = asyncio.run(scenario(1200))
    assert large == small
