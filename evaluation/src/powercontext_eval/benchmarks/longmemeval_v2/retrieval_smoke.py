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

"""Run the fixed LongMemEval-V2 subset through PowerContext without a Reader or Judge."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol, cast

from powercontext_eval.benchmarks.longmemeval_v2.adapter import (
    DEFAULT_SOURCE_CHUNK_BYTES,
    MAX_MEMORY_TEXT_BYTES,
    PowerContextHTTPRuntime,
    PowerContextMemory,
    PowerContextMemoryAdapterError,
    PowerContextMemoryModeError,
    PowerContextRuntime,
)
from powercontext_eval.benchmarks.longmemeval_v2.arms import (
    ExperimentArm,
    arm_manifest_record,
    resolve_experiment_arm,
)
from powercontext_eval.benchmarks.longmemeval_v2.catalog import (
    Ability,
    SmokeSelection,
    load_dataset_lock,
    load_smoke_manifest,
)
from powercontext_eval.benchmarks.longmemeval_v2.smoke import prepare_smoke_run
from powercontext_eval.errors import PowerContextEvalError

RETRIEVAL_MANIFEST_SCHEMA = "powercontext.longmemeval-v2-retrieval-run.v1"
RETRIEVAL_RESULT_SCHEMA = "powercontext.longmemeval-v2-retrieval-result.v1"
RETRIEVAL_FAILURE_SCHEMA = "powercontext.longmemeval-v2-retrieval-failure.v1"
RETRIEVAL_SUMMARY_SCHEMA = "powercontext.longmemeval-v2-retrieval-summary.v1"


class RetrievalSmokeError(PowerContextEvalError):
    """A retrieval-only smoke run could not preserve its evaluation contract."""


class RetrievalCapabilityError(RetrievalSmokeError):
    """The Server cannot execute the search mode required by the experiment arm."""


class RetrievalSmokeRuntime(PowerContextRuntime, Protocol):
    """Public PowerContext operations required by the retrieval runner."""

    def create_scope(self, payload: Mapping[str, object]) -> Mapping[str, object]: ...

    def get_readiness(self) -> Mapping[str, object]: ...

    def get_capabilities(self) -> Mapping[str, object]: ...


@dataclass(frozen=True)
class RetrievalQuestion:
    question_id: str
    domain: Literal["web", "enterprise"]
    ability: Ability
    text: str
    image: str | None


@dataclass(frozen=True)
class HaystackGroup:
    digest: str
    trajectory_ids: tuple[str, ...]
    question_ids: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalSmokeRun:
    output_dir: Path
    manifest_path: Path
    results_path: Path
    failures_path: Path
    summary_path: Path
    audit_path: Path


def run_retrieval_smoke(
    *,
    data_root: Path,
    dataset_lock: Path,
    harness_root: Path,
    smoke_manifest: Path,
    output_dir: Path,
    run_id: str,
    powercontext_revision: str,
    integration_revision: str,
    base_url: str = "http://127.0.0.1:8000",
    token_env: str = "POWERCONTEXT_TOKEN",
    experiment_arm: str | ExperimentArm | None = None,
    search_limit: int = 10,
    timeout_seconds: float = 30.0,
    runtime: RetrievalSmokeRuntime | None = None,
) -> RetrievalSmokeRun:
    """Ingest the fixed subset by isolated haystack and record retrieval-only outputs."""

    if output_dir.exists():
        raise RetrievalSmokeError(f"Refusing to overwrite retrieval smoke artifacts: {output_dir}")
    normalized_run_id = _nonblank(run_id, "run_id")
    powercontext_ref = _nonblank(powercontext_revision, "powercontext_revision")
    integration_ref = _nonblank(integration_revision, "integration_revision")
    arm = resolve_experiment_arm(experiment_arm)
    if isinstance(search_limit, bool) or not 1 <= search_limit <= 50:
        raise RetrievalSmokeError("search_limit must be from 1 through 50")
    if timeout_seconds <= 0:
        raise RetrievalSmokeError("timeout_seconds must be positive")

    client: RetrievalSmokeRuntime = runtime or PowerContextHTTPRuntime(
        base_url,
        token=os.getenv(token_env),
        timeout_seconds=timeout_seconds,
    )
    _require_runtime(client, arm)
    started_at = datetime.now(UTC)
    started_ns = time.perf_counter_ns()
    # Per-execution scope namespace: a run_id alone collides across repeated runs,
    # experiment arms, and same-basename output directories, silently sharing Scopes.
    execution_namespace = uuid.uuid4().hex
    prepared = prepare_smoke_run(
        data_root=data_root,
        dataset_lock=dataset_lock,
        harness_root=harness_root,
        smoke_manifest=smoke_manifest,
        output_dir=output_dir,
    )
    lock = load_dataset_lock(dataset_lock)
    selection = load_smoke_manifest(smoke_manifest)
    questions = _load_questions(
        data_root / "questions.jsonl",
        selection,
        expected_digest=lock.file_digests["questions.jsonl"],
    )
    groups, question_groups = _load_groups(
        data_root / "haystacks" / f"lme_v2_{selection.tier}.json",
        selection,
        expected_digest=lock.file_digests[f"haystacks/lme_v2_{selection.tier}.json"],
    )

    results_path = output_dir / "retrieval-results.jsonl"
    failures_path = output_dir / "failures.jsonl"
    retrieval_manifest_path = output_dir / "retrieval-manifest.json"
    summary_path = output_dir / "summary.json"
    audit_path = output_dir / "adapter-audit.jsonl"
    _create_empty(results_path)
    _create_empty(failures_path)

    root_scope = _create_scope(
        client,
        title=f"LongMemEval-V2 retrieval smoke {normalized_run_id} ({arm.arm_id})",
        summary="Retrieval-only fixed smoke subset; no Reader, Judge, or scoring.",
        idempotency_seed=f"{normalized_run_id}:{arm.arm_id}:{execution_namespace}:root",
        parent_scope_id=None,
    )
    group_scopes: dict[str, str] = {}
    adapters: dict[str, PowerContextMemory] = {}
    for sequence, group in enumerate(groups, start=1):
        scope_id = _create_scope(
            client,
            title=f"LongMemEval-V2 haystack {sequence}: {group.digest[:12]}",
            summary=f"Isolated haystack with {len(group.trajectory_ids)} trajectories.",
            idempotency_seed=f"{normalized_run_id}:{arm.arm_id}:{execution_namespace}:haystack:{group.digest}",
            parent_scope_id=root_scope,
        )
        group_scopes[group.digest] = scope_id
        adapter = PowerContextMemory(
            {
                "scope_id": scope_id,
                "audit_path": str(audit_path),
                "base_url": base_url,
                "token_env": token_env,
                "search_mode": arm.search_mode,
                "query_strategy": arm.retrieval_strategy,
                "prepared_context_max_bytes": arm.prepared_context_max_bytes or 8_000,
                "memory_projection": arm.memory_projection,
                "task_lens": arm.task_lens,
                "search_limit": search_limit,
                "timeout_seconds": timeout_seconds,
            }
        )
        adapter.configure_runtime(runtime=client)
        adapters[group.digest] = adapter

    _write_json_exclusive(
        retrieval_manifest_path,
        {
            "schema": RETRIEVAL_MANIFEST_SCHEMA,
            "classification": "smoke-subset-retrieval-only",
            "run_id": normalized_run_id,
            "execution_namespace": execution_namespace,
            "started_at": started_at.isoformat(),
            "experiment_arm": arm_manifest_record(arm),
            "revisions": {
                "powercontext": powercontext_ref,
                "integration": integration_ref,
            },
            "runtime": {
                "base_url": base_url,
                "token_env": token_env,
                "search_mode": arm.search_mode,
                "query_strategy": arm.retrieval_strategy,
                "prepared_context_max_bytes": arm.prepared_context_max_bytes,
                "search_limit": search_limit,
                "timeout_seconds": timeout_seconds,
                "reader": None,
                "judge": None,
                "source_projection": "full-allowed-trajectory-fields",
                "source_chunk_bytes": DEFAULT_SOURCE_CHUNK_BYTES,
                "memory_projection": arm.memory_projection,
                "query_projection": arm.query_projection,
                "memory_max_bytes": MAX_MEMORY_TEXT_BYTES,
            },
            "input_artifacts": {
                "manifest": prepared.manifest_path.name,
                "subset": prepared.subset_path.name,
            },
            "root_scope_id": root_scope,
            "haystacks": [
                {
                    "digest": group.digest,
                    "scope_id": group_scopes[group.digest],
                    "trajectory_count": len(group.trajectory_ids),
                    "question_ids": list(group.question_ids),
                }
                for group in groups
            ],
        },
    )

    targets: dict[str, list[str]] = {}
    for group in groups:
        for trajectory_id in group.trajectory_ids:
            targets.setdefault(trajectory_id, []).append(group.digest)
    found: set[str] = set()
    inserted: dict[str, int] = {group.digest: 0 for group in groups}
    group_failures: dict[str, dict[str, object]] = {}
    for trajectory in _jsonl(
        data_root / "trajectories.jsonl",
        "trajectories.jsonl",
        expected_digest=lock.file_digests["trajectories.jsonl"],
    ):
        trajectory_id = trajectory.get("id")
        if not isinstance(trajectory_id, str) or trajectory_id not in targets:
            continue
        if trajectory_id in found:
            raise RetrievalSmokeError(f"Selected trajectory appears more than once: {trajectory_id}")
        found.add(trajectory_id)
        for digest in targets[trajectory_id]:
            if digest in group_failures:
                continue
            try:
                adapters[digest].insert(trajectory)
                inserted[digest] += 1
            except Exception as error:  # noqa: BLE001 - one haystack failure must not stop the other haystack
                failure = _failure(
                    failure_id=f"ingest-{digest[:16]}",
                    phase="ingest",
                    error=error,
                    haystack_digest=digest,
                    question_id=None,
                )
                group_failures[digest] = failure
                _append_json(failures_path, failure)
    missing = sorted(set(targets) - found)
    if missing:
        raise RetrievalSmokeError(f"Selected trajectories were not found: {missing[:5]}")

    succeeded = 0
    failed = 0
    context_bytes_total = 0
    citation_count = 0
    for sequence, question in enumerate(questions, start=1):
        group = question_groups[question.question_id]
        scope_id = group_scopes[group.digest]
        if group.digest in group_failures:
            failed += 1
            _append_json(
                results_path,
                _result(
                    sequence=sequence,
                    question=question,
                    group=group,
                    scope_id=scope_id,
                    status="failed",
                    memory_context=[],
                    metadata=None,
                    failure_id=str(group_failures[group.digest]["failure_id"]),
                ),
            )
            continue
        adapter = adapters[group.digest]
        invocation_id = f"{normalized_run_id}:{question.question_id}"
        adapter.set_query_context(query_invocation_id=invocation_id)
        try:
            memory_context = adapter.query(question.text, query_image=question.image)
            metadata = adapter.post_query_hook(
                query=question.text,
                query_image=question.image,
                memory_context=memory_context,
            )
            _validate_context(memory_context)
            result = _result(
                sequence=sequence,
                question=question,
                group=group,
                scope_id=scope_id,
                status="succeeded",
                memory_context=memory_context,
                metadata=metadata,
                failure_id=None,
            )
            succeeded += 1
            context_bytes_total += sum(len(item["value"].encode()) for item in memory_context)
            citations = result["citations"]
            citation_count += len(citations) if isinstance(citations, list) else 0
        except Exception as error:  # noqa: BLE001 - each query needs an independent classified result
            failed += 1
            failure_id = f"query-{question.question_id}"
            try:
                # The adapter keeps the failed query's provenance (requested and actual
                # search mode) even when the query itself failed; keep it in the result.
                metadata = adapter.post_query_hook(
                    query=question.text,
                    query_image=question.image,
                    memory_context=[],
                )
            except Exception:  # noqa: BLE001 - failure provenance must never mask the original failure
                metadata = None
            _append_json(
                failures_path,
                _failure(
                    failure_id=failure_id,
                    phase="query",
                    error=error,
                    haystack_digest=group.digest,
                    question_id=question.question_id,
                ),
            )
            result = _result(
                sequence=sequence,
                question=question,
                group=group,
                scope_id=scope_id,
                status="failed",
                memory_context=[],
                metadata=metadata,
                failure_id=failure_id,
            )
        finally:
            adapter.clear_query_context()
        _append_json(results_path, result)

    summary = {
        "schema": RETRIEVAL_SUMMARY_SCHEMA,
        "classification": "smoke-subset-retrieval-only",
        "run_id": normalized_run_id,
        "experiment_arm": arm_manifest_record(arm),
        "completed_at": datetime.now(UTC).isoformat(),
        "question_count": len(questions),
        "succeeded": succeeded,
        "failed": failed,
        "haystack_count": len(groups),
        "trajectory_count": len(found),
        "inserted_trajectory_copies": sum(inserted.values()),
        "context_bytes": context_bytes_total,
        "citation_count": citation_count,
        "elapsed_ms": round((time.perf_counter_ns() - started_ns) / 1_000_000, 3),
        "accuracy": None,
        "reader": None,
        "judge": None,
    }
    _write_json_exclusive(summary_path, summary)
    return RetrievalSmokeRun(
        output_dir=output_dir,
        manifest_path=retrieval_manifest_path,
        results_path=results_path,
        failures_path=failures_path,
        summary_path=summary_path,
        audit_path=audit_path,
    )


def _require_runtime(runtime: RetrievalSmokeRuntime, arm: ExperimentArm) -> None:
    """Fail as a capability error before any ingestion when the arm cannot be executed."""

    readiness = runtime.get_readiness()
    if readiness.get("status") != "ready":
        raise RetrievalSmokeError("PowerContext Server is not ready")
    capabilities = runtime.get_capabilities()
    modes = capabilities.get("search_modes")
    if arm.retrieval_strategy == "memory-search":
        if arm.search_mode is None or not isinstance(modes, list) or arm.search_mode not in modes:
            raise RetrievalCapabilityError(
                f"PowerContext Server does not support {arm.search_mode} Memory search "
                f"required by experiment arm {arm.arm_id}"
            )
    elif arm.retrieval_strategy == "prepared-context":
        versions = capabilities.get("context_versions")
        if not isinstance(versions, list) or "powercontext.prepared-context.v1" not in versions:
            raise RetrievalCapabilityError(
                f"PowerContext Server does not support PreparedContext required by experiment arm {arm.arm_id}"
            )
    else:
        raise RetrievalCapabilityError(f"unsupported retrieval strategy for experiment arm {arm.arm_id}")
    source_types = capabilities.get("source_types")
    if not isinstance(source_types, list) or "content" not in source_types:
        raise RetrievalCapabilityError("PowerContext Server does not support Content Sources")
    artifact_families = capabilities.get("artifact_families")
    if not isinstance(artifact_families, list) or "memory" not in artifact_families:
        raise RetrievalCapabilityError("PowerContext Server does not support Memory artifacts")


def _create_scope(
    runtime: RetrievalSmokeRuntime,
    *,
    title: str,
    summary: str,
    idempotency_seed: str,
    parent_scope_id: str | None,
) -> str:
    response = runtime.create_scope(
        {
            "title": title,
            "summary": summary,
            "parent_scope_id": parent_scope_id,
            "idempotency_key": hashlib.sha256(idempotency_seed.encode()).hexdigest(),
        }
    )
    return _nonblank(response.get("scope_id"), "created scope_id")


def _load_questions(
    path: Path,
    selection: SmokeSelection,
    *,
    expected_digest: str,
) -> tuple[RetrievalQuestion, ...]:
    cases = {case.question_id: case for case in selection.cases}
    found: dict[str, RetrievalQuestion] = {}
    for row in _jsonl(path, "questions.jsonl", expected_digest=expected_digest):
        question_id = row.get("id")
        if not isinstance(question_id, str) or question_id not in cases:
            continue
        if question_id in found:
            raise RetrievalSmokeError(f"Selected question appears more than once: {question_id}")
        domain = row.get("domain")
        if domain not in {"web", "enterprise"}:
            raise RetrievalSmokeError(f"Selected question has invalid domain: {question_id}")
        text, image = _question_components(
            row.get("question"),
            row.get("image"),
            data_root=path.parent,
            question_id=question_id,
        )
        found[question_id] = RetrievalQuestion(
            question_id=question_id,
            domain=cast("Literal['web', 'enterprise']", domain),
            ability=cases[question_id].ability,
            text=text,
            image=image,
        )
    missing = [case.question_id for case in selection.cases if case.question_id not in found]
    if missing:
        raise RetrievalSmokeError(f"Selected questions were not found: {missing}")
    return tuple(found[case.question_id] for case in selection.cases)


def _load_groups(
    path: Path,
    selection: SmokeSelection,
    *,
    expected_digest: str,
) -> tuple[tuple[HaystackGroup, ...], dict[str, HaystackGroup]]:
    try:
        content = path.read_bytes()
        if hashlib.sha256(content).hexdigest() != expected_digest:
            raise RetrievalSmokeError("LongMemEval-V2 SHA-256 mismatch for haystack")
        value = json.loads(content)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RetrievalSmokeError(f"Cannot read LongMemEval-V2 haystack: {path}") from error
    if not isinstance(value, dict):
        raise RetrievalSmokeError("LongMemEval-V2 haystack must be an object")
    ordered: list[str] = []
    grouped_ids: dict[str, tuple[str, ...]] = {}
    grouped_questions: dict[str, list[str]] = {}
    question_digest: dict[str, str] = {}
    for case in selection.cases:
        raw_ids = value.get(case.question_id)
        if not isinstance(raw_ids, list) or not raw_ids or not all(isinstance(item, str) and item for item in raw_ids):
            raise RetrievalSmokeError(f"Invalid haystack for selected question: {case.question_id}")
        trajectory_ids = tuple(raw_ids)
        digest = hashlib.sha256(
            json.dumps(trajectory_ids, ensure_ascii=True, separators=(",", ":")).encode()
        ).hexdigest()
        if digest not in grouped_ids:
            ordered.append(digest)
            grouped_ids[digest] = trajectory_ids
            grouped_questions[digest] = []
        elif grouped_ids[digest] != trajectory_ids:
            raise RetrievalSmokeError("Haystack digest collision")
        grouped_questions[digest].append(case.question_id)
        question_digest[case.question_id] = digest
    groups = tuple(
        HaystackGroup(
            digest=digest,
            trajectory_ids=grouped_ids[digest],
            question_ids=tuple(grouped_questions[digest]),
        )
        for digest in ordered
    )
    by_digest = {group.digest: group for group in groups}
    return groups, {question_id: by_digest[digest] for question_id, digest in question_digest.items()}


def _question_components(
    value: object,
    raw_image: object,
    *,
    data_root: Path,
    question_id: str,
) -> tuple[str, str | None]:
    text: object = None
    if isinstance(value, str) and value.strip():
        text = value
    if isinstance(value, Mapping):
        text = value.get("text")
        raw_image = value.get("image", raw_image)
    if not isinstance(text, str) or not text.strip():
        raise RetrievalSmokeError(f"Selected question has invalid text content: {question_id}")
    if raw_image is None:
        return text, None
    if not isinstance(raw_image, str) or not raw_image.strip():
        raise RetrievalSmokeError(f"Selected question has invalid image content: {question_id}")
    image_path = Path(raw_image)
    if not image_path.is_absolute():
        image_path = data_root / image_path
    if not image_path.is_file():
        raise RetrievalSmokeError(f"Selected question image is missing: {question_id}")
    return text, str(image_path.resolve())


def _jsonl(path: Path, label: str, *, expected_digest: str) -> Iterator[dict[str, object]]:
    hasher = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for line_number, raw_line in enumerate(stream, start=1):
                hasher.update(raw_line)
                if not raw_line.strip():
                    continue
                try:
                    value = json.loads(raw_line)
                except (UnicodeDecodeError, json.JSONDecodeError) as error:
                    raise RetrievalSmokeError(f"{label} has invalid JSON at line {line_number}") from error
                if not isinstance(value, dict):
                    raise RetrievalSmokeError(f"{label} line {line_number} must be an object")
                yield value
    except OSError as error:
        raise RetrievalSmokeError(f"Cannot stream {label}: {path}") from error
    if hasher.hexdigest() != expected_digest:
        raise RetrievalSmokeError(f"LongMemEval-V2 SHA-256 mismatch while streaming {label}")


def _result(
    *,
    sequence: int,
    question: RetrievalQuestion,
    group: HaystackGroup,
    scope_id: str,
    status: Literal["succeeded", "failed"],
    memory_context: list[dict[str, str]],
    metadata: dict[str, object] | None,
    failure_id: str | None,
) -> dict[str, object]:
    context_bytes = sum(len(item["value"].encode()) for item in memory_context)
    citations = [] if metadata is None else metadata.get("citations", [])
    timings = None if metadata is None else metadata.get("timings_ms")
    return {
        "schema": RETRIEVAL_RESULT_SCHEMA,
        "sequence": sequence,
        "question_id": question.question_id,
        "domain": question.domain,
        "ability": question.ability,
        "question": {"text": question.text, "image": question.image},
        "haystack_digest": group.digest,
        "scope_id": scope_id,
        "status": status,
        "search_mode": {
            "requested": None if metadata is None else metadata.get("requested_mode"),
            "actual": None if metadata is None else metadata.get("actual_mode"),
        },
        "memory_context": memory_context,
        "context_bytes": context_bytes,
        "citations": citations,
        "timings_ms": timings,
        "failure_id": failure_id,
    }


def _failure(
    *,
    failure_id: str,
    phase: Literal["ingest", "query"],
    error: Exception,
    haystack_digest: str,
    question_id: str | None,
) -> dict[str, object]:
    summary = str(error).strip() or type(error).__name__
    return {
        "schema": RETRIEVAL_FAILURE_SCHEMA,
        "failure_id": failure_id,
        "phase": phase,
        "category": _failure_category(error),
        "error_type": type(error).__name__,
        "summary": summary[:500],
        "haystack_digest": haystack_digest,
        "question_id": question_id,
    }


def _failure_category(error: Exception) -> Literal["infrastructure", "integration", "integrity"]:
    if isinstance(error, PowerContextMemoryModeError):
        return "integrity"
    if isinstance(error, PowerContextMemoryAdapterError):
        return "infrastructure"
    return "integration"


def _validate_context(items: list[dict[str, str]]) -> None:
    if not isinstance(items, list):
        raise RetrievalSmokeError("Memory query did not return a list")
    for index, item in enumerate(items):
        if set(item) != {"type", "value"} or item["type"] not in {"text", "image"} or not item["value"]:
            raise RetrievalSmokeError(f"Memory context item {index} is invalid")


def _create_empty(path: Path) -> None:
    with path.open("x", encoding="utf-8"):
        pass


def _append_json(path: Path, value: object) -> None:
    with path.open("a", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n")


def _write_json_exclusive(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8", newline="") as stream:
        stream.write(json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True, allow_nan=False) + "\n")


def _nonblank(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RetrievalSmokeError(f"{label} must be a non-empty string")
    return value.strip()
