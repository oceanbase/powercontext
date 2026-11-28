# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Measure pc_memory_work_blocks capacity and query latency on disposable SQLite targets.

Populates the exact managed schema with synthetic rejection rows, then reports table and
index sizes, allocated page space, and latency for the two query shapes used by the recovery
manager: the current-block (frontier) lookup and the exact historical block-ID lookup.
Active (active = 1) and accepted (active IS NULL) rows are reported separately because both
still occupy table and frontier-index space.

Run with the src directory on PYTHONPATH: `PYTHONPATH=src python scripts/benchmark_memory_block_capacity.py`.
This script measures SQLite only; seekDB and OceanBase require their native backends and are measured separately.
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import platform
import sqlite3
import statistics
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from sqlalchemy import insert

from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES
from powercontext.builtin.persistence.tables import MEMORY_WORK_BLOCKS_TABLE as BLOCKS
from powercontext.builtin.triggers import SOURCE_WINDOW_TRIGGER_NAME
from powercontext.paths import sqlite_url

BLOCK_TABLE = "pc_memory_work_blocks"
_FRONTIER_QUERY = (
    "SELECT * FROM pc_memory_work_blocks WHERE scope_id = ? AND binding_name = ? AND source_after = ? AND active = 1"
)
_EXACT_QUERY = "SELECT * FROM pc_memory_work_blocks WHERE scope_id = ? AND binding_name = ? AND block_id = ?"


def _row(index, *, count, state, scopes, history, field_mode):
    scope_number = index % scopes
    scope_id = f"scp_{scope_number:026d}"
    binding = SOURCE_WINDOW_TRIGGER_NAME
    estimator = "character:weighted"
    if field_mode == "near_limit":
        scope_id = "s" * (256 - len(str(scope_number))) + str(scope_number)
        binding = "b" * 128
        estimator = "e" * 128
    active = state == "active" or (
        state == "mixed" and (index >= count - scopes if history == "repeated" else index % 10 == 0)
    )
    after = 0 if history == "repeated" else index // scopes
    blocked = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)
    return {
        "block_id": str(uuid4()),
        "scope_id": scope_id,
        "binding_name": binding,
        "source_after": after,
        "source_through": after + 1,
        "active": 1 if active else None,
        "input_fingerprint": "a" * 64,
        "estimated_input_tokens": 60000,
        "effective_budget": 40000,
        "token_estimator_id": estimator,
        "failure_location": "atomic_memory.extract",
        "blocked_at": blocked,
        "retried_at": None if active else blocked + timedelta(minutes=1),
        "retry_request_generation": None if active else index + 1,
    }


