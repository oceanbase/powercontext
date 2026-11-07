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

"""Integrity-checked aggregate artifacts and conservative production decision gates."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
import sqlite3
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from .comparison import compare_preparation
from .manifests import EvaluationError, Suite, file_hash, read_json
from .models import Sample, canonical, digest
from .runner import instructions_for


def code_identity(suite: Suite) -> dict:
    import powercontext

    package = Path(__file__).resolve().parent
    repository = next((parent for parent in package.parents if (parent / ".git").exists()), None)
    python_paths = sorted(package.glob("*.py"))
    data_paths = sorted((*suite.root.glob("manifests/*.json"), *suite.root.glob("fixtures/*.json")))
    dependency_paths = (
        [repository / name for name in ("pyproject.toml", "uv.lock", "evaluation/pyproject.toml", "evaluation/uv.lock")]
        if repository
        else []
    )
    paths = python_paths + data_paths + dependency_paths
    suite_hash = digest(
        {
            **{f"python/{path.name}": file_hash(path) for path in python_paths},
            **{f"data/{path.relative_to(suite.root)}": file_hash(path) for path in data_paths},
        }
    )
    runtime = Path(powercontext.__file__).resolve().parent
    runtime_hash = digest({str(path.relative_to(runtime)): file_hash(path) for path in sorted(runtime.rglob("*.py"))})
    revision = "unavailable"
    if repository is not None:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repository, capture_output=True, text=True, check=True
        ).stdout.strip()
    committed = repository is not None
    for path in paths:
        if repository is None or not path.is_relative_to(repository):
            committed = False
            break
        stored = subprocess.run(
            ["git", "show", f"HEAD:{path.relative_to(repository).as_posix()}"],
            cwd=repository,
            capture_output=True,
            check=False,
        )
        if stored.returncode or stored.stdout != path.read_bytes():
            committed = False
            break
    return {
        "revision": revision,
        "suite_code_hash": suite_hash,
        "runtime_code_hash": runtime_hash,
        "frozen_committed": committed,
        "frozen_hashes": suite.frozen_hashes,
        "instruction_hash": hashlib.sha256(instructions_for(suite).encode()).hexdigest(),
        "estimator": suite.experiment.estimator,
        "dependency_hashes": {str(path.relative_to(repository)): file_hash(path) for path in dependency_paths}
        if repository
        else {},
        "versions": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            **{
                name: importlib.metadata.version(name)
                for name in ("powercontext", "pydantic", "sqlalchemy", "pydantic-ai-slim")
            },
        },
    }


def aggregate(suite: Suite, samples: tuple[Sample, ...], manifest: dict) -> dict:
    expected_schema = (
        "evidence-admission.sample.v2" if suite.experiment.citation_protocol else "evidence-admission.sample.v1"
    )
    if any(sample.schema_version != expected_schema for sample in samples):
        raise EvaluationError("sample_protocol_mismatch")
    groups = defaultdict(list)
    for sample in samples:
        groups[(sample.case_hash, sample.arm, sample.budget)].append(sample)
    nondeterministic = []
    for key, group in groups.items():
        signatures = {
            digest(
                {
                    "input": sample.input_hashes,
                    "materials": sample.materializations,
                    "projections": sample.projections,
                    "completeness": sample.completeness,
                    "omissions": [o.model_dump() for o in sample.omissions],
                }
            )
            for sample in group
        }
        if len(signatures) != 1:
            nondeterministic.append(digest(key))
    counts = {}
    for arm in suite.experiment.arms:
        rows = [sample for sample in samples if sample.arm == arm]
        scored = [s.preparation for s in rows if s.preparation and s.preparation.required_content_fits]
        generations = [s.generation for s in rows if s.generation is not None]
        generated_required = sum(len(s.retained_claim_hashes) for s in generations)
        generation_required = sum(s.preparation.required_count for s in rows if s.generation and s.preparation)
        generation_calls = sum(s.generation_calls for s in rows)
        input_usage = [s.provider_input_tokens for s in rows if s.provider_input_tokens is not None]
        output_usage = [s.provider_output_tokens for s in rows if s.provider_output_tokens is not None]
        counts[arm] = {
            "samples": len(rows),
            "statuses": dict(Counter(s.status for s in rows)),
            "completeness": dict(Counter(s.completeness for s in rows)),
            "eligible_required_claims": sum(s.required_count for s in scored),
            "retained_eligible_claims": sum(len(s.retained_claim_hashes) for s in scored),
            "exact_literals_total": sum(s.preparation.exact_literals_total for s in rows if s.preparation),
            "exact_literals_retained": sum(s.preparation.exact_literals_retained for s in rows if s.preparation),
            "prepared_requests": sum(s.metrics.prepared_requests for s in rows),
            "prepared_tokens_total": sum(s.metrics.prepared_tokens_total for s in rows),
            **(
                {"selection_prepared_tokens_total": sum(s.metrics.selection_prepared_tokens_total or 0 for s in rows)}
                if suite.experiment.selection_cost_protocol
                else {}
            ),
            "requests": sum(s.requests for s in rows),
            "generation_calls": generation_calls,
            "generation_scored_samples": len(generations),
            "generation_required_claims": generation_required if generations else None,
            "generated_required_claims": generated_required if generations else None,
            "generated_exact_literals": sum(len(s.retained_literal_hashes) for s in generations)
            if generations
            else None,
            "generation_retention_rate": generated_required / generation_required if generation_required else None,
            "candidates": sum(s.candidates for s in generations) if generations else None,
            "exact_duplicates": sum(s.exact_duplicates for s in generations) if generations else None,
            "near_duplicates": sum(s.near_duplicates for s in generations) if generations else None,
            "unsupported": sum(s.unsupported for s in generations) if generations else None,
            "false_attribution": sum(s.false_attribution for s in generations) if generations else None,
            "invalid_support": sum(s.invalid_support for s in generations) if generations else None,
            "unresolved": sum(s.unresolved for s in generations) if generations else None,
            "provider_input_tokens": sum(input_usage) if input_usage else None,
            "provider_output_tokens": sum(output_usage) if output_usage else None,
            "usage_complete": all(s.usage_complete for s in rows if s.generation_calls) if generation_calls else None,
            "admitted_bytes": sum(s.metrics.admitted_bytes for s in rows),
            "omitted_bytes": sum(s.metrics.omitted_bytes for s in rows),
            "materialization_seconds": sum(s.materialization_seconds for s in rows),
            "admission_seconds": sum(s.admission_seconds for s in rows),
            "generation_seconds": sum(s.generation_seconds for s in rows),
            "value_bytes_by_family": dict(sum((Counter(s.metrics.value_bytes_by_family) for s in rows), Counter())),
            "input_estimator_units_by_family": dict(
                sum((Counter(s.metrics.input_estimator_units_by_family) for s in rows), Counter())
            ),
        }
    candidate_arm = suite.experiment.candidate_arm
    candidate = [s for s in samples if s.arm == candidate_arm]
    comparison = compare_preparation(suite, samples, manifest["repetitions"]) if suite.experiment.v2 else None
    baseline_comparisons = []
    if suite.experiment.v3:
        baseline_comparisons = [
            compare_preparation(suite, samples, manifest["repetitions"], baseline=arm, candidate="bounded-fragments-v3")
            for arm in ("bounded-fragments-v1", "bounded-fragments-v2")
        ]
        comparison = baseline_comparisons[-1]
    expected = {
        (digest(case.model_dump(mode="json")), arm, budget, repetition)
        for case in suite.cases
        for arm in suite.experiment.arms
        for budget in suite.experiment.budgets
        for repetition in range(manifest["repetitions"])
    }
    actual = {(s.case_hash, s.arm, s.budget, s.repetition) for s in samples}
    required_loss = [
        s.sample_id
        for s in candidate
        if s.preparation
        and s.preparation.required_content_fits
        and (
            s.preparation.missing_claim_hashes
            or s.preparation.exact_literals_retained != s.preparation.exact_literals_total
        )
    ]
    cases = {digest(case.model_dump(mode="json")): case for case in suite.cases}
    generation_loss = [
        s.sample_id
        for s in candidate
        if s.preparation
        and s.preparation.required_content_fits
        and s.generation
        and (
            not {digest(c.claim_id) for c in cases[s.case_hash].required} <= set(s.generation.retained_claim_hashes)
            or not {
                digest({"claim": c.claim_id, "literal": index})
                for c in cases[s.case_hash].required
                for index, _ in enumerate(c.exact_literals)
            }
            <= set(s.generation.retained_literal_hashes)
        )
    ]
    critical = [
        s.sample_id
        for s in samples
        if s.memory_mutations
        or s.published_cursor
        or (
            s.arm == candidate_arm
            and s.generation
            and (s.generation.unsupported or s.generation.false_attribution or s.generation.invalid_support)
        )
        or (s.status in {"prepared", "generated"} and s.metrics.prepared_tokens > s.budget)
        or (s.completeness == "bounded" and not s.omissions)
        or (
            (policy := suite.experiment.policy_for(s.arm)) is not None
            and (
                s.metrics.prepared_requests > policy.max_calls_per_sample
                or s.metrics.prepared_tokens_total > policy.total_input_budget_multiplier * s.budget
            )
        )
    ]
    if suite.experiment.citation_protocol:
        critical.extend(
            s.sample_id
            for s in samples
            if s.failure_kind == "invalid_evidence_reference" and s.sample_id not in critical
        )
    for paired in baseline_comparisons or ([comparison] if comparison else []):
        critical.extend(row["sample_id"] for row in paired["regressions"] if row["sample_id"] not in critical)
    unresolved = [s.sample_id for s in samples if s.generation and s.generation.unresolved]
    ordinary_cases = {
        digest(c.model_dump(mode="json")) for c in suite.cases if c.fault == "none" and c.cancel_phase == "none"
    }
    preparation_outcome_failures = [
        s.sample_id
        for s in samples
        if s.case_hash in ordinary_cases
        and (
            s.preparation is None
            or s.status not in {"prepared", "generated", "budget_rejected"}
            or (s.arm == candidate_arm and s.status == "budget_rejected")
        )
    ]
    live_incomplete = []
    for sample in samples:
        if sample.case_hash not in ordinary_cases:
            continue
        generated = sample.status == "generated" and sample.generation is not None
        # A control may reject without calling a model. Accepted subwindows still need generation coverage.
        expected_rejection = (
            sample.arm != candidate_arm
            and sample.status == "budget_rejected"
            and sample.reason == "input_budget"
            and (sample.generation is not None if sample.input_hashes else sample.generation is None)
        )
        if not (generated or expected_rejection):
            live_incomplete.append(sample.sample_id)
    live_covered = manifest["mode"] in {"live", "audited_live"} and not live_incomplete
    fault_cases = {digest(c.model_dump(mode="json")) for c in suite.cases if c.fault != "none"}
    fault_failures = [
        s.sample_id
        for s in samples
        if s.case_hash in fault_cases and (s.status != "unavailable" or s.completeness != "unavailable")
    ]
    cancellable = {digest(c.model_dump(mode="json")) for c in suite.cases if c.cancel_phase != "none"}
    cancellations_valid = all(
        s.status == "cancelled"
        and s.completeness == "cancelled"
        and s.memory_mutations == 0
        and s.published_cursor == 0
        for s in samples
        if s.case_hash in cancellable
    )
    improvement = []
    single_cases = {
        digest(c.model_dump(mode="json")) for c in suite.cases if len(c.sources) == 1 and c.reducibility == "fragments"
    }
    for sample in candidate:
        if (
            sample.case_hash not in single_cases
            or sample.status != "generated"
            or not sample.preparation
            or sample.preparation.missing_claim_hashes
            or not sample.preparation.required_content_fits
            or not sample.preparation.required_count
            or sample.sample_id in generation_loss
        ):
            continue
        controls = groups[(sample.case_hash, "window-reduction-v1", sample.budget)]
        if controls and all(
            s.status == "budget_rejected"
            and s.window_attempts
            and s.window_attempts[-1][1] - s.window_attempts[-1][0] == 1
            for s in controls
        ):
            improvement.append(sample.sample_id)
    coverage = actual == expected and len(samples) == len(expected)
    insufficient = (
        suite.private
        or bool(suite.experiment.v3)
        or not (coverage and live_covered and manifest["code"]["frozen_committed"] and cancellations_valid)
        or bool(unresolved or fault_failures or preparation_outcome_failures)
        or any(
            pair["reference_mismatches"] or not pair["paired_coverage_complete"]
            for pair in baseline_comparisons or ([comparison] if comparison else [])
        )
    )
    if insufficient:
        decision = "insufficient_evidence"
    elif not critical and not required_loss and not generation_loss and not nondeterministic and improvement:
        decision = "bounded_projection_follow_up_required"
    else:
        decision = "no_production_change"
    main_budget = 4096 if 4096 in suite.experiment.budgets else suite.experiment.budgets[0]
    labels = {digest(case.model_dump(mode="json")): case.case_id for case in suite.cases}
    fixture_results = [
        {
            "label": labels[s.case_hash],
            "arm": s.arm,
            "budget": s.budget,
            "status": s.status,
            "completeness": s.completeness,
            "retained": len(s.preparation.retained_claim_hashes) if s.preparation else None,
            "required": s.preparation.required_count if s.preparation else None,
            "jointly_fits": s.preparation.required_content_fits if s.preparation else None,
        }
        for s in samples
        if s.repetition == 0 and s.budget == main_budget
    ]
    result: dict = {
        "schema": "evidence-admission.report.v2"
        if suite.experiment.citation_protocol
        else "evidence-admission.report.v1",
        "decision": decision,
        "candidate_arm": candidate_arm,
        "comparison": comparison,
        "baseline_comparisons": baseline_comparisons,
        "model_free": manifest["mode"] == "prepare",
        "coverage_complete": coverage,
        "live_coverage_complete": live_covered,
        "live_incomplete_samples": live_incomplete if manifest["mode"] != "prepare" else [],
        "fault_outcome_failures": fault_failures,
        "preparation_outcome_failures": preparation_outcome_failures,
        "deterministic_preparation": not nondeterministic,
        "nondeterministic_groups": nondeterministic,
        "required_retention_failures": required_loss,
        "generation_retention_failures": generation_loss,
        "critical_failures": critical,
        "unresolved_samples": unresolved,
        "cancellation_valid": cancellations_valid,
        "one_source_improvement_samples": improvement,
        "arms": counts,
        "structured_tiered": "not_applicable_no_trustworthy_structural_adapter",
        "preparation_first_replay": fixture_results,
        "limitations": [
            "synthetic_sources_are_not_observed_host_payloads",
            "sqlite_only_no_oceanbase_claim",
            "fragment_references_are_evaluation_only",
            "local_task_cancellation_does_not_prove_remote_provider_cancellation",
            "no_production_memory_publication_or_worker_recovery_claim",
            *(["v3_requires_combined_dataset_review"] if suite.experiment.v3 else []),
            *(["live_generation_not_run"] if manifest["mode"] == "prepare" else []),
            *(["freeze_not_committed"] if not manifest["code"]["frozen_committed"] else []),
            *(["private_corpus_does_not_replace_synthetic_gate"] if suite.private else []),
        ],
    }

    if manifest["mode"] in {"smoke", "audited_smoke"}:
        scope = manifest["smoke"]
        expected_smoke = {
            (digest(c.model_dump(mode="json")), arm, budget, repetition)
            for c in suite.cases
            for arm in suite.experiment.arms
            for budget in scope["budgets"]
            for repetition in range(scope["repetitions"])
        }
        returned = all(
            (s.status == "generated" and s.generation is not None)
            or (s.arm != candidate_arm and s.status == "budget_rejected" and not s.input_hashes)
            for s in samples
        )
        usage = all(
            s.usage_complete
            and s.generation_calls == s.metrics.prepared_requests
            and s.requests == s.generation_calls * suite.experiment.max_requests_per_call
            and s.provider_input_tokens is not None
            and s.provider_output_tokens is not None
            for s in samples
            if s.metrics.prepared_requests
        )
        smoke_result: dict[str, Any] = {
            "formal_matrix": False,
            "scope_coverage_complete": actual == expected_smoke and len(samples) == len(expected_smoke),
            "execution_complete": bool(
                actual == expected_smoke and len(samples) == len(expected_smoke) and returned and usage and not critical
            ),
            "provider_requests": sum(s.requests for s in samples),
            "unresolved_samples": len(unresolved),
        }
        if suite.experiment.citation_protocol:
            all_arm_quality_failures = [
                s.sample_id
                for s in samples
                if s.generation
                and (s.generation.unsupported or s.generation.false_attribution or s.generation.invalid_support)
            ]
            request_cap = manifest["provider"]["max_requests"]
            cap_valid = (
                sum(s.requests for s in samples) <= request_cap
                and sum(s.generation_calls for s in samples) * suite.experiment.max_requests_per_call <= request_cap
            )
            quality = not (critical or required_loss or generation_loss or unresolved or all_arm_quality_failures)
            smoke_result["all_arm_quality_failures"] = all_arm_quality_failures
            smoke_result["request_cap_breached"] = not cap_valid
            smoke_result["ready_for_formal"] = bool(
                manifest["mode"] == "audited_smoke" and smoke_result["execution_complete"] and quality and cap_valid
            )
        result["smoke"] = smoke_result
        result["limitations"].append("smoke_scope_does_not_replace_formal_matrix")
        result["decision"] = "insufficient_evidence"
    if suite.experiment.citation_protocol:
        result["generation_failures"] = [
            {"sample_id": s.sample_id, "phase": s.failure_phase, "kind": s.failure_kind}
            for s in samples
            if s.status == "failed"
        ]
    return result


def markdown(report: dict) -> str:
    lines = [
        "# Evidence admission evaluation",
        "",
        f"Decision: `{report['decision']}`",
        "",
        f"Model-free preparation: {report['model_free']}. Live coverage: {report['live_coverage_complete']}.",
        f"Deterministic preparation: {report['deterministic_preparation']}. Cancellation valid: {report['cancellation_valid']}.",
        "",
        "| Arm | Samples | Prepared | Budget rejected | Cancelled | Unavailable | Eligible claims retained | Exact literals retained |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for arm, row in report["arms"].items():
        status = row["statuses"]
        lines.append(
            f"| {arm} | {row['samples']} | {status.get('prepared', 0)} | {status.get('budget_rejected', 0)} | {status.get('cancelled', 0)} | {status.get('unavailable', 0)} | {row['retained_eligible_claims']}/{row['eligible_required_claims']} | {row['exact_literals_retained']}/{row['exact_literals_total']} |"
        )
    lines.extend(
        [
            "",
            "Eligible facts use the jointly feasible reference input; literal counts include every preparation-scored sample.",
            "",
            "## Preparation by fixture (first replay, main budget)",
            "",
            "| Fixture | Arm | Budget | Status | Completeness | Required spans retained | Jointly fits |",
            "| --- | --- | ---: | --- | --- | ---: | --- |",
        ]
    )
    for row in report["preparation_first_replay"]:
        retained = "unavailable" if row["required"] is None else f"{row['retained']}/{row['required']}"
        lines.append(
            f"| {row['label']} | {row['arm']} | {row['budget']} | {row['status']} | {row['completeness']} | {retained} | {row['jointly_fits']} |"
        )
    lines.extend(
        [
            "",
            f"Candidate preparation samples with required-content loss: {len(report['required_retention_failures'])}.",
            "Critical quality decisions are per sample; aggregate retention is descriptive only.",
            "",
            "## Generation and usage",
            "",
        ]
    )
    if report["model_free"]:
        lines.extend(["Live generation: **not run**. Provider-dependent quality and usage metrics are unmeasured.", ""])
    lines.extend(
        [
            "| Arm | Scored samples | Required facts generated | Candidates | Exact / near duplicates | Unsupported / false attribution / invalid support / unresolved |",
            "| --- | ---: | ---: | ---: | ---: | ---: |",
        ]
    )
    for arm, row in report["arms"].items():
        facts = (
            "unmeasured"
            if row["generation_required_claims"] is None
            else f"{row['generated_required_claims']}/{row['generation_required_claims']}"
        )
        lines.append(
            f"| {arm} | {row['generation_scored_samples']} | {facts} | {row['candidates']} | {row['exact_duplicates']} / {row['near_duplicates']} | {row['unsupported']} / {row['false_attribution']} / {row['invalid_support']} / {row['unresolved']} |"
        )
    lines.extend(
        [
            "",
            "Generation recall uses all required claims in each scored sample, including omitted or impossible claims.",
            "Partial window generations are included; their fixture may still be budget-rejected. Missing generations are not scored as zero recall.",
            "",
            "| Arm | Extractor attempts | Reported requests | Input tokens | Output tokens | Usage complete |",
            "| --- | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    for arm, row in report["arms"].items():
        lines.append(
            f"| {arm} | {row['generation_calls']} | {row['requests']} | {row['provider_input_tokens']} | {row['provider_output_tokens']} | {row['usage_complete']} |"
        )
    lines.extend(
        [
            "",
            "Token and request totals contain reported successful-call usage only; incomplete usage is not a zero-cost claim.",
            "Currency costs require the selected provider's prices and are not estimated here.",
            "",
            "## Limitations",
            "",
        ]
    )
    comparison = report.get("comparison")
    if comparison:
        lines.extend(
            [
                "",
                f"## {comparison['candidate_arm']} paired preparation comparison",
                "",
                f"No observed fact/literal/status regression: {comparison['no_observed_regression']}.",
                f"Fully fixed baseline failure combinations: {comparison['fully_fixed_combinations']}/{len(comparison['failing_combinations'])}.",
                "",
                "| Fixture | Budget | Baseline facts | Candidate facts | Repetitions | Fully fixed |",
                "| --- | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in comparison["failing_combinations"]:
            lines.append(
                f"| {row['label']} | {row['budget']} | {row['baseline_retained']}/{row['required']} | {row['candidate_retained']}/{row['required']} | {row['repetitions']} | {row['fully_fixed']} |"
            )
        lines.extend(["", "| Arm | Prepared calls | Total estimated input tokens |", "| --- | ---: | ---: |"])
        for arm, row in report["arms"].items():
            lines.append(f"| {arm} | {row['prepared_requests']} | {row['prepared_tokens_total']} |")
        lines.extend(
            [
                "",
                "Prepared calls are potential live calls, not provider requests. Each request and the versioned cumulative caps are checked.",
                "Generation quality is unmeasured. Window arms may use more than one call per sample.",
                "",
                "## Limitation details",
                "",
            ]
        )
    if "smoke" in report:
        lines.extend(["", "## Smoke gate", "", f"`{json.dumps(report['smoke'], sort_keys=True)}`", ""])
    if "generation_failures" in report:
        lines.extend(
            [
                "",
                "## Generation failures",
                "",
                *[
                    f"- `{row['sample_id']}`: `{row['phase']}` / `{row['kind']}`"
                    for row in report["generation_failures"]
                ],
                "",
            ]
        )
    lines.extend(f"- `{item}`" for item in report["limitations"])
    return "\n".join(lines) + "\n"


def publish(output: Path, manifest: dict, samples: tuple[Sample, ...], report: dict) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        output.mkdir()
    except FileExistsError:
        raise EvaluationError("refusing_existing_output") from None
    with TemporaryDirectory(prefix=".evidence-staging-", dir=output.parent) as directory:
        staging = Path(directory)
        records = [s.model_dump(mode="json") for s in samples]
        for name, value in (("run-manifest.json", manifest), ("aggregate-report.json", report)):
            (staging / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for name, rows in (
            ("preparation-results.jsonl", records),
            ("generation-results.jsonl", [row for row in records if row["generation"] is not None]),
        ):
            (staging / name).write_bytes(b"".join(canonical(row) + b"\n" for row in rows))
        (staging / "REPORT.md").write_text(markdown(report), encoding="utf-8")
        if report["decision"] == "bounded_projection_follow_up_required":
            (staging / "FOLLOW_UP.md").write_text(_follow_up(report, manifest), encoding="utf-8")
        hashes = {path.name: file_hash(path) for path in staging.iterdir()}
        (staging / "artifact-hashes.json").write_text(
            json.dumps(hashes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        for path in staging.iterdir():
            path.replace(output / path.name)
        (output / "COMPLETE.json").write_text(
            json.dumps(
                {
                    "schema": "evidence-admission.complete.v1",
                    "hashes": file_hash(output / "artifact-hashes.json"),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )


def load_run(path: Path) -> tuple[dict, tuple[Sample, ...], dict]:
    marker = read_json(path / "COMPLETE.json")
    if marker != {"schema": "evidence-admission.complete.v1", "hashes": file_hash(path / "artifact-hashes.json")}:
        raise EvaluationError("incomplete_or_modified_run")
    hashes = read_json(path / "artifact-hashes.json")
    allowed = {
        "run-manifest.json",
        "aggregate-report.json",
        "preparation-results.jsonl",
        "generation-results.jsonl",
        "REPORT.md",
        "FOLLOW_UP.md",
    }
    if set(hashes) - allowed or not (allowed - {"FOLLOW_UP.md"}) <= set(hashes):
        raise EvaluationError("invalid_artifact_set")
    if any(file_hash(path / name) != sha for name, sha in hashes.items()):
        raise EvaluationError("artifact_hash_mismatch")
    samples = tuple(
        Sample.model_validate(json.loads(line))
        for line in (path / "preparation-results.jsonl").read_text().splitlines()
    )
    return read_json(path / "run-manifest.json"), samples, read_json(path / "aggregate-report.json")


def _follow_up(report: dict, manifest: dict) -> str:
    identity = digest(manifest)
    return f"""# Bounded Source evidence admission contract

Evidence: evaluation manifest `{identity}`; report decision `{report["decision"]}`.

The reproduced failures are the samples listed in `one_source_improvement_samples`.
Design admission at the Source-to-AtomicMemoryExtractionInput boundary. Preserve raw materializations.
Specify Source/materialization/field/range identity and citation semantics before changing public contracts.
Version the projection and selection policy independently, including historic materialization rebuild.
Keep completeness and stable omission codes explicit; define cancellation, retry and idempotency semantics.
Preserve authorization and Scope isolation; never infer authority from text, fragments or host metadata.
Define existing-Source compatibility, migration, rollback, API impact and operational work/storage limits.
Add regression coverage for every reproduced failure, with #2069 ownership projection coordination.

This contract requires a separate production design and implementation issue.
"""
