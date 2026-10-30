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

"""Real Experience generation through the configured runtime and database."""

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from contextlib import AsyncExitStack, contextmanager
from contextvars import ContextVar
from dataclasses import asdict
from pathlib import Path
from typing import Any

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.inference.errors import find_privacy_error
from powercontext.builtin.inference.usage import collect_input_encodings
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    ApproveCandidateRequest,
    BuiltinConfig,
    CaptureSource,
    GenerateExperienceRequest,
    GetExperienceRequest,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.application import ScopedStatisticsApplication
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.configuration import server_settings_context

from .dataset import (
    MODES,
    PROJECTION,
    PUBLIC_V2_SHA256,
    SCHEMA,
    digest,
    load_catalog,
    sources,
    stability_ids,
    write_json,
)
from .identity import prepare_configuration_identity

_USAGE: ContextVar[list[dict[str, Any]] | None] = ContextVar("input_encoding_eval_usage", default=None)


@contextmanager
def observe_usage():
    """Observe the existing public reporter while preserving normal persistence."""
    original = ScopedStatisticsApplication.record_model_usage

    async def record(self, purpose, operation, usage):
        captured = _USAGE.get()
        if captured is not None:
            captured.append({"purpose": str(purpose), "operation": str(operation), **usage.model_dump(mode="json")})
        return await original(self, purpose, operation, usage)

    ScopedStatisticsApplication.record_model_usage = record
    try:
        yield
    finally:
        ScopedStatisticsApplication.record_model_usage = original


def source_hash() -> str:
    root = Path(__file__).resolve().parents[3]
    files = sorted((root / "src/powercontext").rglob("*.py"))
    files += [Path(__file__).parent / name for name in ("dataset.py", "runner.py", "identity.py")]
    return digest({str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files})


def plan_jobs(case_ids: tuple[str, ...], repeats: int, seed: int) -> list[dict[str, Any]]:
    """Interleave treatments by case with a reproducible randomized order."""
    rng = random.Random(seed)  # noqa: S311 - experimental ordering, not security
    jobs = []
    for repeat in range(repeats):
        order = list(case_ids)
        rng.shuffle(order)
        for case_id in order:
            modes = list(MODES)
            rng.shuffle(modes)
            for mode in modes:
                jobs.append({"instance_id": case_id, "repeat": repeat, "mode": mode})
    return jobs


