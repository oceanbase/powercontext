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

"""Build content-free reports from private case observations."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from hashlib import sha256
from pathlib import Path
from typing import Any

from .manifest import LoadedManifest
from .models import REPORT_SCHEMA, CaseObservation, CaseScore, LiveRunManifest
from .scoring import score_case


def build_report(
    loaded: LoadedManifest,
    observations: Mapping[str, CaseObservation],
    run_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    """Aggregate scores without copying case IDs, evidence, or generated text."""

    public_manifest = LiveRunManifest.model_validate(run_manifest).model_dump(mode="json")
    _validate_dataset_identity(loaded, public_manifest)
    expected_ids = {case.case_id for case in loaded.manifest.cases}
    unknown_ids = set(observations) - expected_ids
    if unknown_ids:
        raise ValueError("observations contain case IDs outside the loaded manifest")  # noqa: TRY003

    scores: list[CaseScore] = []
    for case in loaded.manifest.cases:
        observation = observations.get(case.case_id)
        if observation is None:
            observation = CaseObservation(case_id=case.case_id, status="failed", error_class="missing_observation")
        scores.append(score_case(case, observation))

    failures = Counter(failure for score in scores for failure in score.failure_classes)
    generation_errors = failures["generation_error"]
    overall_passed = sum(score.passed for score in scores)
    retention = [score for score in scores if score.factual_retention_applicable]
    retention_observed = [score for score in retention if "generation_error" not in score.failure_classes]
    leakage_observed = [score for score in scores if "generation_error" not in score.failure_classes]
    report_status = "passed" if overall_passed == len(scores) else "incomplete" if generation_errors else "failed"
    return {
        "schema_version": REPORT_SCHEMA,
        "status": report_status,
        "run_id": public_manifest["run_id"],
        "run_manifest_sha256": _mapping_sha256(public_manifest),
        "dataset": {
            "schema_version": loaded.manifest.schema_version,
            "dataset_version": loaded.manifest.dataset_version,
            "manifest_sha256": loaded.sha256,
            "case_count": len(scores),
        },
        "prompts": public_manifest["prompts"],
        "provider": public_manifest["provider"],
        "overall": {
            "passed": overall_passed,
            "failed": len(scores) - overall_passed,
            "total": len(scores),
        },
        "factual_retention": {
            "passed": sum(score.factual_retention_passed for score in retention_observed),
            "failed": sum(not score.factual_retention_passed for score in retention_observed),
            "unobserved": len(retention) - len(retention_observed),
            "total": len(retention),
            "false_rejections": failures["false_rejection"],
        },
        "instruction_leakage": {
            "passed": sum(score.instruction_leakage_passed for score in leakage_observed),
            "leaks": failures["instruction_leakage"],
            "unobserved": len(scores) - len(leakage_observed),
            "total": len(scores),
        },
        "dispositions": {
            disposition: _disposition_counts(scores, disposition)
            for disposition in ("atomic_memory", "experience_or_skill", "reject")
        },
        "failure_classes": dict(sorted(failures.items())),
        "content_policy": "aggregate_only_no_evidence_or_generated_candidate_text",
    }


def render_markdown(report: Mapping[str, Any]) -> str:
    """Render a compact review artifact from an already content-free report."""

    overall = _mapping(report, "overall")
    retention = _mapping(report, "factual_retention")
    leakage = _mapping(report, "instruction_leakage")
    dataset = _mapping(report, "dataset")
    prompts = _mapping(report, "prompts")
    provider = _mapping(report, "provider")
    failures = _mapping(report, "failure_classes")
    lines = [
        "# Atomic Memory extraction live-run report",
        "",
        f"- Status: `{report.get('status')}`",
        f"- Run: `{report.get('run_id')}`",
        f"- Dataset: `{dataset.get('dataset_version')}` (`{dataset.get('manifest_sha256')}`)",
        f"- Provider/model: `{provider.get('provider')}` / `{provider.get('model')}`",
        f"- Overall: {overall.get('passed')}/{overall.get('total')} passed",
        "",
        "## Quality gates",
        "",
        f"- Factual retention: {retention.get('passed')}/{retention.get('total')} passed; "
        f"false rejections: {retention.get('false_rejections')}",
        f"- Instruction leakage: {leakage.get('leaks')} leaks across {leakage.get('total')} cases; "
        f"unobserved: {leakage.get('unobserved')}",
        "",
        "## Prompt identities",
        "",
    ]
    for profile, identity in sorted(prompts.items()):
        if isinstance(identity, Mapping):
            lines.append(
                f"- `{profile}`: `{identity.get('builtin_version')}` / `{identity.get('instructions_sha256')}`"
            )
    lines.extend(["", "## Failure classes", ""])
    if failures:
        lines.extend(f"- `{name}`: {count}" for name, count in sorted(failures.items()))
    else:
        lines.append("- None")
    lines.extend([
        "",
        "This report contains aggregate counts and run identities only. Evidence and generated candidate text remain "
        "in the ignored private run artifact.",
        "",
    ])
    return "\n".join(lines)


def write_report(directory: Path, report: Mapping[str, Any]) -> tuple[Path, Path]:
    """Write JSON and Markdown summaries beside the private observations."""

    json_path = directory / "report.json"
    markdown_path = directory / "report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, markdown_path


def load_observations(path: Path) -> dict[str, CaseObservation]:
    """Load one private JSONL artifact and reject duplicate case results."""

    observations: dict[str, CaseObservation] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"cannot read private observations: {path}") from error  # noqa: TRY003
    for line in lines:
        if not line.strip():
            continue
        observation = CaseObservation.model_validate_json(line)
        if observation.case_id in observations:
            raise ValueError("private observations contain duplicate case IDs")  # noqa: TRY003
        observations[observation.case_id] = observation
    return observations


def _disposition_counts(scores: Sequence[CaseScore], disposition: str) -> dict[str, int]:
    selected = [score for score in scores if score.disposition == disposition]
    passed = sum(score.passed for score in selected)
    return {"passed": passed, "failed": len(selected) - passed, "total": len(selected)}


def _mapping_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _validate_dataset_identity(loaded: LoadedManifest, run_manifest: Mapping[str, Any]) -> None:
    dataset = _mapping(run_manifest, "dataset")
    expected = {
        "schema_version": loaded.manifest.schema_version,
        "dataset_version": loaded.manifest.dataset_version,
        "manifest_sha256": loaded.sha256,
        "case_count": len(loaded.manifest.cases),
    }
    if dict(dataset) != expected:
        raise ValueError("run manifest dataset identity does not match the scoring manifest")  # noqa: TRY003


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    selected = value.get(key)
    return selected if isinstance(selected, Mapping) else {}


__all__ = ["build_report", "load_observations", "render_markdown", "write_report"]
