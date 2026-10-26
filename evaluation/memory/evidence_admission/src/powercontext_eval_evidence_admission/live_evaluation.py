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

"""Cross-dataset live output gates and an all-arm provider request plan."""

from __future__ import annotations

import re
from pathlib import Path

from .manifests import EvaluationError, Suite, file_hash, live_policy
from .models import digest
from .verification import combined_preparation, verified_runs


def live_plan(base: Suite, preparation_dirs: tuple[Path, ...]) -> dict:
    comparison = combined_preparation(base, preparation_dirs)
    if not comparison["offline_preparation_passed"]:
        raise EvaluationError("live_plan_requires_passing_preparation")
    runs = verified_runs(base, preparation_dirs, {"prepare"})
    datasets = {}
    total_calls = total_input = 0
    for dataset, (_, manifest, samples, report, path) in runs.items():
        arms = {}
        for arm, row in report["arms"].items():
            calls = row["prepared_requests"]
            arms[arm] = {
                "prepared_calls": calls,
                "provider_request_cap": calls * base.experiment.max_requests_per_call,
                "estimated_input_tokens": row["prepared_tokens_total"],
                "maximum_output_tokens": calls
                * base.experiment.max_requests_per_call
                * base.experiment.max_output_tokens,
            }
        calls = sum(a["provider_request_cap"] for a in arms.values())
        inputs = sum(a["estimated_input_tokens"] for a in arms.values())
        datasets[dataset] = {
            "preparation_manifest_hash": digest(manifest),
            "artifact_hashes_sha256": file_hash(path / "artifact-hashes.json"),
            "arms": arms,
            "provider_request_cap": calls,
            "estimated_input_tokens": inputs,
            "maximum_output_tokens": calls * base.experiment.max_output_tokens,
        }
        total_calls += calls
        total_input += inputs
    smoke = live_policy(base.root)["smoke"]
    samples = runs[smoke["dataset"]][2]
    selected_hashes = {
        digest(c.model_dump(mode="json")) for c in runs[smoke["dataset"]][0].cases if c.case_id in smoke["cases"]
    }
    smoke_rows = [
        s
        for s in samples
        if s.case_hash in selected_hashes and s.budget in smoke["budgets"] and s.repetition < smoke["repetitions"]
    ]
    smoke_calls = sum(s.metrics.prepared_requests for s in smoke_rows)
    return {
        "schema": "evidence-admission.live-plan.v1",
        "live_policy_sha256": base.frozen_hashes["live_policy"],
        "preparation_run_set_hash": comparison["run_set_hash"],
        "code": next(iter(runs.values()))[1]["code"],
        "datasets": datasets,
        "formal_provider_request_cap": total_calls,
        "formal_estimated_input_tokens": total_input,
        "formal_maximum_output_tokens": total_calls * base.experiment.max_output_tokens,
        "smoke": {
            **smoke,
            "samples": len(smoke_rows),
            "prepared_calls": smoke_calls,
            "provider_request_cap": smoke_calls * base.experiment.max_requests_per_call,
            "estimated_input_tokens": sum(s.metrics.prepared_tokens_total for s in smoke_rows),
            "maximum_output_tokens": smoke_calls
            * base.experiment.max_requests_per_call
            * base.experiment.max_output_tokens,
        },
        "settings": {
            "model_settings": base.experiment.model_settings,
            "max_output_tokens": base.experiment.max_output_tokens,
            "timeout_seconds": base.experiment.timeout_seconds,
            "max_requests_per_call": base.experiment.max_requests_per_call,
            "concurrency": base.experiment.concurrency,
            "sdk_retries": 0,
        },
        "provider_configuration": "required_not_selected_by_plan",
        "currency_cost": None,
        "actual_provider_usage": None,
        "prerequisites": [
            "committed_live_freeze",
            "explicit_provider_model_endpoint_and_credential_selector",
            "separate_successful_smoke",
            "private_output_and_review_directories",
        ],
    }


def _expected_outputs(case):
    claims = {digest(c.claim_id) for c in case.required}
    literals = {
        digest({"claim": c.claim_id, "literal": index})
        for c in case.required
        for index, _ in enumerate(c.exact_literals)
    }
    return claims, literals


