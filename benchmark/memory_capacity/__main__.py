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

from __future__ import annotations

import argparse
import asyncio
import json
import os
import platform
from pathlib import Path
from statistics import mean
from time import perf_counter
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import event, text

from powercontext.builtin.artifacts.memory import MemoryEntryInput
from powercontext.builtin.artifacts.memory.canonical import memory_content_bytes
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.config import RuntimeConfig


async def run(args):  # noqa: C901
    if args.backend == "oceanbase":
        url = os.environ.get("POWERCONTEXT_TEST_OCEANBASE_URL")
        if not url:
            return {
                "backend": "oceanbase",
                "status": "not_run",
                "reason": "POWERCONTEXT_TEST_OCEANBASE_URL is not configured",
            }
        database = OceanBaseConfig(url=SecretStr(url))
        database_path = None
    else:
        database_path = args.output.parent / ("memory-capacity-" + uuid4().hex + ".db")
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path.resolve().as_posix()}")
    config = BuiltinConfig(database=database, runtime=RuntimeConfig(memory_compaction_enabled=True))
    output = {
        "backend": args.backend,
        "status": "running",
        "python": platform.python_version(),
        "platform": platform.platform(),
        "counts": args.counts,
        "final_window": args.final_window,
        "measurements": [],
        "reclamation": "WAL checkpoint before samples; VACUUM measured separately"
        if database_path
        else f"observed table bytes after {args.reclamation_delay}s; no forced engine compaction",
    }
    async with open_builtin_contexts(config) as contexts:
        scope_id = "capacity-benchmark-" + uuid4().hex
        service = (await contexts.get(scope_id)).artifacts.memory
        projection_rows = 0

        def record(_connection, cursor, statement, _parameters, _context, _executemany):
            nonlocal projection_rows
            sql = statement.lower().lstrip()
            if sql.startswith(("insert", "update", "delete")) and any(
                name in sql for name in ("pc_memory_entry_heads", "pc_memory_entry_fts")
            ):
                projection_rows += max(cursor.rowcount, 0)

        event.listen(contexts.database.engine.sync_engine, "after_cursor_execute", record)

        async def database_bytes():
            if database_path is not None:
                async with contexts.database.engine.connect() as connection:
                    await connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
                return database_path.stat().st_size
            async with contexts.database.connection() as connection:
                # General table statistics can remain zero while OceanBase already occupies SSTable space.
                return int(
                    await connection.scalar(
                        text(
                            "SELECT COALESCE(SUM(OCCUPY_SIZE), 0) "
                            "FROM oceanbase.DBA_OB_TABLE_SPACE_USAGE WHERE DATABASE_NAME = DATABASE()"
                        )
                    )
                    or 0
                )

        async def snapshot(memory):
            capacity = await service.capacity(memory)
            return {
                "revision": memory.revision,
                "active_entries": capacity.active_entry_count,
                "manifest_entries": capacity.manifest_entry_count,
                "manifest_bytes": capacity.manifest_bytes,
                "database_bytes": await database_bytes(),
            }

        memory = None
        latencies = []
        for number in range(1, max(args.counts) + 1):
            started = perf_counter()
            memory = await service.remember(
                memory=memory,
                entries=(
                    MemoryEntryInput(
                        kind="fact",
                        text=f"Capacity benchmark record {number}; project token item{number}.",
                    ),
                ),
                mode="append",
            )
            latencies.append((perf_counter() - started) * 1000)
            if number in args.counts:
                sample = await snapshot(memory)
                sample.update({
                    "mean_append_ms": mean(latencies),
                    "mean_final_window_append_ms": mean(latencies[-args.final_window :]),
                    "projection_rows_per_append": projection_rows / number,
                })
                output["measurements"].append(sample)
                print(json.dumps(sample), flush=True)
                args.output.write_text(json.dumps(output, indent=2) + "\n", encoding="utf-8")
        assert memory is not None  # noqa: S101
        entries = await service.entries(memory)
        retired_entries = entries[: int(len(entries) * 0.8)]
        retained = entries[-1]
        memory = await service.forget(memory, entries=retired_entries)
        for number in range(config.runtime.memory_compaction_min_tombstone_revisions):
            memory = await service.remember(
                memory=memory,
                entries=(
                    MemoryEntryInput(
                        kind="fact",
                        text=f"Capacity sentinel retained record generation {number}.",
                        entry=retained,
                    ),
                ),
                mode="append",
            )
            assert memory is not None  # noqa: S101
            retained = next(entry for entry in await service.entries(memory) if entry.entry_id == retained.entry_id)
        assert memory is not None  # noqa: S101
        before = await snapshot(memory)
        hits_before = await service.search("Capacity sentinel retained", memories=(memory,), mode="fts")
        preview = await service.compact(memory, dry_run=True)
        writes_before = projection_rows
        compacted = await service.compact(memory)
        compaction_writes = projection_rows - writes_before
        hits_after = await service.search("Capacity sentinel retained", memories=(compacted.memory,), mode="fts")
        identities_before = [(hit.entry_id, hit.entry_version_id) for hit in hits_before.hits]
        identities_after = [(hit.entry_id, hit.entry_version_id) for hit in hits_after.hits]
        output["compaction"] = {
            "before": before,
            "after": await snapshot(compacted.memory),
            "removed_entries": len(compacted.entry_ids),
            "reclaimed_bytes": compacted.reclaimed_bytes,
            "preview_matches": preview.entry_ids == compacted.entry_ids,
            "projection_rows_written": compaction_writes,
            "search_hit_count_before": len(identities_before),
            "search_hit_count_after": len(identities_after),
            "search_identities_preserved": identities_before == identities_after,
        }
        if not identities_before or identities_before != identities_after or compaction_writes:
            raise RuntimeError("capacity benchmark invariants failed")  # noqa: TRY003
        followup = await service.remember(
            memory=compacted.memory,
            entries=(MemoryEntryInput(kind="fact", text="New entry after compaction."),),
            mode="append",
        )
        assert followup is not None  # noqa: S101
        output["compaction"]["followup"] = await snapshot(followup)
        if database_path is not None:
            async with contexts.database.engine.connect() as connection:
                await connection.exec_driver_sql("VACUUM")
            output["compaction"]["post_vacuum_database_bytes"] = await database_bytes()
        else:
            await asyncio.sleep(args.reclamation_delay)
            output["compaction"]["delayed_database_bytes"] = await database_bytes()
        output["compaction"]["followup_canonical_bytes"] = len(memory_content_bytes(followup.content))
        event.remove(contexts.database.engine.sync_engine, "after_cursor_execute", record)
    output["status"] = "completed"
    return output


def main():
    parser = argparse.ArgumentParser(description="Measure the Memory capacity envelope without inference.")
    parser.add_argument("--backend", choices=("sqlite", "oceanbase"), default="sqlite")
    parser.add_argument("--counts", nargs="+", type=int, default=[200, 1000, 5000])
    parser.add_argument("--final-window", type=int, default=100)
    parser.add_argument("--reclamation-delay", type=float, default=30)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.counts) < 5 or max(args.counts) > 5000 or args.final_window < 1 or args.reclamation_delay < 0:
        parser.error("counts must be 5..5000, final-window positive, and reclamation-delay nonnegative")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    result = asyncio.run(run(args))
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(args.output)


if __name__ == "__main__":
    main()
