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

"""Real disposable-backend fixture replay with aggregate-only report artifacts."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import os
import platform
import sqlite3
import time
from collections import defaultdict
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from powercontext_eval_longmemeval_v2.lineage import (
    ARMS,
    EVIDENCE_VERSION,
    GROUPING_VERSION,
    LEXICAL_THRESHOLD,
    LEXICAL_VERSION,
    MANIFEST_SCHEMA,
    EntryRef,
    LineageEvaluationError,
    Material,
    Observation,
    ensure_comparable_lineage_runs,
    evidence_identities,
    exact_representatives,
    measure_pool,
)
from powercontext_eval_longmemeval_v2.lineage_fixtures import FIXTURE_VERSION, Case, fixture_cases


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix in {".py", ".json", ".txt", ".yaml", ".toml"}:
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
    return digest.hexdigest()


@dataclass(frozen=True)
class LineageRun:
    output_dir: Path
    report_path: Path
    manifest_path: Path


@asynccontextmanager
async def _database(backend: str, directory: Path, oceanbase_url_env: str) -> AsyncIterator[Any]:
    from powercontext.builtin.persistence.sqlite import SQLiteConfig

    if backend == "sqlite":
        yield SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'fixture.db'}")
        return
    if backend != "oceanbase":
        raise LineageEvaluationError("supported backends are sqlite and oceanbase")
    configured = os.environ.get(oceanbase_url_env)
    if not configured:
        raise LineageEvaluationError("OceanBase is not configured; no backend execution was performed")
    from pydantic import SecretStr
    from sqlalchemy.engine import make_url

    from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile

    # Never reuse or delete the configured database. Only a newly created fixture DB is owned here.
    name = f"pc_lineage_{uuid4().hex}"
    async with OceanBaseProfile.open(OceanBaseConfig(url=SecretStr(configured)), tables=()) as profile:
        async with profile.database.transaction() as connection:
            await connection.exec_driver_sql(f"CREATE DATABASE `{name}`")
        try:
            url = make_url(configured).set(database=name).render_as_string(hide_password=False)
            yield OceanBaseConfig(url=SecretStr(url))
        finally:
            async with profile.database.transaction() as connection:
                await connection.exec_driver_sql(f"DROP DATABASE `{name}`")


async def _seed(contexts: Any, case: Case) -> dict[EntryRef, tuple[str, ...]]:
    from powercontext.artifacts import ArtifactLineage, ArtifactRef
    from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
    from powercontext.builtin.sources.content import ContentSource
    from powercontext.sources import SourceMaterialization, SourceRef

    scope = f"lineage-fixture-{case.category}"
    context = await contexts.get(scope)
    for source in sorted({entry.source for entry in case.entries if entry.source is not None}):
        body = "\n".join(entry.text for entry in case.entries if entry.source == source)
        await context.sources.add(
            ContentSource(name=source, materialization=SourceMaterialization.CAPTURED, content=body)
        )
    revisions: dict[str, int] = {}
    facts = {}
    service = contexts.atomic_memory.service
    for entry in case.entries:
        previous = revisions.get(entry.key)
        evidence = ()
        if entry.previous:
            key, revision = entry.previous.rsplit(":", 1)
            evidence = (ArtifactRef(family="atomic-memory", artifact_id=key, revision=int(revision)),)
        lineage = ArtifactLineage(
            sources=() if entry.source is None else (SourceRef(source_type="content", source_id=entry.source),),
            artifacts=evidence,
        )
        async with contexts.database.transaction() as connection:
            plan = await service.inspect_change(
                connection,
                scope,
                entry.key,
                AtomicMemoryContent(kind="fact", text=entry.text),
                None,
                expected_revision=previous,
                lineage=lineage,
            )
        prepared = await service.prepare_change(plan)
        async with contexts.database.transaction() as connection:
            result = await service.commit(connection, prepared, None)
        revision = result.primary.ref.revision
        revisions[entry.key] = revision
        facts[EntryRef(scope, entry.key, revision)] = entry.facts
    return facts


async def _observe(memory: Any, hit: Any, scope: str, facts: Mapping[EntryRef, tuple[str, ...]]) -> Observation:
    ref = hit.hit.artifact_ref
    key = EntryRef(scope, ref.artifact_id, ref.revision)
    if key not in facts:
        raise LineageEvaluationError("search returned an entry outside the authorized fixture Scope")
    record = await memory.get(ref.artifact_id, revision=ref.revision)
    annotation = await memory.evidence(ref.artifact_id, revision=ref.revision)
    if (
        annotation.scope_id != scope
        or annotation.artifact_ref != ref
        or record.ref != ref
        or record.artifact.content.text != hit.text
    ):
        raise LineageEvaluationError("evidence does not bind to the exact authorized entry revision")
    materials = tuple(
        Material(
            scope,
            value.source_ref.source_type,
            value.source_ref.source_id,
            value.material_digest,
            value.definition_name,
            value.definition_version,
            value.manifest_fingerprint,
            value.declaration.declaration_version,
            value.declaration.authority,
            value.declaration.verification,
        )
        for value in annotation.sources
    )
    return Observation(
        key,
        record.artifact.content.kind,
        hit.text,
        materials,
        tuple(
            EntryRef(scope, value.artifact_id, value.revision, value.family)
            for value in record.artifact.lineage.artifacts
        ),
        frozenset(facts[key]),
    )


def _outcome(
    pool: tuple[Observation, ...], content: str | None, answers: tuple[str, ...]
) -> dict[str, int | float | None]:
    expected = set(answers)
    retrieved = set().union(*(entry.facts for entry in pool)) if pool else set()
    delivered = {answer for answer in expected if content is not None and answer in content}
    return {
        "required_facts": len(expected),
        "retrieved_required_facts": len(expected & retrieved),
        "delivered_required_facts": len(delivered),
        "retrieval_missed_facts": len(expected - retrieved),
        "post_retrieval_omitted_facts": len((expected & retrieved) - delivered),
        "task_success": int(expected <= delivered and (bool(expected) or not pool)),
        "negative_control_false_hits": int(not expected and bool(pool)),
        "retrieval_recall": len(expected & retrieved) / len(expected) if expected else None,
        "delivered_recall": len(delivered) / len(expected) if expected else None,
    }


def _aggregate(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, bool], list[dict[str, Any]]] = defaultdict(list)
    for sample in samples:
        grouped[(sample["case_category"], sample["arm"], sample["held_out"])].append(sample)
    rows = []
    for (category, arm, held_out), group in sorted(grouped.items()):
        row: dict[str, Any] = {
            "case_category": category,
            "experiment_arm": arm,
            "held_out": held_out,
            "samples": len(group),
        }
        for section in ("pool", "delivered", "outcome", "omissions", "comparison", "timing_ms"):
            keys = set().union(*(sample[section] for sample in group))
            values = {}
            for key in sorted(keys):
                numbers = [sample[section][key] for sample in group if sample[section][key] is not None]
                if key.endswith(("concentration", "occupancy", "_recall")):
                    values[f"mean_{key}"] = sum(numbers) / len(numbers) if numbers else None
                else:
                    values[key] = sum(numbers) if numbers else None
            row[section] = values
        rows.append(row)
    return rows


async def _replay(config: Any, cases: tuple[Case, ...]) -> tuple[list[dict[str, Any]], dict[str, object]]:
    from powercontext.artifacts import ArtifactAddress
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
    from powercontext.builtin.runtime.models import PrepareContextRequest
    from powercontext.builtin.runtime.prepared_context import PreparedContextBuilder

    rows = []
    async with open_builtin_contexts(BuiltinConfig(database=config)) as contexts:
        index = contexts.atomic_memory.index
        if not index.capabilities.fts:
            raise LineageEvaluationError("backend has no FTS capability; no semantic equivalence is assumed")
        if not callable(getattr(contexts.atomic_memory.for_scope("capability-probe"), "evidence", None)):
            raise LineageEvaluationError("requires #1975 exact Source evidence API; no inferred replacement is allowed")
        facts = {}
        for case in cases:
            facts.update(await _seed(contexts, case))

        # Read exact historical revisions before/after measurement, including all superseded bodies.
        async def authority_snapshot() -> tuple[str, ...]:
            values = []
            for ref in sorted(facts):
                memory = contexts.atomic_memory.for_scope(ref.scope)
                record = await memory.get(ref.artifact, revision=ref.revision)
                evidence = await memory.evidence(ref.artifact, revision=ref.revision)
                values.append(record.model_dump_json() + evidence.model_dump_json())
            return tuple(values)

        before = await authority_snapshot()
        builder = PreparedContextBuilder()
        for case in cases:
            scope = f"lineage-fixture-{case.category}"
            memory = contexts.atomic_memory.for_scope(scope)
            for query in case.queries:
                started = time.perf_counter_ns()
                page = await memory.search(query.text, mode="text", limit=builder.memory_candidate_limit)
                search_ms = (time.perf_counter_ns() - started) / 1_000_000
                started = time.perf_counter_ns()
                pool = tuple([await _observe(memory, hit, scope, facts) for hit in page.hits])
                evidence_ms = (time.perf_counter_ns() - started) / 1_000_000
                started = time.perf_counter_ns()
                pool_metrics = measure_pool(pool)
                pool_measurement_ms = (time.perf_counter_ns() - started) / 1_000_000
                baseline = None
                baseline_pool = tuple(hit.hit.artifact_ref for hit in page.hits)
                for arm in ARMS:
                    started = time.perf_counter_ns()
                    indices = exact_representatives(pool) if arm == ARMS[1] else tuple(range(len(pool)))
                    projected = tuple(pool[index] for index in indices)
                    hits = tuple(page.hits[index] for index in indices)
                    built = builder.build_result(
                        request=PrepareContextRequest(query=query.text, max_bytes=query.max_bytes),
                        scope_id=scope,
                        hits=hits,
                    )
                    build_ms = (time.perf_counter_ns() - started) / 1_000_000
                    selected = set()
                    for origin in built.origins:
                        if isinstance(origin, ArtifactAddress) and origin.scope_id != scope:
                            raise LineageEvaluationError("Builder returned a foreign Scope origin")
                        ref = origin.artifact if isinstance(origin, ArtifactAddress) else origin
                        selected.add((ref.artifact_id, ref.revision))
                    delivered = tuple(
                        entry for entry in projected if (entry.ref.artifact, entry.ref.revision) in selected
                    )
                    started = time.perf_counter_ns()
                    delivered_metrics = measure_pool(delivered)
                    measurement_ms = pool_measurement_ms + (time.perf_counter_ns() - started) / 1_000_000
                    outcome = _outcome(pool, built.context.content, query.answers)
                    if query.category == "cjk_positive" and outcome["retrieval_missed_facts"]:
                        raise LineageEvaluationError(
                            "mandatory #1976 positive baseline failed; diversity comparison refused"
                        )
                    if baseline is None:
                        baseline = (outcome, built.context.content, delivered)
                    base_outcome, base_content, base_delivered = baseline
                    lost = sum(
                        answer in (base_content or "") and answer not in (built.context.content or "")
                        for answer in query.answers
                    )
                    base_evidence = evidence_identities(base_delivered)
                    delivered_evidence = evidence_identities(delivered)
                    lost_evidence = base_evidence - delivered_evidence
                    rows.append(
                        {
                            "case_category": case.category,
                            "held_out": case.held_out,
                            "arm": arm,
                            "pool": pool_metrics,
                            "delivered": delivered_metrics,
                            "outcome": outcome,
                            "omissions": {
                                **asdict(built.omissions),
                                "content_bytes": built.context.content_bytes,
                                "equality_measurement_excluded_candidates": len(pool) - len(projected),
                                "entry_limit_omitted_candidates": len(projected)
                                - len(delivered)
                                - built.omissions.dropped_items,
                            },
                            "comparison": {
                                "false_suppressed_baseline_facts": lost,
                                "lost_baseline_evidence": len(lost_evidence),
                                "lost_baseline_source_evidence": sum(
                                    identity[0] == "source" for identity in lost_evidence
                                ),
                                "lost_baseline_artifact_evidence": sum(
                                    identity[0] == "artifact" for identity in lost_evidence
                                ),
                                "task_regressions": int(
                                    (outcome["task_success"] or 0) < (base_outcome["task_success"] or 0)
                                ),
                                "task_improvements": int(
                                    (outcome["task_success"] or 0) > (base_outcome["task_success"] or 0)
                                ),
                            },
                            "timing_ms": {
                                "retrieval": search_ms,
                                "evidence": evidence_ms,
                                "build": build_ms,
                                "measurement": measurement_ms,
                            },
                        }
                    )
                    if tuple(hit.hit.artifact_ref for hit in page.hits) != baseline_pool:
                        raise LineageEvaluationError("an alternative mutated fixed RRF pool membership or order")
        if await authority_snapshot() != before:
            raise LineageEvaluationError("evaluation mutated authoritative fixture records")
        return rows, {
            "fts": True,
            "vector": bool(index.capabilities.vector),
            "hybrid": bool(index.capabilities.hybrid),
            "exact_source_evidence_api": True,
            "historical_revision_reads": True,
            "historical_revisions_checked": len(facts),
            "authoritative_records_unchanged": True,
            "production_ranking_changed": False,
            "context_receipts_emitted": 0,
            "runtime_events_emitted": 0,
            "measurement_path": "native-scoped-search-and-in-process-builder",
        }


async def run_lineage_fixture(
    *,
    output_dir: Path,
    backend: str,
    runtime_revision: str,
    dependency_state: str = "unknown",
    oceanbase_url_env: str = "POWERCONTEXT_TEST_OCEANBASE_URL",
) -> LineageRun:
    """Execute bounded fixtures, never writing entry/Source content to a report."""
    if output_dir.exists():
        raise LineageEvaluationError("refusing to overwrite an existing report directory")
    if dependency_state not in {"merged", "unmerged", "unknown"}:
        raise LineageEvaluationError("dependency state must be merged, unmerged, or unknown")
    if len(runtime_revision) != 40 or any(char not in "0123456789abcdef" for char in runtime_revision):
        raise LineageEvaluationError("runtime revision must be an exact lowercase Git SHA")
    try:
        import powercontext
    except ImportError:
        raise LineageEvaluationError(
            "install the pinned powercontext[builtin] runtime in the evaluation environment"
        ) from None
    cases = fixture_cases()
    from sqlalchemy.exc import SQLAlchemyError

    try:
        output_dir.parent.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(prefix="pc-lineage-", dir=output_dir.parent) as directory:
            async with _database(backend, Path(directory), oceanbase_url_env) as config:
                samples, capabilities = await _replay(config, cases)
    except LineageEvaluationError:
        raise
    except (SQLAlchemyError, OSError, ValueError):
        # Provider values and URLs are not reportable diagnostic data. Preserve cancellation.
        raise LineageEvaluationError("backend fixture execution failed; no successful report was produced") from None
    package_root = Path(__file__).parent
    runtime_root = Path(powercontext.__file__).parent
    manifest = {
        "schema": MANIFEST_SCHEMA,
        "fixture_version": FIXTURE_VERSION,
        "fixture_sha256": _digest([asdict(case) for case in cases]),
        "runtime_revision": runtime_revision,
        "runtime_source_sha256": _tree_digest(runtime_root),
        "evaluation_source_sha256": _tree_digest(package_root),
        "backend": backend,
        "capabilities": capabilities,
        "dependency_state": dependency_state,
        "grouping_version": GROUPING_VERSION,
        "evidence_version": EVIDENCE_VERSION,
        "lexical_version": LEXICAL_VERSION,
        "lexical_threshold": LEXICAL_THRESHOLD,
        "candidate_limit": 16,
        "top_k": 8,
        "model": None,
        "advisory_jev": {"status": "not_provided", "authority": "none"},
        "environment": {
            "python": platform.python_version(),
            "platform": platform.system(),
            "sqlite": sqlite3.sqlite_version,
            "packages": {name: importlib.metadata.version(name) for name in ("SQLAlchemy", "jieba", "pydantic")},
        },
        "experiment_arm": ARMS[0],
    }
    manifests = [{**manifest, "experiment_arm": arm} for arm in ARMS]
    for value in manifests[1:]:
        ensure_comparable_lineage_runs(manifests[0], value)
    aggregates = _aggregate(samples)
    backend_matrix = [
        {"backend": backend, "status": "executed", "capabilities": capabilities},
        {
            "backend": "oceanbase" if backend == "sqlite" else "sqlite",
            "status": "not_run",
            "reason": "environment_not_configured"
            if backend == "sqlite" and not os.environ.get(oceanbase_url_env)
            else "separate_backend_run_required",
        },
    ]
    report = {
        "schema": "powercontext.lineage-evaluation-report.v1",
        "evidence_version": EVIDENCE_VERSION,
        "classification": "bounded-synthetic-fixture-evaluation",
        "recommendation": "no_product_change",
        "dependency_state": dependency_state,
        "workload": {
            "scope_partitions": len(cases),
            "queries": sum(len(case.queries) for case in cases),
            "held_out_partitions": sum(case.held_out for case in cases),
        },
        "backend_matrix": backend_matrix,
        "aggregates": aggregates,
        "findings": {
            "sampled_lexical_false_relation_pairs": sum(
                row["pool"]["lexical_false_relation_pairs"] for row in aggregates if row["experiment_arm"] == ARMS[0]
            ),
            "equality_measurement_task_improvements": sum(
                row["comparison"]["task_improvements"] for row in aggregates if row["experiment_arm"] == ARMS[1]
            ),
            "alternative_task_regressions": sum(row["comparison"]["task_regressions"] for row in aggregates),
            "alternative_false_suppressed_facts": sum(
                row["comparison"]["false_suppressed_baseline_facts"] for row in aggregates
            ),
            "alternative_lost_evidence": sum(row["comparison"]["lost_baseline_evidence"] for row in aggregates),
        },
        "cost": {"model_calls": 0, "model_cost_usd": 0, "currency": "USD"},
        "limits": [
            "Synthetic fixture oracles are not a full benchmark or production task acceptance.",
            "Shared evidence is not duplicate-fact proof; lexical annotations are advisory only.",
            "Exact-equality collapse is an in-memory measurement projection of the unchanged pool.",
            "Native scoped reads and the pure Builder were exercised; HTTP/MCP and host delivery were not.",
            "Backend results must be executed and reported separately.",
            "Unmerged dependency snapshots do not establish merged-main acceptance.",
        ],
    }
    output_dir.mkdir(parents=True, exist_ok=False)
    _write(output_dir / "manifest.json", manifest)
    _write(output_dir / "arm-manifests.json", manifests)
    for value in manifests:
        _write(output_dir / f"{value['experiment_arm']}.manifest.json", value)
    _write(output_dir / "report.json", report)
    _write(output_dir / "backend-matrix.json", backend_matrix)
    _write(
        output_dir / "command.json",
        {
            "argv": [
                "longmemeval-v2",
                "lineage-fixture",
                "--backend",
                backend,
                "--runtime-revision",
                runtime_revision,
                "--dependency-state",
                dependency_state,
                "--oceanbase-url-env",
                oceanbase_url_env,
                "--output-dir",
                "<new-report-directory>",
            ],
            "prerequisites": "Install the exact recorded Runtime source and evaluation source; compare their digests.",
        },
    )
    (output_dir / "report.md").write_text(_markdown(report, aggregates), encoding="utf-8")
    (output_dir / "decision.md").write_text(
        "# Decision: no product change\n\n"
        "This bounded fixture evaluation measures concentration, lexical relationship uncertainty, fact recall, "
        "false suppression, and final byte-budget omissions separately. Shared Source evidence does not justify "
        "suppressing distinct facts. Neither equality projection nor lexical annotations are a production policy.\n\n"
        f"Measured lexical false-relation pairs across query samples: {report['findings']['sampled_lexical_false_relation_pairs']}. "
        f"Equality-measurement task improvements: {report['findings']['equality_measurement_task_improvements']}. "
        f"Alternative task regressions: {report['findings']['alternative_task_regressions']}. "
        f"False-suppressed baseline facts: {report['findings']['alternative_false_suppressed_facts']}.\n\n"
        f"Lost baseline evidence references: {report['findings']['alternative_lost_evidence']}. "
        "Evidence loss is independent of whether identical text still delivers the same fact.\n\n"
        "A later fixed-pool stable reorder experiment or review-gated relation proposal requires broader held-out "
        "task evidence, merged prerequisites, and separately executed backend validation. No production ranking, "
        "write behavior, lifecycle state, citations, public APIs, or delivery contract is changed.\n\n"
        f"Dependency state: {dependency_state}. See report.json for measured outcomes and explicit limits.\n",
        encoding="utf-8",
    )
    _write(
        output_dir / "artifact-hashes.json",
        {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in sorted(output_dir.iterdir())
            if path.is_file()
        },
    )
    return LineageRun(output_dir, output_dir / "report.json", output_dir / "manifest.json")


def _write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _markdown(report: Mapping[str, Any], rows: list[dict[str, Any]]) -> str:
    lines = [
        "# Scope-local lineage and occupancy evaluation",
        "",
        "Recommendation: **no product change**. Classification: bounded synthetic fixtures.",
        "",
        f"Dependency state: `{report['dependency_state']}`. Models: 0 calls / USD 0.",
        "",
        "Each category is one isolated Scope; only aggregates are exported. Counts sum over that Scope's queries.",
        "",
        "| Case | Arm | Queries | Retrieved facts | Delivered facts | Required facts | False suppression | Task regressions |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for row in rows:
        result, comparison = row["outcome"], row["comparison"]
        lines.append(
            f"| {row['case_category']} | {row['experiment_arm']} | {row['samples']} | "
            f"{result['retrieved_required_facts']} | {result['delivered_required_facts']} | {result['required_facts']} | "
            f"{comparison['false_suppressed_baseline_facts']} | {comparison['task_regressions']} |"
        )
    lines.extend(
        [
            "",
            "## Direct evidence coverage",
            "",
            "Source materializations and exact Artifact citations are distinct evidence identities; counts sum over queries.",
            "",
            "| Case | Arm | Delivered Sources | Delivered Artifacts | Lost Sources | Lost Artifacts |",
            "| --- | --- | ---: | ---: | ---: | ---: |",
        ]
    )
    for row in rows:
        delivered, comparison = row["delivered"], row["comparison"]
        lines.append(
            f"| {row['case_category']} | {row['experiment_arm']} | "
            f"{delivered['unique_source_evidence_count']} | {delivered['unique_artifact_evidence_count']} | "
            f"{comparison['lost_baseline_source_evidence']} | {comparison['lost_baseline_artifact_evidence']} |"
        )
    lines.extend(["", "## Interpretation and limits", ""])
    lines.extend(f"- {value}" for value in report["limits"])
    return "\n".join(lines) + "\n"