def _generation_comparison(suite, samples, baseline):
    pairs = {}
    for s in samples:
        if s.arm in {baseline, suite.experiment.candidate_arm}:
            pairs.setdefault((s.case_hash, s.budget, s.repetition), {})[s.arm] = s
    losses = []
    compared = 0
    for pair in pairs.values():
        before, after = pair.get(baseline), pair.get(suite.experiment.candidate_arm)
        if before is None or after is None or before.generation is None or after.generation is None:
            continue
        compared += 1
        facts = set(before.generation.retained_claim_hashes) - set(after.generation.retained_claim_hashes)
        literals = set(before.generation.retained_literal_hashes) - set(after.generation.retained_literal_hashes)
        if facts or literals:
            losses.append(
                {
                    "sample_id": after.sample_id,
                    "missing_claim_hashes": sorted(facts),
                    "missing_literal_hashes": sorted(literals),
                    "jointly_fits": bool(after.preparation and after.preparation.required_content_fits),
                }
            )
    return {
        "baseline_arm": baseline,
        "candidate_arm": suite.experiment.candidate_arm,
        "identity_losses": losses,
        "paired_generated_samples": compared,
        "no_observed_generation_regression": not losses if compared else None,
        "scope": "descriptive_all_cases_including_impossible",
    }


