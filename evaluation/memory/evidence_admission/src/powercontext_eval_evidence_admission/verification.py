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

"""Bind all required datasets to one conservative offline preparation decision."""

from __future__ import annotations

import re
from pathlib import Path

from .manifests import EvaluationError, Suite, file_hash, load_suite
from .models import digest
from .report import aggregate, code_identity, load_run


def verified_runs(base: Suite, run_dirs: tuple[Path, ...], modes: set[str]) -> dict:
    policy = base.experiment.v3
    if policy is None or base.private:
        raise EvaluationError("combined_comparison_requires_public_v3_suite")
    runs = {}
    for path in run_dirs:
        manifest, samples, stored = load_run(path)
        dataset = manifest.get("dataset")
        if dataset not in policy.required_datasets or dataset in runs:
            raise EvaluationError("unexpected_or_duplicate_dataset")
        suite = load_suite(base.root, experiment_version=base.version, dataset=dataset)
        current = code_identity(suite)
        keys = (
            "suite_code_hash",
            "runtime_code_hash",
            "frozen_hashes",
            "instruction_hash",
            "estimator",
            "dependency_hashes",
            "versions",
        )
        if any(current[key] != manifest["code"][key] for key in keys):
            raise EvaluationError("run_code_or_configuration_mismatch")
        if manifest["mode"] not in modes or manifest.get("private"):
            raise EvaluationError("unexpected_run_mode")
        if (
            base.experiment.citation_protocol
            and manifest["mode"] != "prepare"
            and any(
                not re.fullmatch(r"[0-9a-f]{64}", manifest.get(key, ""))
                for key in ("smoke_manifest_hash", "audited_smoke_manifest_hash")
            )
        ):
            raise EvaluationError("missing_smoke_proof")
        if manifest["experiment"] != suite.experiment.model_dump(mode="json", exclude_none=True):
            raise EvaluationError("run_experiment_mismatch")
        repetitions = (
            suite.experiment.repetitions if manifest["mode"] == "prepare" else suite.experiment.live_repetitions
        )
        if manifest["repetitions"] != repetitions:
            raise EvaluationError("run_repetition_mismatch")
        if manifest["mode"] == "prepare" and (
            manifest.get("provider") is not None or any(s.requests or s.generation for s in samples)
        ):
            raise EvaluationError("invalid_preparation_run")
        if manifest["mode"] != "prepare":
            provider = manifest.get("provider")
            if not isinstance(provider, dict) or set(provider) != {
                "provider",
                "model",
                "endpoint_hash",
                "max_requests",
            }:
                raise EvaluationError("invalid_provider_identity")
            if (
                provider["provider"] not in {"openai-compatible", "anthropic"}
                or not isinstance(provider["model"], str)
                or not provider["model"].strip()
                or not isinstance(provider["endpoint_hash"], str)
                or len(provider["endpoint_hash"]) != 64
                or not isinstance(provider["max_requests"], int)
                or isinstance(provider["max_requests"], bool)
                or provider["max_requests"] < 1
            ):
                raise EvaluationError("invalid_provider_identity")
        report = aggregate(suite, samples, manifest)
        if report != stored:
            raise EvaluationError("stored_report_mismatch")
        runs[dataset] = (suite, manifest, samples, report, path)
    if set(runs) != set(policy.required_datasets):
        raise EvaluationError("missing_required_dataset")
    return runs


def combined_preparation(base: Suite, run_dirs: tuple[Path, ...]) -> dict:
    policy = base.experiment.v3
    if policy is None or base.private:
        raise EvaluationError("combined_comparison_requires_public_v3_suite")
    runs = {}
    for dataset, (_, manifest, _, report, path) in verified_runs(base, run_dirs, {"prepare"}).items():
        comparisons = report["baseline_comparisons"]
        passed = (
            report["coverage_complete"]
            and report["deterministic_preparation"]
            and report["cancellation_valid"]
            and not (
                report["required_retention_failures"]
                or report["critical_failures"]
                or report["preparation_outcome_failures"]
                or report["fault_outcome_failures"]
                or report["unresolved_samples"]
            )
            and len(comparisons) == 2
            and all(p["no_observed_regression"] for p in comparisons)
        )
        runs[dataset] = {
            "run_manifest_sha256": file_hash(path / "run-manifest.json"),
            "artifact_hashes_sha256": file_hash(path / "artifact-hashes.json"),
            "offline_preparation_passed": bool(passed),
            "candidate": report["arms"][base.experiment.candidate_arm],
            "baseline_comparisons": comparisons,
            "required_retention_failures": report["required_retention_failures"],
            "critical_failures": report["critical_failures"],
            "preparation_outcome_failures": report["preparation_outcome_failures"],
            "coverage_complete": report["coverage_complete"],
            "decision": report["decision"],
        }
    if set(runs) != set(policy.required_datasets):
        raise EvaluationError("missing_required_dataset")
    return {
        "schema": "evidence-admission.combined-preparation.v3",
        "experiment_sha256": base.frozen_hashes["experiment"],
        "required_datasets": list(policy.required_datasets),
        "offline_preparation_passed": all(row["offline_preparation_passed"] for row in runs.values()),
        "decision": "insufficient_evidence",
        "provider_requests": 0,
        "runs": {name: runs[name] for name in policy.required_datasets},
        "run_set_hash": digest({name: runs[name]["run_manifest_sha256"] for name in policy.required_datasets}),
        "limitations": [
            "offline_preparation_only",
            "synthetic_development_stress_not_blinded",
            "live_quality_attribution_and_usage_not_run",
            "uncommitted_freeze_not_production_authorization",
        ],
    }
