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

"""Measure public archive validation on disposable, file-backed SQLite targets.

Run the same script against each checkout with its src directory on PYTHONPATH.
Fixture creation/export and restore population are outside validation timings.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from pathlib import Path

from sqlalchemy import event, insert

from powercontext.builtin.persistence.sources import SourceRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import (
    BUILTIN_TABLES,
    SCOPES_TABLE,
    SOURCE_JOURNAL_HEADS_TABLE,
    SOURCES_TABLE,
)
from powercontext.builtin.portability import PortableBundleService
from powercontext.builtin.sources import BUILTIN_SOURCE_REGISTRY, ContentSource
from powercontext.paths import sqlite_url
from powercontext.sources import SourceMaterialization


async def measure(directory: Path, count: int, runs: int) -> None:
    archive = directory / "sources.pcb"

    async def authorize(_scopes: tuple[str, ...]) -> None:
        pass

    async with SQLiteProfile.open(
        SQLiteConfig(url=sqlite_url(directory / "source.db")), tables=BUILTIN_TABLES
    ) as source:
        async with source.database.transaction() as connection:
            await connection.execute(
                insert(SCOPES_TABLE).values(
                    scope_id="benchmark",
                    title="",
                    summary="",
                    scope_id_search="benchmark",
                    title_search="",
                    summary_search="",
                    version=1,
                )
            )
            await connection.execute(insert(SOURCE_JOURNAL_HEADS_TABLE).values(scope_id="benchmark", position=count))
            for start in range(0, count, 100):
                rows = []
                for number in range(start, min(start + 100, count)):
                    identity = f"source-{number:06}"
                    payload = (
                        ContentSource(name=identity, materialization=SourceMaterialization.CAPTURED, content="x" * 256)
                        .model_dump_json()
                        .encode()
                    )
                    rows.append({
                        "scope_id": "benchmark",
                        "source_type": "content",
                        "source_id": identity,
                        "payload": payload,
                        "journal_position": number + 1,
                    })
                await connection.execute(insert(SOURCES_TABLE), rows)
        await PortableBundleService(source.database).export(["benchmark"], archive, authorize=authorize)

    for state in ("empty", "identical", "absent"):
        async with SQLiteProfile.open(
            SQLiteConfig(url=sqlite_url(directory / f"{state}.db")),
            tables=() if state == "absent" else BUILTIN_TABLES,
        ) as target:
            service = PortableBundleService(
                target.database, source_decoder=SourceRepository(BUILTIN_SOURCE_REGISTRY).decode_source
            )
            restore_seconds = None
            if state == "identical":
                started = time.perf_counter()
                await service.restore(archive)
                restore_seconds = time.perf_counter() - started
            executions = []

            def observe(_connection, _cursor, statement, parameters, _context, _many, executions=executions):
                if statement.startswith("SELECT") and any(
                    f"FROM {table.name}" in statement
                    for table in (SCOPES_TABLE, SOURCE_JOURNAL_HEADS_TABLE, SOURCES_TABLE)
                ):
                    executions.append(len(parameters))

            event.listen(target.database.engine.sync_engine, "before_cursor_execute", observe)
            durations, queries = [], []
            max_parameters = 0
            for _ in range(runs):
                executions.clear()
                started = time.perf_counter()
                result = await service.validate(archive)
                durations.append(round(time.perf_counter() - started, 4))
                if (
                    not result.compatible
                    or result.conflicts
                    or result.already_present != (count + 2 if state == "identical" else 0)
                ):
                    message = "unexpected validation classification"
                    raise RuntimeError(message)
                queries.append(len(executions))
                max_parameters = max(max_parameters, max(executions, default=0))
            print(
                json.dumps({
                    "records": count + 2,
                    "state": state,
                    "queries": queries,
                    "seconds": durations,
                    "median_seconds": statistics.median(durations),
                    "max_parameters": max_parameters,
                    "restore_without_projections_seconds": restore_seconds,
                }),
                flush=True,
            )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=int, nargs="+", default=[100, 1_000, 5_000])
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    if args.runs < 1 or any(count < 1 for count in args.sources):
        parser.error("source counts and runs must be positive")
    print(
        json.dumps({
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
        }),
        flush=True,
    )
    for count in args.sources:
        with tempfile.TemporaryDirectory(prefix="pc-archive-benchmark-") as temporary:
            asyncio.run(measure(Path(temporary), count, args.runs))


if __name__ == "__main__":
    main()