def combined_live(
    base: Suite, preparation_dirs: tuple[Path, ...], original_dirs: tuple[Path, ...], audited_dirs: tuple[Path, ...]
) -> dict:
    offline = combined_preparation(base, preparation_dirs)
    prepared = verified_runs(base, preparation_dirs, {"prepare"})
    original = verified_runs(base, original_dirs, {"live"})
    audited = verified_runs(base, audited_dirs, {"audited_live"})
    policy = live_policy(base.root)
    provider_identity = None
    incomplete, quality = [], []
    rows = {}
    improvement = False
    for dataset, (suite, manifest, samples, report, path) in audited.items():
        _, parent, parent_samples, _, parent_path = original[dataset]
        review_hash = manifest.get("review_hash", "")
        if manifest.get("parent_manifest_hash") != digest(parent) or not re.fullmatch(r"[0-9a-f]{64}", review_hash):
            raise EvaluationError("audit_parent_or_review_mismatch")
        expected_manifest = {
            **parent,
            "mode": "audited_live",
            "parent_manifest_hash": digest(parent),
            "review_hash": review_hash,
        }
        if manifest != expected_manifest:
            raise EvaluationError("audit_changed_run_identity")
        parents = {s.sample_id: s for s in parent_samples}
        if len(parents) != len(parent_samples) or {s.sample_id for s in samples} != set(parents):
            raise EvaluationError("audit_changed_sample_set")
        for s in samples:
            before = parents[s.sample_id]
            if s.model_dump(exclude={"generation"}) != before.model_dump(exclude={"generation"}):
                raise EvaluationError("audit_changed_execution_or_usage")
            if (s.generation is None) != (before.generation is None):
                raise EvaluationError("audit_created_or_removed_generation")
            if s.generation and any(
                getattr(s.generation, key) != getattr(before.generation, key)
                for key in ("candidates", "candidate_hashes", "support", "exact_duplicates", "near_duplicates")
            ):
                raise EvaluationError("audit_changed_candidates_or_support")
        provider = parent["provider"]
        identity = {k: provider[k] for k in ("provider", "model", "endpoint_hash")}
        if provider_identity is not None and provider_identity != identity:
            raise EvaluationError("provider_model_or_endpoint_mismatch")
        provider_identity = identity
        prep_samples = {(s.case_hash, s.arm, s.budget, s.repetition): s for s in prepared[dataset][2]}
        if len(prep_samples) != len(prepared[dataset][2]):
            raise EvaluationError("duplicate_preparation_identity")
        input_keys = (
            "input_hashes",
            "materializations",
            "projections",
            "source_hashes",
            "definitions",
            "projection_versions",
            "metrics",
            "preparation",
            "supports",
            "omissions",
            "window_attempts",
            "completeness",
        )
        for s in samples:
            prep = prep_samples.get((s.case_hash, s.arm, s.budget, s.repetition))
            if prep is None or any(getattr(s, key) != getattr(prep, key) for key in input_keys):
                raise EvaluationError("live_inputs_differ_from_preparation")
        case_lookup = {digest(c.model_dump(mode="json")): c for c in suite.cases}
        usage_failures = []
        request_cap_breached = (
            sum(s.requests for s in samples) > provider["max_requests"]
            or sum(s.generation_calls for s in samples) * suite.experiment.max_requests_per_call
            > provider["max_requests"]
        )
        eligible_required = eligible_retained = literal_required = literal_retained = 0
        for s in samples:
            case = case_lookup[s.case_hash]
            if case.fault != "none" or case.cancel_phase != "none":
                continue
            if s.metrics.prepared_requests and (
                s.generation_calls != s.metrics.prepared_requests
                or len(s.output_hashes) != s.metrics.prepared_requests
                or s.requests != s.metrics.prepared_requests * suite.experiment.max_requests_per_call
                or not s.usage_complete
                or s.provider_input_tokens is None
                or s.provider_output_tokens is None
                or s.provider_input_tokens < 0
                or s.provider_output_tokens < 0
            ):
                usage_failures.append(s.sample_id)
            if not s.metrics.prepared_requests and (
                s.generation is not None or s.generation_calls or s.requests or s.output_hashes
            ):
                usage_failures.append(s.sample_id)
            if s.arm == suite.experiment.candidate_arm and s.preparation and s.preparation.required_content_fits:
                claims, literals = _expected_outputs(case)
                eligible_required += len(claims)
                literal_required += len(literals)
            if s.generation:
                claims, literals = _expected_outputs(case)
                if (
                    not set(s.generation.retained_claim_hashes) <= claims
                    or not set(s.generation.retained_literal_hashes) <= literals
                ):
                    raise EvaluationError("unknown_generated_claim_or_literal_identity")
                if s.arm == suite.experiment.candidate_arm and s.preparation and s.preparation.required_content_fits:
                    eligible_retained += len(claims & set(s.generation.retained_claim_hashes))
                    literal_retained += len(literals & set(s.generation.retained_literal_hashes))
        complete = (
            report["coverage_complete"]
            and report["live_coverage_complete"]
            and report["cancellation_valid"]
            and parent["code"]["frozen_committed"]
            and not usage_failures
            and not request_cap_breached
            and not (
                report["unresolved_samples"]
                or report["fault_outcome_failures"]
                or report["preparation_outcome_failures"]
            )
        )
        if not complete:
            incomplete.append(dataset)
        if (
            report["critical_failures"]
            or report["required_retention_failures"]
            or report["generation_retention_failures"]
            or not report["deterministic_preparation"]
        ):
            quality.append(dataset)
        improvement = improvement or bool(report["one_source_improvement_samples"])
        rows[dataset] = {
            "original_manifest_hash": digest(parent),
            "audited_manifest_hash": digest(manifest),
            "preparation_manifest_hash": digest(prepared[dataset][1]),
            "original_artifact_hashes_sha256": file_hash(parent_path / "artifact-hashes.json"),
            "audited_artifact_hashes_sha256": file_hash(path / "artifact-hashes.json"),
            "review_hash": review_hash,
            "live_coverage_complete": report["live_coverage_complete"],
            "evidence_complete": bool(complete),
            "usage_failures": usage_failures,
            "request_cap_breached": request_cap_breached,
            "required_retention_failures": report["required_retention_failures"],
            "generation_retention_failures": report["generation_retention_failures"],
            "critical_failures": report["critical_failures"],
            "unresolved_samples": report["unresolved_samples"],
            "candidate_generated_eligible_claims": eligible_retained,
            "candidate_eligible_required_claims": eligible_required,
            "candidate_generated_eligible_literals": literal_retained,
            "candidate_eligible_required_literals": literal_required,
            "generation_comparisons": [
                _generation_comparison(suite, samples, arm) for arm in ("bounded-fragments-v1", "bounded-fragments-v2")
            ],
            "arms": report["arms"],
        }
    if incomplete:
        decision = "insufficient_evidence"
    elif not offline["offline_preparation_passed"] or quality or not improvement:
        decision = "no_production_change"
    else:
        decision = "bounded_projection_follow_up_required"
    return {
        "schema": "evidence-admission.combined-live.v1",
        "decision": decision,
        "live_policy_sha256": base.frozen_hashes["live_policy"],
        "required_datasets": policy["required_datasets"],
        "provider": provider_identity,
        "offline_preparation_passed": offline["offline_preparation_passed"],
        "incomplete_datasets": incomplete,
        "quality_failure_datasets": quality,
        "one_source_improvement": improvement,
        "runs": rows,
        "limitations": [
            "synthetic_sources_not_real_host_observations",
            "fragment_references_evaluation_only",
            "usage_contains_reported_returned_calls_only",
            "local_cancellation_not_remote_cancellation",
            "follow_up_requires_separate_production_design",
        ],
    }