def usage_totals(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Unknown counts stay unknown; retries remain in the billed request total."""
    result: dict[str, Any] = {
        "requests": sum(event["requests"] for event in events) if events else None,
        "events": len(events),
    }
    for field in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens", "reasoning_tokens"):
        values = [event.get(field) for event in events]
        result[field] = sum(values) if values and all(value is not None for value in values) else None
        result[field + "_known_subtotal"] = sum(value for value in values if value is not None)
    result["total_tokens"] = (
        result["input_tokens"] + result["output_tokens"]
        if result["input_tokens"] is not None and result["output_tokens"] is not None
        else None
    )
    result["total_tokens_known_subtotal"] = (
        result["input_tokens_known_subtotal"] + result["output_tokens_known_subtotal"]
    )
    result["incomplete"] = not events or any(event.get("incomplete", False) for event in events)
    result["cost"] = None
    result["cost_unknown_reason"] = "no_explicit_provider_price_policy"
    return result


def content_diagnostics(content: dict[str, Any], instance) -> dict[str, Any]:
    """Diagnostics, not a substitute for blinded semantic grading."""
    text = "\n".join(str(content.get(key, "")) for key in ("situation", "action", "outcome", "lesson"))
    paths = sorted(set(re.findall(r"^\+\+\+ b/(.+)$", instance.patch, flags=re.MULTILINE)))
    mentioned = [path for path in paths if path in text or path.rsplit("/", 1)[-1] in text]
    return {
        "nonempty_fields": all(
            str(content.get(key, "")).strip() for key in ("situation", "action", "outcome", "lesson")
        ),
        "reference_patch_paths": len(paths),
        "mentioned_patch_paths": len(mentioned),
        "path_coverage_is_diagnostic_only": True,
        "semantic_quality": None,
        "semantic_quality_reason": "requires_blinded_grounding_and_transfer_grading",
    }


def _configuration(
    settings,
    mode: str,
    output: Path,
    database_config: Path | None,
    protection_config: Path | None = None,
    max_output_tokens: int | None = None,
) -> BuiltinConfig:
    values = settings.inference.model_dump()
    values["input_encoding"] = {"experience.generate": mode}
    if max_output_tokens is not None:
        values["generation_model_settings"] = {**values["generation_model_settings"], "max_tokens": max_output_tokens}
    if protection_config is not None:
        values["protection"] = json.loads(protection_config.read_text())
    runtime_values = settings.runtime.model_dump()
    database = settings.database
    if database_config is not None:
        database = json.loads(database_config.read_text())
    elif database.kind == "sqlite":
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{output.resolve() / 'experience.db'}")
    database_kind = database["kind"] if isinstance(database, dict) else database.kind
    if database_kind == "oceanbase":
        # API role constructs no worker/supervisor, preserving the shared DB's manifest.
        runtime_values["artifact_processing_role"] = "api"
    else:
        runtime_values["artifact_processing_role"] = "all"
        runtime_values["artifact_processing_families"] = ()
        runtime_values["profile_schedule_enabled"] = False
        for key in runtime_values:
            if key.endswith("schedule_seconds"):
                runtime_values[key] = None
    return BuiltinConfig(database=database, runtime=runtime_values, inference=values)


async def _attempt(
    runtime,
    instance,
    job: dict[str, Any],
    directory: Path,
    run_id: str,
    deadline_seconds: float,
    *,
    queued_at: float | None = None,
    segment_id: str | None = None,
) -> None:
    key = digest(job)[:24]
    path = directory / "attempts" / f"{key}.json"
    if path.exists():
        return
    result = {**job, "schema": SCHEMA, "attempt_id": key, "status": "running", "reopen_equal": None}
    write_json(path, result)
    started = time.perf_counter()
    result["started_at_epoch"] = time.time()
    result["queue_wait_seconds"] = 0.0 if queued_at is None else max(0.0, started - queued_at)
    result["segment_id"] = segment_id
    generation_start = None
    events: list[dict[str, Any]] = []
    token = _USAGE.set(events)
    with collect_input_encodings() as encodings:
        try:
            async with asyncio.timeout(deadline_seconds):
                scope = await runtime.scopes.create(
                    ScopeDraft(
                        title="Public input encoding evaluation",
                        summary="Disposable SWE-bench historical evidence; not an official coding-task score.",
                        idempotency_key=f"input-eval-{run_id}-{key}",
                    )
                )
                result["scope_id"] = scope.scope_id
                write_json(path, result)
                refs = []
                for source in sources(instance):
                    captured = await runtime.sources.for_scope(scope.scope_id).capture(CaptureSource(**source))
                    refs.append(captured.source_ref)
                result["source_refs"] = [ref.model_dump(mode="json") for ref in refs]
                generation_start = time.perf_counter()
                generated = await runtime.experience.for_scope(scope.scope_id).generate(
                    GenerateExperienceRequest(
                        sources=tuple(refs),
                        reason="Extract grounded reusable lessons from the supplied historical issue and reference patch. "
                        "The evaluator has not executed tests; do not invent verification results.",
                    )
                )
                result["generation_latency_seconds"] = time.perf_counter() - generation_start
                if generated.candidate is None:
                    result["status"] = "no_proposal"
                else:
                    candidate = generated.candidate
                    result["candidate"] = candidate.model_dump(mode="json")
                    approved = await runtime.review.for_scope(scope.scope_id).approve(
                        ApproveCandidateRequest(
                            candidate_id=candidate.candidate_id,
                            expected_version=candidate.version,
                        )
                    )
                    if approved.result_artifact is None:
                        result["status"] = "approval_missing_artifact"
                    else:
                        saved = await runtime.experience.for_scope(scope.scope_id).get(
                            GetExperienceRequest(artifact=approved.result_artifact)
                        )
                        result["artifact_ref"] = approved.result_artifact.model_dump(mode="json")
                        result["content"] = saved.content.model_dump(mode="json")
                        result["content_sha256"] = digest(result["content"])
                        result["lineage_equal"] = tuple(saved.lineage.sources) == tuple(refs)
                        result["diagnostics"] = content_diagnostics(result["content"], instance)
                        result["status"] = "succeeded" if result["lineage_equal"] else "lineage_mismatch"
                statistics = await runtime.statistics.for_scope(scope.scope_id).overview()
                result["persisted_statistics"] = statistics.usage.model_dump(mode="json")
        except Exception as error:
            # Provider messages may include credentials or prompts; only retain the type.
            result["status"] = "failed"
            result["error_type"] = type(error).__name__
            privacy = find_privacy_error(error)
            if privacy is not None:
                result["privacy"] = {
                    "phase": privacy.phase,
                    "decision": privacy.decision,
                    "reason_codes": list(privacy.reason_codes),
                }
        finally:
            _USAGE.reset(token)
            result["finished_at_epoch"] = time.time()
            result["elapsed_seconds"] = time.perf_counter() - started
            result["queue_inclusive_seconds"] = result["elapsed_seconds"] + result["queue_wait_seconds"]
            if generation_start is not None and "generation_latency_seconds" not in result:
                result["generation_latency_seconds"] = time.perf_counter() - generation_start
            result["usage_events"] = events
            result["usage"] = usage_totals(events)
            result["usage_by_operation"] = {
                operation: usage_totals([event for event in events if event["operation"] == operation])
                for operation in sorted({event["operation"] for event in events})
            }
            result["encodings"] = [asdict(report) for report in encodings]
            write_json(path, result)
            print(json.dumps({"attempt_id": key, "mode": job["mode"], "status": result["status"]}), flush=True)


async def _reopen(configs, args) -> None:
    for mode, config in configs.items():
        async with open_builtin_runtime(config, scheduler_path=args.output / f"scheduler-{mode}.db") as runtime:
            for path in sorted((args.output / "attempts").glob("*.json")):
                result = json.loads(path.read_text())
                if result["mode"] != mode or "artifact_ref" not in result:
                    continue
                try:
                    saved = await runtime.experience.for_scope(result["scope_id"]).get(
                        GetExperienceRequest(artifact=ArtifactRef.model_validate(result["artifact_ref"]))
                    )
                    result["reopen_equal"] = digest(saved.content.model_dump(mode="json")) == result["content_sha256"]
                    result["reopen_lineage_equal"] = [
                        ref.model_dump(mode="json") for ref in saved.lineage.sources
                    ] == result["source_refs"]
                except Exception as error:
                    result["reopen_equal"] = False
                    result["reopen_error_type"] = type(error).__name__
                write_json(path, result)


def select_cases(catalog, case_file: Path | None, limit: int) -> tuple[str, ...]:
    if case_file is None:
        return stability_ids(catalog)[:limit]
    selected = json.loads(case_file.read_text())
    if not isinstance(selected, list) or not selected or any(not isinstance(item, str) for item in selected):
        raise ValueError("case IDs must be a nonempty JSON string array")  # noqa: TRY003
    if len(set(selected)) != len(selected):
        raise ValueError("case IDs must be unique")  # noqa: TRY003
    for case_id in selected:
        catalog.require(case_id)
    return tuple(selected[:limit])


async def run(args) -> None:
    catalog = load_catalog(args.dataset)
    selected = select_cases(catalog, args.case_ids, args.limit)
    jobs = plan_jobs(selected, args.repeats, args.seed)
    args.output.mkdir(parents=True, exist_ok=True)
    with server_settings_context(env_file=args.env_file, data_dir=args.output / "runtime") as settings:
        configs = {
            mode: _configuration(
                settings, mode, args.output, args.database_config, args.protection_config, args.max_output_tokens
            )
            for mode in MODES
        }
        manifest_path = args.output / "manifest.json"
        previous = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
        configuration_identity = prepare_configuration_identity(
            args.output, configs, previous=None if previous is None else previous.get("configuration_identity")
        )
        identity = {
            "schema": SCHEMA,
            "projection": PROJECTION,
            "dataset_sha256": PUBLIC_V2_SHA256,
            "source_hash": source_hash(),
            "config_hashes": {mode: digest(config.model_dump(mode="json")) for mode, config in configs.items()},
            "configuration_identity": configuration_identity,
            "model": settings.inference.generation_model,
            "database_kind": configs["off"].database.kind,
            "protection_mode": configs["off"].inference.protection.mode,
            "max_output_tokens_override": args.max_output_tokens,
            "cohort": args.cohort or (args.case_ids.stem if args.case_ids else "swebench-pro-stability-v1"),
            "case_ids": selected,
            "case_ids_sha256": digest(selected),
            "seed": args.seed,
            "repeats": args.repeats,
            "concurrency": args.concurrency,
            "attempt_timeout_seconds": args.timeout,
            "jobs": jobs,
            "source_claim": "public historical reference patches; tests not executed; not official SWE-bench scoring",
        }
        # JSON canonicalizes tuples to arrays for reliable resume equality.
        identity = json.loads(json.dumps(identity))
        if previous is not None:
            if previous != identity:
                raise ValueError("resume identity changed; use a new output directory")  # noqa: TRY003
        else:
            write_json(manifest_path, identity)
        run_id = digest(str(args.output.resolve()))[:16]
        pending = [job for job in jobs if not (args.output / "attempts" / f"{digest(job)[:24]}.json").exists()]
        segment_id = str(time.time_ns())
        segment = {
            "segment_id": segment_id,
            "status": "running",
            "started_at_epoch": time.time(),
            "pending_attempt_ids": [digest(job)[:24] for job in pending],
            "concurrency": args.concurrency,
        }
        segment_path = args.output / "segments" / f"{segment_id}.json"
        write_json(segment_path, segment)
        startup = time.perf_counter()
        semaphore = asyncio.Semaphore(args.concurrency)
        try:
            with observe_usage():
                async with AsyncExitStack() as stack:
                    runtimes = {
                        mode: await stack.enter_async_context(
                            open_builtin_runtime(config, scheduler_path=args.output / f"scheduler-{mode}.db")
                        )
                        for mode, config in configs.items()
                    }
                    execution_start = time.perf_counter()
                    segment["startup_seconds"] = execution_start - startup
                    segment["execution_started_at_epoch"] = time.time()

                    async def execute(job):
                        queued_at = time.perf_counter()
                        async with semaphore:
                            await _attempt(
                                runtimes[job["mode"]],
                                catalog.require(job["instance_id"]),
                                job,
                                args.output,
                                run_id,
                                args.timeout,
                                queued_at=queued_at,
                                segment_id=segment_id,
                            )

                    await asyncio.gather(*(execute(job) for job in pending))
                    segment["execution_wall_seconds"] = time.perf_counter() - execution_start
                    segment["execution_finished_at_epoch"] = time.time()
                    closing = time.perf_counter()
                segment["close_seconds"] = time.perf_counter() - closing
            reopen_start = time.perf_counter()
            await _reopen(configs, args)
            segment["reopen_validation_seconds"] = time.perf_counter() - reopen_start
            segment["status"] = "completed"
        except Exception as error:
            segment["status"] = "failed"
            segment["error_type"] = type(error).__name__
            raise
        finally:
            segment["finished_at_epoch"] = time.time()
            segment["total_wall_seconds"] = time.perf_counter() - startup
            write_json(segment_path, segment)
