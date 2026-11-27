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

"""Keep operation history pages and cursor reads bounded on retained SQLite runs."""

import asyncio
from datetime import timedelta

from sqlalchemy import insert

from powercontext.builtin.dream.models import ListDreamRunsRequest
from powercontext.builtin.persistence.codec import dump_model
from powercontext.builtin.persistence.dream import DreamRepository, ticks
from powercontext.builtin.persistence.tables import DREAM_RUNS_TABLE
from tests.builtin.test_processing_status import _run_record, _runtime


def test_operation_pages_seek_sparse_empty_and_deep_history(tmp_path):
    async def scenario():
        async with (
            _runtime(tmp_path) as (runtime, scopes),
            runtime.primary_database.transaction() as connection,
        ):
            original = await _run_record(connection, scopes[0], 0)
            rows = []
            for index in range(1, 12001):
                identifier = f"page-{index:05}"
                operation = "refine_experience" if index % 2000 == 0 else "derive_skill"
                accepted = original.run.accepted_at + timedelta(microseconds=index // 3)
                record = original.model_copy(
                    update={
                        "run": original.run.model_copy(
                            update={
                                "run_id": identifier,
                                "operation": operation,
                                "accepted_at": accepted,
                            }
                        ),
                        "request": original.request.model_copy(
                            update={
                                "idempotency_key": identifier,
                                "operation": operation,
                            }
                        ),
                    }
                )
                rows.append({
                    "scope_id": scopes[0],
                    "run_id": identifier,
                    "principal_key": "fixture",
                    "idempotency_key": identifier,
                    "request_digest": record.request.digest(),
                    "operation": operation,
                    "status": "succeeded",
                    "accepted_at": ticks(accepted),
                    "generation": 0,
                    "request_generation": 1,
                    "payload": dump_model(record, kind="dream", name="record"),
                })
            await connection.execute(insert(DREAM_RUNS_TABLE), rows)
            await _run_record(connection, scopes[1], 1)
            driver = (await connection.get_raw_connection()).driver_connection
            for operation, cursor in (
                ("refine_experience", None),
                ("revise_tags", None),
                ("derive_skill", f"{ticks(original.run.accepted_at + timedelta(microseconds=2000))}:page-06000"),
            ):
                steps = 0

                def progress():
                    nonlocal steps
                    steps += 100
                    return 0

                await driver.set_progress_handler(progress, 100)
                try:
                    page = await DreamRepository().list(
                        connection,
                        scopes[0],
                        ListDreamRunsRequest(operation=operation, cursor=cursor, limit=6),
                    )
                finally:
                    await driver.set_progress_handler(None, 0)
                # An external read-work budget: sparse and deep pages must not walk 12,000 runs.
                assert steps < 2000
                assert all(run.scope_id == scopes[0] and run.operation == operation for run in page.runs)
                if operation == "revise_tags":
                    assert not page.runs and page.next_cursor is None
                elif cursor:
                    assert len(page.runs) == 6
                    assert page.runs[0].run_id == "page-05999"
                    following = await DreamRepository().list(
                        connection,
                        scopes[0],
                        ListDreamRunsRequest(operation=operation, cursor=page.next_cursor, limit=6),
                    )
                    assert not {run.run_id for run in page.runs} & {run.run_id for run in following.runs}
                else:
                    assert len(page.runs) == 6
                    assert page.runs[0].run_id == "page-12000"
                    assert page.next_cursor is None

    asyncio.run(scenario())
