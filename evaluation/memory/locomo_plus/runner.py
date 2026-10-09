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

"""Isolated, resumable LoCoMo-Plus experiments through public Runtime interfaces."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from contextlib import AsyncExitStack
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import ValidationError
from pydantic_ai import capture_run_messages
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pyobvector import AsyncOceanBaseDialect
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

from evaluation.memory.locomo.dataset import LoCoMoSession
from evaluation.memory.locomo.metrics import retrieval_metrics
from evaluation.memory.locomo.runner import (
    _all_atomic_records,
    _atomic_snapshot,
    _lineage_source_ids,
    load_settings,
    normalize_run_id,
    public_configuration,
)
from powercontext.builtin.artifacts.atomic_memory.extraction import atomic_memory_extraction_instructions
from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryStateValue
from powercontext.builtin.artifacts.atomic_memory.reconciliation import ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS
from powercontext.builtin.inference import InvalidInferenceOutputError, character_token_estimator
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    CaptureSource,
    MemoryExtractionProfile,
    RuntimeConfig,
    SearchMemoryRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.config import DatabaseConfig
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.settings import ServerSettings

from .dataset import LoCoMoPlusCase, LoCoMoPlusDataset, render_case_session
from .metrics import render_summary, summarize_observations
from .models import generate, open_model
from .prompts import (
    ANSWER_INSTRUCTIONS,
    ANSWER_INSTRUCTIONS_VERSION,
    build_answer_input,
    build_judge_input,
    replay_judgment,
)

ARMS = ("memory", "memory-source", "query-only", "oracle-cue", "full-context")
HARNESS_VERSION = "powercontext.locomo-plus.v2"

# Only application-authored details are safe to copy verbatim. Provider exception messages can contain credentials.
_KNOWN_DETAILS = frozenset({
    "provider did not return a valid result",
    "provider continuation is not allowed",
    "generator returned the wrong output type",
    "generator returned an invalid output tree",
    "candidate does not cite evidence",
    "candidate cites evidence outside the request",
    "add candidate must not identify an existing entry",
    "candidate has an unsupported intent",
    "revise candidate must identify an active entry",
    "revise candidate does not identify an active entry",
    "an active entry can only be revised once per extraction",
    "provider returned the wrong input type",
    "provider changed input order",
    "provider returned the wrong vector count",
})


def describe_error(error: BaseException) -> dict[str, Any]:
    """Retain exception causes and validation codes, excluding messages and rejected input values."""

    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        item: dict[str, Any] = {"type": type(current).__name__}
        if isinstance(current, InvalidInferenceOutputError):
            if current.operation in {"generate", "embed", "atomic-memory-extract", "atomic-memory-reconcile"}:
                item["operation"] = current.operation
            if current.detail in _KNOWN_DETAILS:
                item["detail"] = current.detail
        if isinstance(current, ValidationError):
            item["validation_errors"] = [
                {"type": value["type"], "location": list(value["loc"])}
                for value in current.errors(include_input=False, include_context=False, include_url=False)
            ]
        chain.append(item)
        current = current.__cause__ or (None if current.__suppress_context__ else current.__context__)
    return {**chain[0], "chain": chain}


def _write_json(path: Path, value: Any) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _append(path: Path, value: Any) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False) + "\n")
        stream.flush()


def _observations(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    return {row["case_id"]: row for line in path.read_text(encoding="utf-8").splitlines() if (row := json.loads(line))}


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _history(case: LoCoMoPlusCase, limit: int | None) -> tuple[LoCoMoSession, ...]:
    if limit is None or len(case.sessions) <= limit:
        return case.sessions
    if case.cue_session_id is None:
        return case.sessions[-limit:]
    cue_index = next(index for index, session in enumerate(case.sessions) if session.session_id == case.cue_session_id)
    start = min(cue_index, len(case.sessions) - limit)
    return case.sessions[start : start + limit]


def _selection(dataset, profile, limit, max_history_sessions):
    if profile not in {"smoke", "full"}:
        raise ValueError("profile must be smoke or full")  # noqa: TRY003
    if limit is not None and limit < 1:
        raise ValueError("limit must be positive")  # noqa: TRY003
    if max_history_sessions is not None and max_history_sessions < 1:
        raise ValueError("max_history_sessions must be positive")  # noqa: TRY003
    history_limit = max_history_sessions
    cases = dataset.selected_cases(mode=profile, limit=limit)
    if not cases:
        raise ValueError("selection contains no cases")  # noqa: TRY003
    return cases, history_limit


def _history_identity(case: LoCoMoPlusCase, sessions: tuple[LoCoMoSession, ...]) -> dict[str, Any]:
    rendered = [
        {"source_id": session.session_id, "content": render_case_session(case, session)} for session in sessions
    ]
    full = [
        {"source_id": session.session_id, "content": render_case_session(case, session)} for session in case.sessions
    ]
    return {
        "session_count": len(sessions),
        "full_session_count": len(case.sessions),
        "content_bytes": sum(len(row["content"].encode("utf-8")) for row in rendered),
        "sha256": _digest(rendered),
        "full_sha256": _digest(full),
        "matches_full_history": rendered == full,
    }


def dry_run_plan(
    dataset: LoCoMoPlusDataset,
    *,
    profile: str = "smoke",
    limit: int | None = None,
    arm: str = "memory",
    max_history_sessions: int | None = None,
) -> dict[str, Any]:
    """Describe the exact workload before opening models or a database."""
    if arm not in ARMS:
        raise ValueError(f"unsupported experiment arm: {arm}")  # noqa: TRY003
    cases, history_limit = _selection(dataset, profile, limit, max_history_sessions)
    if arm == "oracle-cue" and any(case.cue_session_id is None for case in cases):
        raise ValueError("oracle-cue requires a cognitive-only selection (use smoke)")  # noqa: TRY003
    histories = {case.sample_id: _history(case, history_limit) for case in cases}
    truncated = any(len(_history(case, history_limit)) < len(case.sessions) for case in cases)
    scope = "full" if profile == "full" and limit is None and not truncated and not dataset.exclusions else "subset"
    return {
        "benchmark": "LoCoMo-Plus",
        "requested_profile": profile,
        "scope": scope,
        "arm": arm,
        "selected_count": len(cases),
        "available_count": len(dataset.cases),
        "excluded_count": len(dataset.exclusions),
        "case_ids": [case.case_id for case in cases],
        "selection_policy": "fixed-four-then-relation-round-robin-source-order-v1"
        if profile == "smoke"
        else "source-order",
        "max_history_sessions": history_limit,
        "history_truncated": truncated,
        "history_policy": "cue-anchored contiguous window; factual tail" if history_limit else "all sessions",
        "histories": {
            case.sample_id: _history_identity(case, histories[case.sample_id])
            for case in {case.sample_id: case for case in cases}.values()
        },
        "ingestion_session_count": sum(len(sessions) for sessions in histories.values())
        if arm.startswith("memory")
        else 0,
        "answer_requests": len(cases),
        "judge_requests": len(cases),
        "note": "Subset scores are not complete benchmark results. Extraction and embedding add model calls.",
    }


def _harness_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[3]
    digest = hashlib.sha256()
    paths = sorted((root / "src" / "powercontext").rglob("*.py"))
    # Unrelated memory suites must not invalidate a frozen LoCoMo-Plus run.
    memory_root = root / "evaluation" / "memory"
    paths += sorted(memory_root.glob("*.py"))
    for suite in ("locomo", "locomo_plus"):
        paths += sorted((memory_root / suite).rglob("*.py"))
    paths += sorted((root / "evaluation" / "performance").rglob("*.py"))
    paths += [root / "uv.lock"]
    for path in paths:
        digest.update(str(path.relative_to(root)).encode())
        digest.update(path.read_bytes())
    revision = subprocess.run(  # noqa: S603 - fixed read-only Git arguments
        [shutil.which("git") or "/usr/bin/git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return {
        "version": HARNESS_VERSION,
        "git_revision": revision,
        "source_sha256": digest.hexdigest(),
        "dependencies": {name: importlib.metadata.version(name) for name in ("powercontext", "pydantic-ai-slim")},
    }


def _prices(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    if not value.get("version") or not isinstance(value.get("models"), dict):
        raise ValueError("price file requires version and models")  # noqa: TRY003
    for rates in value["models"].values():
        for name in ("input_per_million_usd", "output_per_million_usd"):
            rate = rates.get(name)
            if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or rate < 0:
                raise ValueError("prices must be finite non-negative numbers")  # noqa: TRY003
    return value


def _cost(usage: dict[str, Any], model: str | None, prices: dict[str, Any] | None) -> dict[str, Any]:
    rates = None if prices is None else prices["models"].get(model)
    known = usage.get("input_tokens") is not None and usage.get("output_tokens") is not None
    cost = None
    if usage.get("requests") == 0:
        cost = 0.0
    elif rates is not None and known:
        cost = (
            usage["input_tokens"] * rates["input_per_million_usd"]
            + usage["output_tokens"] * rates["output_per_million_usd"]
        ) / 1_000_000
    return {**usage, "model": model, "cost_usd": cost, "pricing_version": None if prices is None else prices["version"]}


def _database_config(settings: ServerSettings, output_directory: Path) -> DatabaseConfig:
    if "database" not in settings.model_fields_set:
        return SQLiteConfig(url=f"sqlite+aiosqlite:///{output_directory / 'state.sqlite3'}")
    return settings.database


def _oceanbase_target(url: URL) -> dict[str, Any]:
    if any(not isinstance(value, str) for value in url.query.values()):
        raise ValueError("OceanBase URL query parameters must not be repeated")  # noqa: TRY003
    if {key.casefold() for key in url.query} & {"read_default_file", "read_default_group", "sql_mode"}:
        raise ValueError("OceanBase external defaults and SQL mode overrides are not supported for resumable runs")  # noqa: TRY003
    try:
        # URL query values override the translated authority/path in the installed dialect.
        _, options = AsyncOceanBaseDialect().create_connect_args(url)
        username = options.get("user")
        database = options.get("db")
        host = options.get("host")
        port = int(options.get("port", 3306))
        if (
            not isinstance(host, str)
            or not host
            or not all(isinstance(value, str) and value for value in (username, database))
            or not 1 <= port <= 65535
        ):
            raise ValueError  # noqa: TRY301
    except (ArgumentError, TypeError, ValueError):
        raise ValueError(  # noqa: TRY003
            "OceanBase connection target must explicitly identify its host, port, user and database"
        ) from None
    socket = options.get("unix_socket")
    # Passwords, TLS/authentication material and tuning options do not identify the database.
    # init_command is replaced by the runtime's fixed SET autocommit = 0 command.
    return {
        "driver": url.drivername,
        "username": username,
        "database": database,
        "host": None if socket else host.lower(),
        "port": None if socket else port,
        "unix_socket": str(Path(socket).resolve()) if socket else None,
    }


def _database_identity(database: DatabaseConfig) -> dict[str, str]:
    if database.kind == "oceanbase":
        target = _oceanbase_target(make_url(database.url.get_secret_value()))
        return {
            "database_kind": database.kind,
            "database_fingerprint_version": "oceanbase-target-v2",
            "database_fingerprint": _digest(json.dumps(target, sort_keys=True)),
        }
    target: dict[str, Any]
    if database.kind == "seekdb":
        target = {"path": str(database.path.resolve()), "database": database.database}
    else:
        url = make_url(database.url)
        name = url.database
        if name and name != ":memory:" and not name.startswith("file:"):
            name = str(Path(name).resolve())
        url = url.set(database=name)
        target = {
            "driver": url.drivername,
            "host": (url.host or "").lower(),
            "port": url.port or None,
            "username": url.username,
            "database": url.database,
            "query": dict(url.query),
        }
        if (url.database or "").startswith("file:"):
            target["working_directory"] = str(Path.cwd().resolve())
    return {
        "database_kind": database.kind,
        "database_fingerprint": _digest(json.dumps(target, sort_keys=True)),
    }


def _configuration(settings, judge_model, max_tokens, database) -> dict[str, Any]:
    inference = settings.inference
    return {
        **public_configuration(settings),
        **_database_identity(database),
        "persistence": (
            "configured database; isolated run scopes"
            if "database" in settings.model_fields_set
            else "isolated output-directory/state.sqlite3"
        ),
        "judge_model": judge_model,
        "judge_shares_generator_model": judge_model == inference.generation_model,
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "generation_timeout_seconds": inference.generation_timeout_seconds,
        "generation_max_requests": inference.generation_max_requests,
        "generation_model_settings": inference.generation_model_settings,
        "embedding_model_settings": inference.embedding_model_settings,
        "embedding_timeout_seconds": inference.embedding_timeout_seconds,
        "endpoint_fingerprints": {
            "generation": _digest(str(inference.generation_base_url)),
            "embedding": _digest(str(inference.embedding_base_url)),
        },
        "memory_extraction_profile": "conversation",
        "memory_extraction_instructions_sha256": hashlib.sha256(
            atomic_memory_extraction_instructions(MemoryExtractionProfile.CONVERSATION).encode("utf-8")
        ).hexdigest(),
        "memory_reconciliation_instructions_sha256": hashlib.sha256(
            ATOMIC_MEMORY_RECONCILIATION_INSTRUCTIONS.encode("utf-8")
        ).hexdigest(),
    }


def _scope(run_id: str, sample_id: str) -> str:
    return f"benchmark:locomo-plus:{run_id}:{sample_id}"


async def _flush_session(memory_app, session, position, scope, output_directory, record):
    with capture_run_messages() as messages:
        try:
            return await memory_app.flush(limit=1)
        except Exception as error:
            failures = record.setdefault("failures", [])
            failure = {
                "session_position": position,
                "source_id": session.session_id,
                "error": describe_error(error),
            }
            if messages:
                filename = f"{_digest(scope)[:16]}-session-{position}-failure-{len(failures) + 1}.json"
                path = output_directory / "diagnostics" / filename
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(ModelMessagesTypeAdapter.dump_json(messages, indent=2))
                failure["messages_file"] = str(path.relative_to(output_directory))
            failures.append(failure)
            raise


async def _ingest(runtime, case, sessions, scope, output_directory, records, prices, settings):
    source_app = runtime.sources.for_scope(scope)
    memory_app = runtime.memory.for_scope(scope)
    started = perf_counter()
    record = records.setdefault(scope, {"scope_id": scope, "latency_ms": 0.0})
    flush_inflight = False
    try:
        for session in sessions:
            await source_app.capture(
                CaptureSource(source_id=session.session_id, content=render_case_session(case, session), metadata={})
            )
        cursor = await memory_app.cursor()
        record.update({
            "status": "running",
            "planned_session_count": len(sessions),
            "processed_session_count": cursor.sequence,
        })
        _write_json(output_directory / "ingestion.json", records)
        if cursor.sequence > len(sessions):
            raise ValueError("isolated benchmark scope contains unexpected Sources")  # noqa: TRY003, TRY301
        while cursor.sequence < len(sessions):
            flush_inflight = True
            result = await _flush_session(
                memory_app, sessions[cursor.sequence], cursor.sequence + 1, scope, output_directory, record
            )
            flush_inflight = False
            if not result.processed or result.current_cursor != cursor.sequence + 1:
                raise RuntimeError("memory extraction did not advance one Source")  # noqa: TRY003, TRY301
            cursor = await memory_app.cursor()
            record["processed_session_count"] = cursor.sequence
            _write_json(output_directory / "ingestion.json", records)
        memories = await _all_atomic_records(memory_app, include_inactive=True)
        record.update({
            "status": "ok",
            "session_count": len(sessions),
            "schema": "powercontext.benchmark.locomo-plus.ingestion.v2",
            "atomic_memory_count": sum(memory.state.state is AtomicMemoryStateValue.ACTIVE for memory in memories),
            "atomic_memory_snapshot": [read.model_dump(mode="json") for read in _atomic_snapshot(memories)],
            "memories": [memory.model_dump(mode="json", by_alias=True) for memory in memories],
        })
        record.pop("error_type", None)
        record.pop("error", None)
        return memories  # noqa: TRY300
    except Exception as error:
        record.update({"status": "error", "error_type": type(error).__name__, "error": describe_error(error)})
        if flush_inflight:
            # Runtime statistics contain successful calls only; failed provider calls may still be billed.
            record["usage_incomplete"] = True
        raise
    finally:
        record["latency_ms"] += (perf_counter() - started) * 1_000
        try:
            usage = (await runtime.statistics.for_scope(scope).overview()).usage
            ingestion = [
                row for row in usage.by_purpose if row.purpose.value in {"memory_extraction", "memory_indexing"}
            ]
            record["usage"] = {
                "generation": _cost(
                    _sum_usage([row.generation.model_dump() for row in ingestion]),
                    settings.inference.generation_model,
                    prices,
                ),
                "embedding": _cost(
                    _sum_usage([{**row.embedding.model_dump(), "output_tokens": 0} for row in ingestion]),
                    settings.inference.embedding_model,
                    prices,
                ),
            }
            if record.get("usage_incomplete"):
                record["usage"]["unreported_failed_calls"] = {
                    "requests": None,
                    "input_tokens": None,
                    "output_tokens": None,
                    "cost_usd": None,
                }
        finally:
            _write_json(output_directory / "ingestion.json", records)


def _sum_usage(values: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    requests = [row.get("requests") for row in values]
    result: dict[str, Any] = {"requests": None if None in requests else sum(requests)}
    for key in ("input_tokens", "output_tokens", "cost_usd"):
        relevant = [row for row in values if row.get("requests") != 0]
        result[key] = (
            None if any(row.get(key) is None for row in relevant) else sum(row.get(key, 0) or 0 for row in relevant)
        )
    return result


def _start_usage(observation, stage, model, prices):
    attempts = observation.setdefault("usage_attempts", {}).setdefault(stage, [])
    attempts.append(_cost({"requests": 1, "input_tokens": None, "output_tokens": None}, model, prices))
    observation.setdefault("usage", {})[stage] = _sum_usage(attempts)


def _finish_usage(observation, stage, usage, model, prices):
    attempts = observation["usage_attempts"][stage]
    attempts[-1] = _cost(usage, model, prices)
    observation["usage"][stage] = {
        **_sum_usage(attempts),
        "pricing_version": None if prices is None else prices["version"],
    }


async def _recall_usage(runtime, scope):
    statistics = await runtime.statistics.for_scope(scope).overview()
    rows = [row for row in statistics.usage.by_purpose if row.purpose.value == "memory_recall"]
    return {
        "embedding": _sum_usage([{**row.embedding.model_dump(), "output_tokens": 0} for row in rows]),
        "generation": _sum_usage([row.generation.model_dump() for row in rows]),
    }


async def _retrieve(runtime, case, scope, sessions, top_k, source_expansion):
    result = await runtime.memory.for_scope(scope).search(
        SearchMemoryRequest(query=case.question, limit=top_k, mode="hybrid")
    )
    records = runtime.records.for_scope(scope)
    cache: dict[tuple[str, str, int], tuple[str, ...]] = {}
    rendered: list[str] = []
    hits: list[dict[str, Any]] = []
    session_map = {session.session_id: session for session in sessions}
    selected_ids: list[str] = []
    for rank, wrapper in enumerate(result.hits, 1):
        hit = wrapper.hit
        ids = await _lineage_source_ids(records, hit.artifact_ref, cache)
        hits.append({
            "rank": rank,
            "artifact_ref": hit.artifact_ref.model_dump(mode="json"),
            "state_version": hit.state_version,
            "kind": hit.kind,
            "text": hit.text,
            "score": hit.score,
            "distance": hit.distance,
            "matched_by": list(wrapper.matched_by),
            "source_ids": list(ids),
        })
        rendered.append(f"Memory: {hit.text}\nSources: {', '.join(ids)}")
        for source_id in ids:
            local_id = source_id.rsplit(":", maxsplit=1)[-1]
            if local_id in session_map and local_id not in selected_ids:
                selected_ids.append(local_id)
                rendered.append(f"Source {local_id} date: {session_map[local_id].date_time}")
    if source_expansion:
        rendered.extend(render_case_session(case, session_map[source_id]) for source_id in selected_ids)
    evidence_sessions = tuple(dict.fromkeys(reference.split(":", maxsplit=1)[0] for reference in case.evidence))
    metrics = retrieval_metrics(
        evidence_sessions=evidence_sessions, hit_source_ids=tuple(tuple(hit["source_ids"]) for hit in hits)
    )
    return (
        "\n\n".join(rendered),
        hits,
        selected_ids,
        {
            **metrics,
            "mode": result.mode,
            "score_kind": "rrf-ranking-score",
            "embedding_calls": result.embedding_calls,
            "generation_calls": result.generation_calls,
            "rerank": None
            if result.rerank is None
            else {
                "policy_id": result.rerank.policy_id,
                "candidate_count": len(result.rerank.candidate_hits),
                "selected_ranks": list(result.rerank.selected_ranks),
                "discarded_rank_count": result.rerank.discarded_rank_count,
                "used_fallback": result.rerank.used_fallback,
                "latency_ms": result.rerank.latency_ms,
                "usage": result.rerank.usage.model_dump(mode="json"),
            },
        },
    )


async def _evaluate(
    case,
    *,
    runtime,
    answer_model,
    judge_model,
    judge_model_id,
    settings,
    output_directory,
    run_id,
    sessions,
    arm,
    top_k,
    max_tokens,
    prices,
    ingestion,
    scope_ids,
    scope_namespace,
    previous,
):
    started = perf_counter()
    phase = "infrastructure"
    observation = (
        dict(previous)
        if previous
        else {
            "case_id": case.case_id,
            "schema": "powercontext.benchmark.locomo-plus.observation.v2",
            "category": case.category,
            "constraint_type": case.relation_type,
            "question": case.question,
            "gold_answer": case.answer,
            "evidence": list(case.evidence),
            "metadata": case.metadata,
            "latency_ms": {},
            "usage": {},
        }
    )
    observation.setdefault("latency_ms", {})
    observation.setdefault("usage", {})
    observation.pop("error", None)
    observation.pop("failure_stage", None)
    try:
        if not observation.get("generated_answer"):
            prepared = perf_counter()
            hits, selected_ids, retrieval = [], [], {}
            if arm.startswith("memory"):
                namespace = _scope(run_id, case.sample_id)
                scope = scope_ids.get(namespace)
                if scope is None:
                    registered = await runtime.scopes.create(
                        ScopeDraft(
                            title="Benchmark conversation",
                            summary="Isolated conversation evidence for a reproducible evaluation.",
                            idempotency_key=_digest(f"{scope_namespace}:{namespace}"),
                        )
                    )
                    scope = registered.scope_id
                    scope_ids[namespace] = scope
                    ingestion[scope] = {"scope_id": scope, "namespace": namespace, "latency_ms": 0.0}
                    _write_json(output_directory / "ingestion.json", ingestion)
                observation["scope_id"] = scope
                await _ingest(runtime, case, sessions, scope, output_directory, ingestion, prices, settings)
                phase = "retrieval"
                queried = perf_counter()
                before = await _recall_usage(runtime, scope)
                _start_usage(observation, "retrieval", settings.inference.embedding_model, prices)
                context, hits, selected_ids, retrieval = await _retrieve(
                    runtime, case, scope, sessions, top_k, arm == "memory-source"
                )
                observation["latency_ms"]["query"] = (perf_counter() - queried) * 1_000
                after = await _recall_usage(runtime, scope)
                for channel, stage, model in (
                    ("embedding", "retrieval", settings.inference.embedding_model),
                    ("generation", "retrieval_generation", settings.inference.generation_model),
                ):
                    usage = {
                        key: None
                        if after[channel][key] is None or before[channel][key] is None
                        else after[channel][key] - before[channel][key]
                        for key in ("requests", "input_tokens", "output_tokens")
                    }
                    if stage == "retrieval_generation":
                        _start_usage(observation, stage, model, prices)
                    _finish_usage(observation, stage, usage, model, prices)
            elif arm == "query-only":
                context = ""
            else:
                selected = tuple(
                    session
                    for session in sessions
                    if arm == "full-context" or session.session_id == case.cue_session_id
                )
                context = "\n\n".join(render_case_session(case, session) for session in selected)
                selected_ids = [session.session_id for session in selected]
            observation["latency_ms"]["prepare"] = (perf_counter() - prepared) * 1_000
            estimator = character_token_estimator()
            observation.update({
                "context": {
                    "text": context,
                    "bytes": len(context.encode()),
                    "tokens": estimator.estimate(context),
                    "tokens_estimated": True,
                    "tokenizer": "character:weighted@1 (estimate)",
                },
                "hits": hits,
                "retrieval": retrieval,
                "citation": {
                    "available": bool(selected_ids),
                    "exact_source_content": arm in {"memory-source", "full-context", "oracle-cue"},
                    "source_ids": selected_ids,
                    "target_evidence_available": bool(
                        {reference.split(":", maxsplit=1)[0] for reference in case.evidence}.intersection(selected_ids)
                    )
                    if case.evidence
                    else None,
                },
            })
            phase = "generation"
            request = case.question if case.query_time is None else f"Date and time: {case.query_time}\n{case.question}"
            prompt = build_answer_input(question=request, context=context)
            observation["answer_instructions"] = ANSWER_INSTRUCTIONS
            observation["answer_instructions_version"] = ANSWER_INSTRUCTIONS_VERSION
            observation["answer_input"] = prompt
            generated = perf_counter()
            _start_usage(observation, "generation", settings.inference.generation_model, prices)
            observation["status"] = "pending_generation"
            _append(output_directory / "observations.jsonl", observation)
            answer, usage = await generate(
                answer_model,
                prompt=prompt,
                instructions=ANSWER_INSTRUCTIONS,
                settings=settings.inference,
                max_tokens=max_tokens,
            )
            observation["latency_ms"]["generation"] = (perf_counter() - generated) * 1_000
            observation["answer_messages"] = usage.pop("messages")
            _finish_usage(observation, "generation", usage, settings.inference.generation_model, prices)
            if not answer.strip():
                raise ValueError("answer model returned an empty response")  # noqa: TRY003, TRY301
            observation["generated_answer"] = answer
            observation["judge_input"] = build_judge_input(
                category=case.category, evidence=case.evidence_text, prediction=answer, gold=case.answer
            )
            # Persist the answer before the judge so interrupted runs never need to regenerate it.
            observation["status"] = "pending_judge"
            _append(output_directory / "observations.jsonl", observation)
        phase = "judge"
        judged = perf_counter()
        judge_input = observation["judge_input"]
        _start_usage(observation, "judge", judge_model_id, prices)
        observation["status"] = "pending_judge"
        _append(output_directory / "observations.jsonl", observation)
        raw, usage = await generate(
            judge_model,
            prompt=judge_input["input"],
            instructions=judge_input["instructions"],
            settings=settings.inference,
            max_tokens=max_tokens,
        )
        observation["latency_ms"]["judge"] = (perf_counter() - judged) * 1_000
        observation["judge_messages"] = usage.pop("messages")
        _finish_usage(observation, "judge", usage, judge_model_id, prices)
        observation["judge_raw"] = raw
        observation["judge"] = replay_judgment(judge_input, raw)
        observation["status"] = "ok"
    except Exception as error:
        observation.update({
            "status": "error",
            "failure_stage": phase,
            "error": {"stage": phase, **describe_error(error)},
        })
    observation["latency_ms"]["total"] = (perf_counter() - started) * 1_000
    return observation


def _summarize(output_directory: Path) -> dict[str, Any]:
    manifest = json.loads((output_directory / "run.json").read_text(encoding="utf-8"))
    plan = manifest["selection"]
    observed = _observations(output_directory / "observations.jsonl")
    rows = [observed[case_id] for case_id in plan["case_ids"] if case_id in observed]
    ingestion_path = output_directory / "ingestion.json"
    ingestion = json.loads(ingestion_path.read_text(encoding="utf-8")) if ingestion_path.exists() else {}
    ingestion_usage = _sum_usage([usage for row in ingestion.values() for usage in row.get("usage", {}).values()])
    summary = summarize_observations(
        rows, scope=plan["scope"], planned_count=plan["selected_count"], ingestion_usage=ingestion_usage
    )
    summary.update({
        "run_id": manifest["run_id"],
        "selection": plan,
        "configuration": manifest["configuration"],
        "ingestion": {
            "scopes": len(ingestion),
            "latency_ms": sum(row["latency_ms"] for row in ingestion.values()),
            "usage": ingestion_usage,
        },
        "excluded_dataset_cases": plan["excluded_count"],
    })
    _write_json(output_directory / "summary.json", summary)
    (output_directory / "summary.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


async def run_benchmark(  # noqa: C901
    dataset: LoCoMoPlusDataset,
    *,
    settings: ServerSettings,
    output_directory: Path,
    run_id: str,
    judge_model: str,
    profile: str = "smoke",
    limit: int | None = None,
    arm: str = "memory",
    top_k: int = 5,
    max_tokens: int = 512,
    max_history_sessions: int | None = None,
    prices: Path | None = None,
) -> dict[str, Any]:
    """Run a sequential, budget-conscious selection with immutable resume identity."""
    if not 1 <= top_k <= 50 or max_tokens < 1:
        raise ValueError("top_k must be 1..50 and max_tokens positive")  # noqa: TRY003
    if not settings.inference.generation_model or not judge_model.strip():
        raise ValueError("generation and judge models must be explicitly configured")  # noqa: TRY003
    plan = dry_run_plan(dataset, profile=profile, limit=limit, arm=arm, max_history_sessions=max_history_sessions)
    cases, history_limit = _selection(dataset, profile, limit, max_history_sessions)
    rates = _prices(prices)
    run_id = normalize_run_id(run_id)
    output_directory = output_directory.resolve()  # noqa: ASYNC240 - small local artifact operation
    output_directory.mkdir(parents=True, exist_ok=True)
    database = _database_config(settings, output_directory)
    if arm.startswith("memory") and database.kind == "sqlite":
        url = make_url(database.url)
        if database.is_in_memory or url.query.get("mode") == "memory" or url.database == "file::memory:":
            raise ValueError("Memory runs require a persistent database for resume; configure a SQLite file")  # noqa: TRY003
    manifest_path = output_directory / "run.json"
    saved_manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    scope_namespace = saved_manifest.get("scope_namespace") if saved_manifest is not None else uuid4().hex
    if not isinstance(scope_namespace, str) or not scope_namespace:
        raise ValueError("run identity changed; use a new output directory (existing runs remain replayable)")  # noqa: TRY003
    manifest = {
        "run_id": run_id,
        "scope_namespace": scope_namespace,
        "harness": _harness_identity(),
        "dataset": dataset.manifest,
        "selection": plan,
        "configuration": _configuration(settings, judge_model, max_tokens, database),
        "top_k": top_k,
        "prices": rates,
    }
    if saved_manifest is not None and saved_manifest != manifest:
        raise ValueError("run identity changed; use a new output directory")  # noqa: TRY003
    _write_json(manifest_path, manifest)
    _write_json(
        output_directory / "dataset-audit.json", {"manifest": dataset.manifest, "exclusions": dataset.exclusions}
    )
    inputs_path = output_directory / "inputs.jsonl"
    if not inputs_path.exists():
        for case in cases:
            _append(
                inputs_path,
                {
                    "case_id": case.case_id,
                    "sample_id": case.sample_id,
                    "sessions": [
                        {"source_id": session.session_id, "content": render_case_session(case, session)}
                        for session in _history(case, history_limit)
                    ],
                },
            )
    observed = _observations(output_directory / "observations.jsonl")
    pending = [case for case in cases if observed.get(case.case_id, {}).get("status") != "ok"]
    ingestion_path = output_directory / "ingestion.json"
    ingestion = json.loads(ingestion_path.read_text(encoding="utf-8")) if ingestion_path.exists() else {}
    scope_ids = {record["namespace"]: record["scope_id"] for record in ingestion.values()}
    recorded_scopes = set(scope_ids.values())
    if any(row.get("scope_id") not in recorded_scopes for row in observed.values() if row.get("scope_id")):
        raise ValueError("saved Scope mapping is missing; restore the run's ingestion.json before resuming")  # noqa: TRY003
    if not pending:
        return _summarize(output_directory)
    try:
        async with AsyncExitStack() as resources:
            runtime = None
            if arm.startswith("memory"):
                runtime_config = BuiltinConfig(
                    database=database,
                    runtime=RuntimeConfig(
                        memory_extraction_profile=MemoryExtractionProfile.CONVERSATION,
                        dream_enabled=False,
                        artifact_processing_role="all",
                    ),
                    inference=settings.inference,
                )
                runtime = await resources.enter_async_context(open_builtin_runtime(runtime_config))
                if runtime.scopes is None:
                    raise RuntimeError("Memory runs require a Scope registry")  # noqa: TRY003, TRY301
                for scope_id in scope_ids.values():
                    await runtime.scopes.get(scope_id)
            answer = await open_model(settings.inference.generation_model, settings.inference, resources)
            judge = await open_model(judge_model, settings.inference, resources)
            for index, case in enumerate(pending, 1):
                row = await _evaluate(
                    case,
                    runtime=runtime,
                    answer_model=answer,
                    judge_model=judge,
                    judge_model_id=judge_model,
                    settings=settings,
                    output_directory=output_directory,
                    run_id=run_id,
                    sessions=_history(case, history_limit),
                    arm=arm,
                    top_k=top_k,
                    max_tokens=max_tokens,
                    prices=rates,
                    ingestion=ingestion,
                    scope_ids=scope_ids,
                    scope_namespace=scope_namespace,
                    previous=observed.get(case.case_id),
                )
                _append(output_directory / "observations.jsonl", row)
                _summarize(output_directory)
                print(f"[{index}/{len(pending)}] {case.case_id}: {row['status']}")
    except Exception as error:
        observed = _observations(output_directory / "observations.jsonl")
        for case in pending:
            if observed.get(case.case_id, {}).get("status") == "ok":
                continue
            row = {
                **observed.get(case.case_id, {}),
                "case_id": case.case_id,
                "category": case.category,
                "constraint_type": case.relation_type,
                "status": "error",
                "failure_stage": "infrastructure",
                "error": {"stage": "infrastructure", **describe_error(error)},
            }
            _append(output_directory / "observations.jsonl", row)
    return _summarize(output_directory)


def replay_results(directory: Path) -> dict[str, Any]:
    """Recompute scores from frozen judge inputs and raw outputs without network calls."""
    for row in _observations(directory / "observations.jsonl").values():
        if row.get("judge_raw") is not None:
            try:
                verdict = replay_judgment(row["judge_input"], row["judge_raw"])
            except (ValueError, TypeError, KeyError):
                if row.get("status") == "ok":
                    raise
            else:
                if row.get("status") == "ok" and row.get("judge") != verdict:
                    raise ValueError("saved judge score does not match deterministic replay")  # noqa: TRY003
    return _summarize(directory)


__all__ = ["dry_run_plan", "load_settings", "replay_results", "run_benchmark"]
