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
# ruff: noqa: TRY003, TRY301 -- Explicit benchmark validation and cleanup lifecycle.
"""Compare frozen LOCATE and current production index methods in disposable databases.

No SQL replacement or private capability override is used. The baseline module is
loaded from an explicit Git commit; both classes use the current shared schema.
Connection secrets are read only by execution, never emitted into evidence.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import re
import statistics
import subprocess
import sys
import time
import types
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import bindparam, create_engine, delete, event, insert, select, text  # noqa: E402
from sqlalchemy.engine import URL  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from powercontext.artifacts import ArtifactRef  # noqa: E402
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent  # noqa: E402
from powercontext.builtin.artifacts.memory.canonical import canonical_json  # noqa: E402
from powercontext.builtin.artifacts.search import AdmissionFloor, fts_query_requirements  # noqa: E402
from powercontext.builtin.persistence.atomic_memory_index import (  # noqa: E402
    AtomicMemoryIndexFilter,
    AtomicMemoryProjection,
    AtomicMemoryProjectionPublisher,
    AtomicMemoryRelatedRequest,
    AtomicMemorySearchRequest,
)
from powercontext.builtin.persistence.oceanbase.atomic_memory_index import OceanBaseAtomicMemoryIndex  # noqa: E402
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE, SHARED_METADATA  # noqa: E402
from powercontext.builtin.tags import TagFilter  # noqa: E402

BASE_REF = "c4b9b540a6daa6514be83f1696e0fccf18c5c147"
INDEX_PATH = "src/powercontext/builtin/persistence/oceanbase/atomic_memory_index.py"
MAIN_SCOPE = "native-benchmark-main"
ARMS = ("baseline", "automatic")
DB_PATTERN = re.compile(r"pc_fts_bench_\d{8}_[a-f0-9]{16}_v(?:4601|501)")
RAW_REL_TOL = 1e-12
RAW_ABS_TOL = 1e-12
RECOVERY_FLOOR = AdmissionFloor(lexical_coverage=0.20)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def emit(output: Path, name: str, value: Any) -> None:
    with (output / f"{name}.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def safe_error(error: BaseException) -> dict[str, Any]:
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        original = getattr(current, "orig", current)
        args = getattr(original, "args", ())
        if args and isinstance(args[0], int):
            return {"type": type(original).__name__, "code": args[0]}
        current = current.__cause__
    return {"type": type(error).__name__, "code": None}


def frozen_baseline(ref: str) -> tuple[type, dict[str, str]]:
    if not re.fullmatch(r"[0-9a-f]{40}", ref):
        raise ValueError("Baseline must be a full lowercase Git commit hash")
    commit = subprocess.check_output(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=ROOT, text=True).strip()  # noqa: S603, S607 -- Validated commit in the trusted repository, no shell.
    source = subprocess.check_output(["git", "show", f"{commit}:{INDEX_PATH}"], cwd=ROOT, text=True)  # noqa: S603, S607 -- Fixed path in the resolved repository commit.
    module = types.ModuleType("powercontext.builtin.persistence.oceanbase._benchmark_frozen_baseline")
    module.__file__ = f"git:{commit}:{INDEX_PATH}"
    sys.modules[module.__name__] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102 -- Execute the explicitly selected trusted repository implementation.
    return module.OceanBaseAtomicMemoryIndex, {
        "commit": commit,
        "module_sha256": hashlib.sha256(source.encode()).hexdigest(),
    }


@dataclass
class Document:
    artifact_id: str
    scope: str
    kind: str
    body: str
    tags: tuple[str, ...] = ("keep",)
    revision: int = 1
    state_version: int = 0
    generation: int = 0
    prepared: Any = None
    content: bytes = b""


@dataclass(frozen=True)
class Query:
    name: str
    body: str
    filters: AtomicMemoryIndexFilter = field(default_factory=AtomicMemoryIndexFilter)
    scope: str = MAIN_SCOPE
    limit: int = 30


def controlled_queries() -> list[Query]:
    return [Query(f"terms-{n}", " ".join(f"q{i:04d}" for i in range(n))) for n in (1, 2, 8, 17, 128, 485)]


def controlled_fixtures() -> list[Document]:
    result = []
    for query in controlled_queries():
        words = query.body.split()
        required = 1 if len(words) <= 2 else max(2, math.ceil(len(words) * 0.25))
        cases = [
            ("threshold", " ".join(words[:required]), MAIN_SCOPE),
            ("below", " ".join(words[: required - 1]) or "unrelated", MAIN_SCOPE),
            ("repeat", " ".join([words[0]] * 64), MAIN_SCOPE),
            ("substring", " ".join(f"x{word}x" for word in words), MAIN_SCOPE),
            ("foreign", query.body, MAIN_SCOPE + "-other"),
        ]
        result.extend(Document(f"{query.name}-{name}", scope, "fact", body) for name, body, scope in cases)
    return result


def semantic_data() -> tuple[list[Document], list[Query]]:
    documents = controlled_fixtures()
    queries = controlled_queries()
    for number in range(64):
        documents.append(
            Document(
                f"enumerate-{number:03}",
                MAIN_SCOPE,
                "fact" if number % 2 else "preference",
                "enumalpha enumbeta enumgamma",
                ("keep",) if number % 3 else ("drop",),
                revision=2,
                state_version=number % 5,
                generation=number % 7,
            )
        )
    queries.extend([
        Query("enumerate-over-limit", "enumalpha", limit=3),
        Query(
            "kind-and-tag",
            "enumalpha",
            AtomicMemoryIndexFilter(kind="fact", tag_filter=TagFilter(tags=("keep",))),
            limit=3,
        ),
        Query("duplicate-query", "enumalpha enumalpha enumalpha enumbeta enumbeta", limit=3),
    ])
    gate = "gatea gateb gatec gated gatee gatef gateg gateh"
    documents.extend(Document(f"gate-decoy-{i:03}", MAIN_SCOPE, "fact", " ".join(["gatea"] * 100)) for i in range(40))
    documents.extend(Document(f"gate-eligible-{i:03}", MAIN_SCOPE, "fact", "gatea gateb") for i in range(4))
    queries.append(Query("admission-before-limit", gate, limit=2))
    for scope in (MAIN_SCOPE, MAIN_SCOPE.upper(), MAIN_SCOPE + "-other"):
        documents.append(Document("same-identity", scope, "fact", "scopeunique evidence"))
    queries.append(Query("scope-isolation", "scopeunique"))
    for name, body in [
        ("chinese", "部署 恢复 监控 方案"),
        ("identifier", "PowerContext atomic_memory scope_id"),
        ("unicode", "CAFÉ résumé naïve"),
    ]:
        documents.append(Document(name, MAIN_SCOPE, "fact", body))
        documents.append(Document(name + "-other", MAIN_SCOPE + "-other", "fact", body))
        queries.append(Query(name, body))
    return documents, queries


def scale_documents(dataset: str, size: int, corpus: list[dict[str, str]]) -> list[Document]:
    fixtures = controlled_fixtures()
    words = [f"q{i:04d}" for i in range(485)]
    result = []
    for number in range(size):
        if dataset == "controlled" and number < len(fixtures):
            result.append(fixtures[number])
            continue
        scope = MAIN_SCOPE if number % 5 != 4 else MAIN_SCOPE + "-other"
        if dataset == "corpus":
            value = corpus[number % len(corpus)]
            kind, body = value.get("kind", "fact"), value["text"]
        else:
            count = (485, 121, 32, 1)[number % 4]
            body = " ".join(words[:count])
            if number % 7 == 0:
                # Keep the longest fixture below AtomicMemoryContent's byte limit.
                body = " ".join([body] * (2 if count == 485 else 4))
            kind, body = "fact", f"{body} deployment recovery observation sample{number:09d}"
        result.append(Document(f"{dataset}-{number:09d}", scope, kind, body, state_version=number % 3))
    return result


async def prepare_documents(index: Any, documents: list[Document]) -> None:
    publisher = AtomicMemoryProjectionPublisher(index)
    cache = {}
    for document in documents:
        key = (document.kind, document.body)
        if key not in cache:
            content = AtomicMemoryContent(kind=document.kind, text=document.body)
            cache[key] = (
                await publisher.prepare(content),
                canonical_json(content.model_dump(mode="json", by_alias=True)),
            )
        document.prepared, document.content = cache[key]


def eligible_documents(
    documents: list[Document], query: Query, *, floor: AdmissionFloor | None = None
) -> dict[str, Document]:
    terms, required = fts_query_requirements(query.body, floor=floor)
    result = {}
    for document in documents:
        if document.scope != query.scope or query.filters.kind not in (None, document.kind):
            continue
        tags = query.filters.tag_filter
        if tags is not None:
            matches = [key in document.tags for key in tags.keys]
            if not (all(matches) if tags.match == "all" else any(matches)):
                continue
        if len(set(terms) & set(document.prepared.searchable_text.split())) >= required:
            result[document.artifact_id] = document
    return result


def validate_hits(hits: Any, heads: Any, documents: list[Document], query: Query, operation: str) -> dict[str, Any]:
    expected = eligible_documents(documents, query)
    ids = [hit.artifact_ref.artifact_id for hit in hits]
    count = len(expected) if operation == "enumerate" else min(query.limit, len(expected))
    checks = {
        "eligible_ids": set(ids) <= set(expected),
        "unique_ids": len(ids) == len(set(ids)),
        "expected_count": len(ids) == count,
        "complete_set": set(ids) == set(expected) if operation == "enumerate" or len(expected) <= query.limit else None,
    }
    checks["identity_and_content"] = all(
        identity in expected
        and hit.artifact_ref.revision == expected[identity].revision
        and hit.state_version == expected[identity].state_version
        and hit.kind == expected[identity].kind
        and hit.text == expected[identity].body
        for hit, identity in zip(hits, ids, strict=True)
    )
    checks["raw_score_contract"] = all(
        hit.channel_scores
        and "text" in hit.channel_scores
        and hit.channel_scores["text"].metric == "oceanbase_match"
        and hit.channel_scores["text"].higher_is_better
        and math.isfinite(hit.channel_scores["text"].raw)
        and hit.channel_scores["text"].raw > 0
        for hit in hits
    )
    if operation == "enumerate":
        checks["authority_heads"] = set(heads) == set(ids) and all(
            head["scope_id"] == query.scope
            and head["family"] == "atomic-memory"
            and head["artifact_id"] == identity
            and head["revision"] == expected[identity].revision
            and head["governance_generation"] == expected[identity].generation
            and head["lifecycle_state"] == "active"
            and bytes(head["content"]) == expected[identity].content
            for identity, head in heads.items()
            if identity in expected
        )
    return {"passed": all(value is not False for value in checks.values()), "eligible_count": len(expected), **checks}


class SqlObservation:
    def __init__(self) -> None:
        self.current: list[dict[str, Any]] | None = None

    def record(self, connection: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
        if self.current is None:
            return
        upper = statement.upper()
        if "PC_ATOMIC_MEMORY_CURRENT" in upper and "SELECT" in upper:
            self.current.append({
                "sql_sha256": digest(statement),
                "compiled_sql_chars": len(statement),
                "native_match": "SEARCHABLE_TEXT^1" in upper and "SCORE()" in upper,
                "locate_count": upper.count("LOCATE("),
                "contains_limit": "LIMIT" in upper,
                "parameter_count": len(parameters) if isinstance(parameters, (dict, tuple, list)) else None,
            })


def read_config(path: Path) -> dict[str, Any]:
    config = json.loads(path.read_text())
    if not all(isinstance(config.get(key), str) and config[key] for key in ("host", "username", "password")):
        raise ValueError("Connection configuration needs nonempty host, username and password")
    return config


def connection_url(config: dict[str, Any], version: str, database: str | None, *, asynchronous: bool) -> URL:
    return URL.create(
        "mysql+aiomysql" if asynchronous else "mysql+pymysql",
        username=config["username"],
        password=config["password"],
        host=config["host"],
        port=int(config["ports"][version]),
        database=database,
        query={"charset": "utf8mb4"},
    )


def config_identity(config: dict[str, Any], versions: list[str]) -> str:
    return digest({
        "host": config["host"],
        "username": config["username"],
        "ports": {v: config["ports"][v] for v in versions},
    })


def owned_sessions(admin: Any, database: str, sessions: list[int]) -> list[dict[str, Any]]:
    statement = text(
        "SELECT ID, DB, COMMAND, STATE FROM information_schema.PROCESSLIST WHERE DB = :database OR ID IN :sessions"
    ).bindparams(bindparam("sessions", expanding=True))
    rows = admin.execute(statement, {"database": database, "sessions": sessions}).mappings()
    return [
        {"id": int(row["ID"]), "command": row["COMMAND"], "state": row["STATE"]}
        for row in rows
        if str(row["COMMAND"]).lower() != "sleep" or "SESSION_KILLED" in str(row["STATE"])
    ]


async def drain(admin: Any, output: Path, version: str, entry: dict[str, Any]) -> None:
    started, clean = time.monotonic(), 0
    while True:
        active = owned_sessions(admin, entry["database"], entry["session_ids"])
        clean = clean + 1 if not active else 0
        emit(
            output,
            "drain",
            {
                "version": version,
                "active": active,
                "consecutive_clean": clean,
                "elapsed_seconds": time.monotonic() - started,
            },
        )
        if clean >= 2:
            return
        if time.monotonic() - started >= 180:
            raise TimeoutError("Owned database still has active queries; stopped to avoid contamination")
        await asyncio.sleep(5)


async def cleanup(config: dict[str, Any], journal: dict[str, Any], output: Path) -> bool:
    if journal["target_fingerprint"] != config_identity(config, list(journal["databases"])):
        raise ValueError("Cleanup targets differ from the journal")
    complete = True
    for version, entry in journal["databases"].items():
        if entry["status"] in {"planned", "dropped"}:
            continue
        database = entry["database"]
        if (
            not DB_PATTERN.fullmatch(database)
            or database != f"pc_fts_bench_{journal['date']}_{journal['run_id']}_{version}"
        ):
            raise ValueError("Journal database is not owned by this run")
        engine = create_engine(
            connection_url(config, version, None, asynchronous=False),
            hide_parameters=True,
            isolation_level="AUTOCOMMIT",
            connect_args={"connect_timeout": 15, "read_timeout": 60},
        )
        try:
            with engine.connect() as admin:
                active = owned_sessions(admin, database, entry["session_ids"])
                if active:
                    raise TimeoutError("Cleanup deferred while owned queries remain")
                admin.execute(text(f"DROP DATABASE IF EXISTS `{database}`"))
                remaining = admin.scalar(
                    text("SELECT COUNT(*) FROM information_schema.schemata WHERE schema_name = :database"),
                    {"database": database},
                )
                if remaining:
                    raise RuntimeError("Owned database remained after DROP")
                entry["status"] = "dropped"
                emit(output, "cleanup", {"version": version, "database": database, "status": "dropped", "remaining": 0})
        except Exception as error:
            complete = False
            emit(
                output,
                "cleanup",
                {"version": version, "database": database, "status": "pending", "error": safe_error(error)},
            )
        finally:
            engine.dispose()
            write_json(output / "journal.json", journal)
    return complete


class Experiment:
    def __init__(
        self, args: argparse.Namespace, config: dict[str, Any], baseline: type, journal: dict[str, Any]
    ) -> None:
        self.args, self.config, self.baseline = args, config, baseline
        self.journal = journal
        self.records: list[dict[str, Any]] = []
        self.comparisons: list[dict[str, Any]] = []
        self.disabled: set[tuple[Any, ...]] = set()
        self.observation = SqlObservation()
        self.connection: Any = None

    @asynccontextmanager
    async def maintenance(self, common: dict[str, Any], operation: str) -> AsyncIterator[None]:
        record = {**common, "operation": operation, "maintenance_timeout_us": 180000000}
        began = time.perf_counter()
        emit(self.args.output, "maintenance", {**record, "status": "started"})
        try:
            await self.connection.exec_driver_sql("SET SESSION ob_query_timeout=180000000")
            yield
        except Exception as error:
            emit(self.args.output, "maintenance", {**record, "status": "error", "error": safe_error(error)})
            raise
        else:
            emit(
                self.args.output,
                "maintenance",
                {**record, "status": "ok", "duration_ms": (time.perf_counter() - began) * 1000},
            )
        finally:
            try:
                await self.connection.exec_driver_sql("SET SESSION ob_query_timeout=10000000")
                timeout = int(await self.connection.scalar(text("SELECT @@ob_query_timeout")))
                if timeout != 10000000:
                    raise ValueError("Maintenance did not restore the 10 second query timeout")
            except Exception as error:
                emit(
                    self.args.output,
                    "maintenance",
                    {**record, "operation": "restore-query-timeout", "status": "error", "error": safe_error(error)},
                )
                raise
            emit(
                self.args.output,
                "maintenance",
                {**record, "operation": "restore-query-timeout", "status": "ok", "session_timeout_us": timeout},
            )

    async def connect(self, engine: Any, entry: dict[str, Any]) -> None:
        self.connection = await engine.connect()
        await self.connection.exec_driver_sql("SET SESSION ob_query_timeout=10000000")
        session_id = int(await self.connection.scalar(text("SELECT CONNECTION_ID()")))
        if session_id not in entry["session_ids"]:
            entry["session_ids"].append(session_id)
        write_json(self.args.output / "journal.json", self.journal)

    async def reconnect(self, engine: Any, entry: dict[str, Any]) -> None:
        recovery = {"database": entry["database"], "steps": []}
        for operation in ("rollback", "invalidate", "close"):
            try:
                async with asyncio.timeout(10):
                    await getattr(self.connection, operation)()
                recovery["steps"].append({"operation": operation, "status": "ok"})
            except Exception as error:
                recovery["steps"].append({"operation": operation, "status": "error", "error": safe_error(error)})
        await self.connect(engine, entry)
        recovery["session_timeout_us"] = int(await self.connection.scalar(text("SELECT @@ob_query_timeout")))
        emit(self.args.output, "recovery", recovery)
        if recovery["session_timeout_us"] != 10000000:
            raise ValueError("Recovered connection does not retain the 10 second timeout")

    async def publish(self, index: Any, documents: list[Document]) -> None:
        await prepare_documents(index, documents)
        for lower in range(0, len(documents), 200):
            batch = documents[lower : lower + 200]
            by_scope = defaultdict(list)
            for item in batch:
                by_scope[item.scope].append(
                    AtomicMemoryProjection(
                        ArtifactRef(family="atomic-memory", artifact_id=item.artifact_id, revision=item.revision),
                        item.state_version,
                        item.prepared,
                        item.tags,
                    )
                )
            await self.connection.execute(
                insert(ARTIFACTS_TABLE),
                [
                    {
                        "scope_id": d.scope,
                        "family": "atomic-memory",
                        "artifact_id": d.artifact_id,
                        "revision": d.revision,
                        "content": d.content,
                    }
                    for d in batch
                ],
            )
            await self.connection.execute(
                insert(ARTIFACT_HEADS_TABLE),
                [
                    {
                        "scope_id": d.scope,
                        "family": "atomic-memory",
                        "artifact_id": d.artifact_id,
                        "revision": d.revision,
                        "governance_generation": d.generation,
                        "lifecycle_state": "active",
                    }
                    for d in batch
                ],
            )
            for scope, projections in by_scope.items():
                await index.replace_many(self.connection, scope, projections)

    async def population(self, index: Any, documents: list[Document], common: dict[str, Any]) -> None:
        began = time.perf_counter()
        result = await self.connection.exec_driver_sql("ANALYZE TABLE pc_atomic_memory_current")
        if result.returns_rows:
            result.all()
        emit(
            self.args.output,
            "maintenance",
            {**common, "operation": "ANALYZE", "status": "ok", "duration_ms": (time.perf_counter() - began) * 1000},
        )
        rows = (
            await self.connection.execute(
                select(index.table.c.scope_id, index.table.c.artifact_id, index.table.c.searchable_text).order_by(
                    index.table.c.scope_id, index.table.c.artifact_id
                )
            )
        ).all()
        expected = sorted((d.scope, d.artifact_id, d.prepared.searchable_text) for d in documents)
        actual_hash, expected_hash = digest([list(row) for row in rows]), digest(expected)
        emit(
            self.args.output,
            "population",
            {
                **common,
                "rows": len(rows),
                "main_scope_rows": sum(d.scope == MAIN_SCOPE for d in documents),
                "data_sha256": actual_hash,
                "expected_sha256": expected_hash,
            },
        )
        if actual_hash != expected_hash:
            raise ValueError("Stored data differs from prepared projections")

    async def call(
        self, index: Any, documents: list[Document], query: Query, operation: str, common: dict[str, Any]
    ) -> dict[str, Any]:
        terms, required = fts_query_requirements(query.body)
        record = {
            **common,
            "query": query.name,
            "query_sha256": digest(query.body),
            "term_count": len(terms),
            "required": required,
            "operation": operation,
            "server_query_timeout_us": 10000000,
            "client_deadline_seconds": 60,
        }
        hits, heads, recovered = (), None, False
        self.observation.current = []
        started = time.perf_counter()
        try:
            async with asyncio.timeout(60):
                if operation == "search":
                    channels = await index.search(
                        self.connection,
                        query.scope,
                        AtomicMemorySearchRequest(query.body, query.filters, limit=query.limit),
                    )
                    hits, heads = channels.fts, None
                elif operation == "enumerate":
                    hits, heads = await index.enumerate_related_with_heads(
                        self.connection, query.scope, AtomicMemoryRelatedRequest(query.body, query.filters)
                    )
                else:
                    recovered = await index.probe_recoverable(
                        self.connection,
                        query.scope,
                        AtomicMemorySearchRequest(query.body, query.filters, limit=query.limit),
                        RECOVERY_FLOOR,
                    )
            record["duration_ms"] = (time.perf_counter() - started) * 1000
            record["status"] = "ok"
            if operation == "recovery":
                current = eligible_documents(documents, query)
                lower = eligible_documents(documents, query, floor=RECOVERY_FLOOR)
                gained = set(lower) - set(current)
                record.update(recoverable=recovered, lower_lexical_coverage=RECOVERY_FLOOR.lexical_coverage)
                record["validation"] = {
                    "passed": type(recovered) is bool and recovered == bool(gained),
                    "expected_recoverable": bool(gained),
                    "current_eligible_count": len(current),
                    "lower_eligible_count": len(lower),
                    "gained_count": len(gained),
                }
            else:
                record.update(
                    ids=[h.artifact_ref.artifact_id for h in hits],
                    raw_scores=[[h.artifact_ref.artifact_id, h.channel_scores["text"].raw] for h in hits],
                    scores=[[h.artifact_ref.artifact_id, h.score] for h in hits],
                )
                record["validation"] = validate_hits(hits, heads, documents, query, operation)
            native = any(item["native_match"] for item in self.observation.current)
            locate = any(item["locate_count"] for item in self.observation.current)
            record["validation"]["expected_sql_path"] = (
                native and not locate if common["arm"] == "automatic" else locate and not native
            )
            record["validation"]["passed"] &= record["validation"]["expected_sql_path"]
            if not record["validation"]["passed"]:
                record["status"] = "invalid"
        except Exception as error:
            record.update(status="error", duration_ms=(time.perf_counter() - started) * 1000, error=safe_error(error))
        finally:
            record["sql_observations"] = self.observation.current
            self.observation.current = None
        emit(self.args.output, "runs", record)
        self.records.append(record)
        return record

    def compare(self, records: list[dict[str, Any]], common: dict[str, Any]) -> None:
        successful = {
            arm: [r for r in records if r["arm"] == arm and r["status"] == "ok" and not r["warmup"]] for arm in ARMS
        }
        comparable = all(successful.values())
        result = {**common, "successful_counts": {k: len(v) for k, v in successful.items()}, "comparable": comparable}
        if comparable and common["operation"] == "recovery":
            reference = successful["baseline"][0]["recoverable"]
            result["recoverable_equal"] = all(
                r["recoverable"] == reference for records_for_arm in successful.values() for r in records_for_arm
            )
            result["passed"] = result["recoverable_equal"]
        elif comparable:
            reference = successful["baseline"][0]
            all_successes = successful["baseline"] + successful["automatic"]
            result["ordered_ids_equal"] = all(r["ids"] == reference["ids"] for r in all_successes)
            result["raw_scores_exact"] = all(r["raw_scores"] == reference["raw_scores"] for r in all_successes)
            raw = dict(reference["raw_scores"])
            result["raw_scores_close"] = all(
                set(dict(r["raw_scores"])) == set(raw)
                and all(
                    math.isclose(value, raw[identity], rel_tol=RAW_REL_TOL, abs_tol=RAW_ABS_TOL)
                    for identity, value in r["raw_scores"]
                )
                for r in all_successes
            )
            result["max_raw_abs_delta"] = max(
                (
                    abs(value - raw[identity])
                    for r in all_successes
                    for identity, value in r["raw_scores"]
                    if identity in raw
                ),
                default=0,
            )
            result["passed"] = result["ordered_ids_equal"] and result["raw_scores_close"]
        else:
            result["passed"] = None
        emit(self.args.output, "comparisons", result)
        self.comparisons.append(result)

    async def group(
        self,
        indexes: dict[str, Any],
        documents: list[Document],
        query: Query,
        operation: str,
        common: dict[str, Any],
        engine: Any,
        admin: Any,
        entry: dict[str, Any],
    ) -> None:
        timeout = int(await self.connection.scalar(text("SELECT @@ob_query_timeout")))
        if timeout != 10000000:
            raise ValueError("Measured query group requires a verified 10 second server timeout")
        emit(
            self.args.output,
            "query-timeout-checks",
            {**common, "query": query.name, "operation": operation, "session_timeout_us": timeout},
        )
        slots = [(True, arm) for arm in ARMS] if common["phase"] == "benchmark" else []
        slots += [
            (False, arm)
            for _ in range(self.args.cycles if common["phase"] == "benchmark" else 1)
            for arm in ("baseline", "automatic", "automatic", "baseline")
        ]
        records = []
        for slot, (warmup, arm) in enumerate(slots):
            metadata = {**common, "arm": arm, "warmup": warmup, "slot": slot}
            key = (common["version"], common["dataset"], query.name, operation, arm)
            if key in self.disabled:
                record = {
                    **metadata,
                    "query": query.name,
                    "operation": operation,
                    "status": "skipped",
                    "reason": "Earlier same-query arm failed",
                }
                emit(self.args.output, "runs", record)
                self.records.append(record)
            else:
                record = await self.call(indexes[arm], documents, query, operation, metadata)
            records.append(record)
            if record["status"] == "invalid":
                raise ValueError("Production result or selected SQL path failed validation")
            if record["status"] == "error":
                self.disabled.add(key)
                await self.reconnect(engine, entry)
                await drain(admin, self.args.output, common["version"], entry)
                if common["phase"] != "benchmark":
                    raise ValueError("Semantic or complete-enumeration call failed")
        self.compare(records, {**common, "query": query.name, "operation": operation})
        if common["phase"] != "benchmark" and self.comparisons[-1]["passed"] is False:
            raise ValueError("Semantic or complete-enumeration results differ")
        print(json.dumps({**common, "query": query.name, "operation": operation, "phase_done": True}), flush=True)
        self.summarize()

    def summarize(self) -> None:
        groups = defaultdict(list)
        for record in self.records:
            key = tuple(
                record[k] for k in ("phase", "version", "dataset", "size", "query", "operation", "arm", "warmup")
            )
            groups[key].append(record)
        summary = []
        for key, records in groups.items():
            successes = [r for r in records if r["status"] == "ok"]
            summary.append({
                **dict(
                    zip(
                        ("phase", "version", "dataset", "size", "query", "operation", "arm", "warmup"), key, strict=True
                    )
                ),
                "slots": len(records),
                "attempts": sum(r["status"] != "skipped" for r in records),
                "successes": len(successes),
                "errors": sum(r["status"] == "error" for r in records),
                "invalid": sum(r["status"] == "invalid" for r in records),
                "skipped": sum(r["status"] == "skipped" for r in records),
                "successful_median_ms": statistics.median(r["duration_ms"] for r in successes) if successes else None,
                "successful_max_ms": max((r["duration_ms"] for r in successes), default=None),
            })
        write_json(self.args.output / "summary.json", summary)

    async def version(  # noqa: C901 -- Keep ordered database lifecycle together.
        self, version: str, entry: dict[str, Any], corpus: dict[str, Any], journal: dict[str, Any]
    ) -> None:
        admin_engine = create_engine(
            connection_url(self.config, version, None, asynchronous=False),
            isolation_level="AUTOCOMMIT",
            hide_parameters=True,
            connect_args={"connect_timeout": 15, "read_timeout": 60},
        )
        engine = create_async_engine(
            connection_url(self.config, version, entry["database"], asynchronous=True),
            isolation_level="AUTOCOMMIT",
            hide_parameters=True,
            connect_args={"connect_timeout": 15},
        )
        event.listen(engine.sync_engine, "before_cursor_execute", self.observation.record)
        try:
            with admin_engine.connect() as admin:
                entry["status"] = "creating"
                write_json(self.args.output / "journal.json", journal)
                admin.execute(text(f"CREATE DATABASE `{entry['database']}` CHARACTER SET utf8mb4 COLLATE utf8mb4_bin"))
                entry["status"] = "created"
                write_json(self.args.output / "journal.json", journal)
                await self.connect(engine, entry)
                if await self.connection.scalar(text("SELECT DATABASE()")) != entry["database"]:
                    raise ValueError("Connected to a different database")
                indexes = {"baseline": self.baseline(), "automatic": OceanBaseAtomicMemoryIndex()}
                async with self.maintenance({"version": version, "phase": "setup"}, "initialize-schema"):
                    for index in indexes.values():
                        await index.initialize(self.connection)
                    await self.connection.run_sync(
                        lambda sync: SHARED_METADATA.create_all(sync, tables=(ARTIFACTS_TABLE, ARTIFACT_HEADS_TABLE))
                    )
                metadata = {
                    "version": await self.connection.scalar(text("SELECT VERSION()")),
                    "session_timeout_us": await self.connection.scalar(text("SELECT @@ob_query_timeout")),
                    "create_table": [
                        list(row)
                        for row in (
                            await self.connection.exec_driver_sql("SHOW CREATE TABLE pc_atomic_memory_current")
                        ).all()
                    ],
                }
                write_json(self.args.output / f"metadata-{version}.json", metadata)
                documents, queries = semantic_data()
                common = {"phase": "semantic", "version": version, "dataset": "semantic", "size": len(documents)}
                async with self.maintenance(common, "publish-and-verify"):
                    await self.publish(indexes["automatic"], documents)
                    await self.population(indexes["automatic"], documents, common)
                for query in queries:
                    for operation in ("search", "enumerate"):
                        await self.group(indexes, documents, query, operation, common, engine, admin, entry)
                if self.args.semantic_only:
                    return
                for dataset in self.args.datasets:
                    common = {"phase": "dataset-reset", "version": version, "dataset": dataset}
                    for table in (indexes["automatic"].table, ARTIFACT_HEADS_TABLE, ARTIFACTS_TABLE):
                        async with self.maintenance({**common, "table": table.name}, "delete-fixture-data"):
                            await self.connection.execute(delete(table))
                    previous = 0
                    for size in self.args.sizes:
                        documents = scale_documents(dataset, size, corpus["memories"])
                        common = {"phase": "benchmark", "version": version, "dataset": dataset, "size": size}
                        async with self.maintenance(common, "publish-and-verify"):
                            await self.publish(indexes["automatic"], documents[previous:])
                            await prepare_documents(indexes["automatic"], documents[:previous])
                            await self.population(indexes["automatic"], documents, common)
                        await drain(admin, self.args.output, version, entry)
                        queries = (
                            [Query(q["name"], q["text"]) for q in corpus["queries"]]
                            if dataset == "corpus"
                            else [q for q in controlled_queries() if q.name in {"terms-128", "terms-485"}]
                        )
                        for query in queries:
                            await self.group(indexes, documents, query, "search", common, engine, admin, entry)
                        if dataset == "controlled":
                            recovery_queries = [
                                next(q for q in controlled_queries() if q.name == "terms-485"),
                                Query("absent-485", " ".join(f"absent{i:04d}" for i in range(485))),
                            ]
                            for query in recovery_queries:
                                await self.group(indexes, documents, query, "recovery", common, engine, admin, entry)
                        if dataset == "controlled" and size <= 1000:
                            await self.group(
                                indexes,
                                documents,
                                Query("complete-over-limit", "q0000"),
                                "enumerate",
                                {**common, "phase": "enumeration"},
                                engine,
                                admin,
                                entry,
                            )
                        previous = size
                        write_json(self.args.output / "journal.json", journal)
        finally:
            self.summarize()
            if self.connection is not None:
                await self.connection.close()
                self.connection = None
            await engine.dispose()
            admin_engine.dispose()


async def execute(args: argparse.Namespace) -> int:  # noqa: C901 -- Explicit run and cleanup outcomes.
    baseline, baseline_metadata = frozen_baseline(args.base_ref)
    corpus = json.loads(args.corpus.read_text())
    if len(corpus["queries"]) != 4 or len(corpus["memories"]) != 24:
        raise ValueError("Expected the fixed 24-memory, four-query corpus")
    if args.prepare_only:
        documents, queries = semantic_data()
        await prepare_documents(OceanBaseAtomicMemoryIndex(), documents)
        print(
            json.dumps(
                {
                    "status": "prepared_without_database",
                    "documents": len(documents),
                    "queries": len(queries),
                    "baseline": baseline_metadata,
                    "eligible_counts": {q.name: len(eligible_documents(documents, q)) for q in queries},
                },
                ensure_ascii=False,
            )
        )
        return 0
    if args.connection_file is None:
        raise ValueError("--connection-file is required for database execution")
    config = read_config(args.connection_file)
    args.output.mkdir(parents=True, exist_ok=True)
    if args.cleanup_only:
        journal = json.loads(args.cleanup_only.read_text())
        return 0 if await cleanup(config, journal, args.output) else 2
    if (args.output / "journal.json").exists() or (args.output / "runs.jsonl").exists():
        raise ValueError("Use a fresh output directory")
    run_id, date = uuid.uuid4().hex[:16], datetime.now(UTC).strftime("%Y%m%d")
    journal = {
        "run_id": run_id,
        "date": date,
        "target_fingerprint": config_identity(config, args.versions),
        "databases": {
            v: {"database": f"pc_fts_bench_{date}_{run_id}_{v}", "status": "planned", "session_ids": []}
            for v in args.versions
        },
    }
    write_json(args.output / "journal.json", journal)
    source_paths = [
        Path(__file__),
        ROOT / INDEX_PATH,
        ROOT / "src/powercontext/builtin/persistence/atomic_memory_index.py",
        ROOT / "src/powercontext/builtin/persistence/atomic_memory_index_schema.py",
        ROOT / "src/powercontext/builtin/artifacts/search.py",
    ]
    head = await asyncio.to_thread(subprocess.check_output, ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True)
    write_json(
        args.output / "experiment.json",
        {
            "baseline": baseline_metadata,
            "head_commit": head.strip(),
            "source_sha256": {
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths
            },
            "corpus_sha256": hashlib.sha256(args.corpus.read_bytes()).hexdigest(),
            "versions": args.versions,
            "sizes": args.sizes,
            "cycles": args.cycles,
            "datasets": args.datasets,
            "semantic_only": args.semantic_only,
            "raw_score_tolerance": {"relative": RAW_REL_TOL, "absolute": RAW_ABS_TOL},
            "method": "Production search/enumerate calls, same schema/data, ABBA after one warmup, 10s server timeout",
        },
    )
    experiment, code = Experiment(args, config, baseline, journal), 0
    finished, experiment_errors = False, 0
    try:
        for version, entry in journal["databases"].items():
            await experiment.version(version, entry, corpus, journal)
        if any(c["passed"] is False for c in experiment.comparisons):
            code = 3
        elif any(r["status"] == "error" for r in experiment.records):
            code = 4
        finished = True
    except Exception as error:
        emit(args.output, "errors", {"phase": "experiment", "error": safe_error(error)})
        experiment_errors += 1
        code = 1
    finally:
        write_json(args.output / "journal.json", journal)
        experiment.summarize()
        if not await cleanup(config, journal, args.output):
            code = code or 2
    status = (
        "complete" if code == 0 else "completed_with_failures" if finished and code in {3, 4} else "stopped_or_failed"
    )
    outcome = {
        "status": status,
        "exit_code": code,
        "errors": sum(r["status"] == "error" for r in experiment.records),
        "experiment_errors": experiment_errors,
        "invalid": sum(r["status"] == "invalid" for r in experiment.records),
        "skipped": sum(r["status"] == "skipped" for r in experiment.records),
        "different_comparisons": sum(c["passed"] is False for c in experiment.comparisons),
    }
    write_json(args.output / "outcome.json", outcome)
    print(json.dumps(outcome), flush=True)
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--connection-file", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--corpus", type=Path, default=ROOT / "evaluation/performance/oceanbase_fts_native/corpus.json")
    parser.add_argument("--base-ref", default=BASE_REF)
    parser.add_argument("--versions", type=lambda s: s.split(","), default=["v4601", "v501"])
    parser.add_argument("--sizes", type=lambda s: [int(v) for v in s.split(",")], default=[100, 1000, 10000])
    parser.add_argument("--datasets", type=lambda s: s.split(","), default=["corpus", "controlled"])
    parser.add_argument("--cycles", type=int, default=2)
    parser.add_argument("--semantic-only", action="store_true")
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="Build fixtures locally; never read connection credentials or connect",
    )
    parser.add_argument("--cleanup-only", type=Path, metavar="JOURNAL")
    args = parser.parse_args()
    if (
        not set(args.versions) <= {"v4601", "v501"}
        or len(set(args.versions)) != len(args.versions)
        or not args.versions
    ):
        parser.error("--versions accepts distinct v4601,v501 only")
    if args.sizes != sorted(set(args.sizes)) or not args.sizes or min(args.sizes) < 30 or args.cycles < 1:
        parser.error("Sizes must increase, fit 30 fixtures, and cycles must be positive")
    if not set(args.datasets) <= {"corpus", "controlled"}:
        parser.error("Datasets are corpus,controlled")
    try:
        return asyncio.run(execute(args))
    except Exception as error:
        print(json.dumps({"status": "failed", "error": safe_error(error)}), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