def _space(conn):
    page_size = conn.execute("PRAGMA page_size").fetchone()[0]
    page_count = conn.execute("PRAGMA page_count").fetchone()[0]
    freelist = conn.execute("PRAGMA freelist_count").fetchone()[0]
    sizes = dict(conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name").fetchall())
    # Include every index, including the UNIQUE frontier B-tree and its NULL rows.
    indices = [row[1] for row in conn.execute(f"PRAGMA index_list({BLOCK_TABLE})")]
    return {
        "table_bytes": sizes.get(BLOCK_TABLE, 0),
        "index_bytes": {name: sizes.get(name, 0) for name in indices},
        "allocated_bytes": page_count * page_size,
        "free_bytes": freelist * page_size,
    }


def _latency(conn, query, params, runs, expected):
    observed = len(conn.execute(query, params).fetchall())
    if observed != expected:
        raise RuntimeError(f"Query classified incorrectly: expected {expected}, observed {observed}")  # noqa: TRY003
    durations = []
    for _ in range(runs):
        started = time.perf_counter()
        conn.execute(query, params).fetchall()
        durations.append(time.perf_counter() - started)
    return {"matched_rows": observed, "median_seconds": statistics.median(durations)}


def _measure(directory, *, count, state, scopes, history, field_mode, runs):
    path = directory / "blocks.db"

    async def populate():
        async with (
            SQLiteProfile.open(SQLiteConfig(url=sqlite_url(path)), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            for start in range(0, count, 1000):
                rows = [
                    _row(
                        i,
                        count=count,
                        state=state,
                        scopes=scopes,
                        history=history,
                        field_mode=field_mode,
                    )
                    for i in range(start, min(start + 1000, count))
                ]
                await connection.execute(insert(BLOCKS), rows)

    asyncio.run(populate())
    with sqlite3.connect(path) as conn:
        space = _space(conn)
        active_rows = conn.execute("SELECT COUNT(*) FROM pc_memory_work_blocks WHERE active = 1").fetchone()[0]
        hit = conn.execute(
            "SELECT scope_id, binding_name, source_after FROM pc_memory_work_blocks WHERE active = 1 LIMIT 1"
        ).fetchone()
        frontier_hit = None if hit is None else _latency(conn, _FRONTIER_QUERY, hit, runs, 1)
        # An absent Scope guarantees a miss even in a densely populated frontier.
        frontier_miss = _latency(conn, _FRONTIER_QUERY, ("absent-scope", SOURCE_WINDOW_TRIGGER_NAME, 0), runs, 0)
        exact_params = conn.execute(
            "SELECT scope_id, binding_name, block_id FROM pc_memory_work_blocks LIMIT 1"
        ).fetchone()
        exact = _latency(conn, _EXACT_QUERY, exact_params, runs, 1)
        with sqlite3.connect(":memory:") as backup:
            started = time.perf_counter()
            conn.backup(backup)
            backup_seconds = time.perf_counter() - started
            backup_rows = backup.execute("SELECT COUNT(*) FROM pc_memory_work_blocks").fetchone()[0]
    total = space["table_bytes"] + sum(space["index_bytes"].values())
    print(
        json.dumps({
            "count": count,
            "state": state,
            "scope_count": scopes,
            "history": history,
            "field_mode": field_mode,
            "active_rows": active_rows,
            "accepted_rows": count - active_rows,
            "block_id_characters": 36,
            "binding_characters": len(SOURCE_WINDOW_TRIGGER_NAME) if field_mode == "representative" else 128,
            "file_bytes": path.stat().st_size,
            **space,
            "block_storage_bytes": total,
            "bytes_per_block": total / count,
            "frontier_hit": frontier_hit,
            "frontier_miss": frontier_miss,
            "exact_hit": exact,
            "backup_seconds": backup_seconds,
            "backup_rows": backup_rows,
        }),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counts", type=int, nargs="+", default=[1000, 10_000, 100_000])
    parser.add_argument("--states", nargs="+", choices=["active", "accepted", "mixed"], default=["mixed"])
    parser.add_argument(
        "--field-modes", nargs="+", choices=["representative", "near_limit"], default=["representative"]
    )
    parser.add_argument("--scope-counts", type=int, nargs="+", default=[1])
    parser.add_argument("--histories", nargs="+", choices=["distinct", "repeated"], default=["distinct"])
    parser.add_argument("--runs", type=int, default=200)
    args = parser.parse_args()
    if args.runs < 1 or any(n < 1 for n in (*args.counts, *args.scope_counts)):
        parser.error("counts, scope counts and runs must be positive")
    if "repeated" in args.histories and "active" in args.states:
        parser.error("repeated frontiers cannot have multiple active blocks; use mixed or accepted")
    print(
        json.dumps({
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "platform": platform.platform(),
            "backend": "sqlite",
            "cache": "warm in-process",
        }),
        flush=True,
    )
    for count, state, field_mode, scopes, history in itertools.product(
        args.counts, args.states, args.field_modes, args.scope_counts, args.histories
    ):
        with tempfile.TemporaryDirectory(prefix="pc-block-capacity-") as temporary:
            _measure(
                Path(temporary),
                count=count,
                state=state,
                scopes=min(scopes, count),
                history=history,
                field_mode=field_mode,
                runs=args.runs,
            )


if __name__ == "__main__":
    main()
