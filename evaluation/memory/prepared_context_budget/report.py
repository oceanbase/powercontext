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

"""Deterministic JSON and Markdown reports plus a run manifest that pins the inputs and code."""

from __future__ import annotations

import hashlib
import json
import platform
import subprocess
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .experiment import CaseResult, Decision, sensitivity
from .manifest import Manifest

REPORT_SCHEMA_VERSION = "powercontext.prepared-context-budget-report.v1"
_PATH_OPTIONS = ("--manifest", "--output")
_SOURCES = (
    "src/powercontext/builtin/runtime/prepared_context.py",
    "src/powercontext/builtin/runtime/prepared_text.py",
    "evaluation/memory/prepared_context_budget/arms.py",
    "evaluation/memory/prepared_context_budget/experiment.py",
    "evaluation/memory/prepared_context_budget/manifest.py",
    "evaluation/memory/prepared_context_budget/report.py",
    "evaluation/memory/prepared_context_budget/run.py",
)


def report_payload(manifest: Manifest, results: Sequence[CaseResult], decision: Decision) -> dict[str, Any]:
    """The full machine-readable report. It holds no timestamps, so a replay produces identical bytes."""

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "manifest": {
            "schema_version": manifest.schema_version,
            "dataset_version": manifest.dataset_version,
            "contract_version": manifest.contract_version,
            "sha256": manifest.sha256,
            "definitions_sha256": manifest.definitions_sha256,
        },
        "arms": manifest.arms,
        "decision": asdict(decision),
        "sensitivity": sensitivity(results),
        "limitations": list(manifest.limitations),
        "cases": [
            {
                **{key: value for key, value in asdict(result).items() if key != "items"},
                "baseline_dispositions": _dispositions(result, "baseline"),
                "candidate_dispositions": _dispositions(result, "candidate"),
                "items": [asdict(item) for item in result.items],
            }
            for result in results
        ],
    }


def run_manifest(manifest: Manifest, repo_root: Path, command: Sequence[str]) -> dict[str, Any]:
    """Pin the run: manifest hash, code revision, source hashes and environment."""

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "command": _relative_arguments(command, repo_root),
        "manifest_path": _relative(manifest.path, repo_root),
        "manifest_sha256": manifest.sha256,
        "definitions_path": _relative(manifest.definitions_path, repo_root),
        "definitions_sha256": manifest.definitions_sha256,
        "git_head": _git(repo_root, "rev-parse", "HEAD"),
        "git_worktree_dirty": bool(_git(repo_root, "status", "--porcelain", "--", "src", "evaluation/memory")),
        "source_sha256": {source: hashlib.sha256((repo_root / source).read_bytes()).hexdigest() for source in _SOURCES},
        "python": platform.python_version(),
        "platform": platform.platform(),
    }


def markdown(manifest: Manifest, results: Sequence[CaseResult], decision: Decision) -> str:
    lines = [
        "# Prepared-context budget allocation report",
        "",
        f"- Manifest: `{manifest.dataset_version}` (schema `{manifest.schema_version}`, "
        f"contract `{manifest.contract_version}`)",
        f"- Manifest SHA-256: `{manifest.sha256}`",
        f"- Definitions SHA-256: `{manifest.definitions_sha256}`",
        "- Arms: baseline = production Builder; candidate = evaluation-only `waterfill-v1`",
        "",
        "## Decision",
        "",
        f"**{decision.outcome}**",
        "",
        f"- Primary cases: {decision.primary_cases}",
        f"- Mean coverage delta: {decision.mean_coverage_delta:.6f} "
        f"(must be strictly greater than {decision.min_mean_coverage_delta})",
        f"- Top-item truncation loss above {decision.max_top_item_truncation_loss}: "
        f"{_listing(decision.top_item_loss_failures)}",
        f"- Critical fact regressions: {_listing(decision.critical_fact_regressions)}",
        f"- Failed validity checks: {_listing(decision.invalid_checks)}",
        *(f"- Reason: {reason}" for reason in decision.reasons),
        "",
        "## Cases",
        "",
        "| Case | Role | Path | max_bytes | Baseline bytes | Candidate bytes | Baseline coverage "
        "| Candidate coverage | Delta | Top-item loss | Facts (declared / baseline / candidate) | Fact regressions |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | ---: |",
        *(_case_row(result) for result in results),
        "",
        "## Sensitivity",
        "",
        "Reordering cases are preregistered, reported here and excluded from the decision.",
        "",
        "| Case | Reorders | Reference delta | Reordered delta | Reference top-item loss | Reordered top-item loss |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
        *(
            f"| `{row['case_id']}` | `{row['reorders']}` | {row['reference_coverage_delta']:.4f} "
            f"| {row['reordered_coverage_delta']:.4f} | {row['reference_top_item_truncation_loss']:.4f} "
            f"| {row['reordered_top_item_truncation_loss']:.4f} |"
            for row in sensitivity(results)
        ),
        "",
        "## Limitations",
        "",
        *(f"- {limitation}" for limitation in manifest.limitations),
        "",
        "## Items",
        "",
        "Bytes are UTF-8 body bytes; `display` is the complete display body, `delivered` includes the ellipsis.",
    ]
    for result in results:
        lines.extend(_item_table(result))
    return "\n".join(lines) + "\n"


def write_reports(output: Path, payload: dict[str, Any], report_md: str, pinned: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "report.json").write_text(_json(payload), encoding="utf-8")
    (output / "report.md").write_text(report_md, encoding="utf-8")
    (output / "run-manifest.json").write_text(_json(pinned), encoding="utf-8")


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"


def _dispositions(result: CaseResult, arm: str) -> dict[str, int]:
    counts = Counter(getattr(item, f"{arm}_disposition") for item in result.items)
    return dict(sorted(counts.items()))


def _case_row(result: CaseResult) -> str:
    role = f"{result.role}{' (critical)' if result.critical else ''}"
    return (
        f"| `{result.case_id}` | {role} | {result.path} | {result.max_bytes} | {result.baseline_content_bytes} "
        f"| {result.candidate_content_bytes} | {result.baseline_coverage:.4f} | {result.candidate_coverage:.4f} "
        f"| {result.coverage_delta:+.4f} | {result.top_item_truncation_loss:.4f} "
        f"| {result.facts_declared} / {result.baseline_facts_retained} / {result.candidate_facts_retained} "
        f"| {len(result.fact_regressions)} |"
    )


def _item_table(result: CaseResult) -> list[str]:
    return [
        "",
        f"### `{result.case_id}`",
        "",
        f"Inputs SHA-256: `{result.input_sha256}`",
        "",
        "| Pos | Kind | Artifact | Rank | Display | Baseline | Delivered | Retained | Candidate | Delivered | Retained |",
        "| ---: | --- | --- | ---: | ---: | --- | ---: | ---: | --- | ---: | ---: |",
        *(
            f"| {item.position} | {item.kind} | `{item.artifact_id}` | {item.rank} | {item.display_bytes} "
            f"| {item.baseline_disposition} | {item.baseline_delivered_bytes} "
            f"| {item.baseline_retained_fraction:.3f} | {item.candidate_disposition} "
            f"| {item.candidate_delivered_bytes} | {item.candidate_retained_fraction:.3f} |"
            for item in result.items
        ),
    ]


def _listing(values: Sequence[str]) -> str:
    return "none" if not values else "; ".join(f"`{value}`" for value in values)


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _relative_arguments(command: Sequence[str], repo_root: Path) -> list[str]:
    """Record path arguments the way the other manifest paths are recorded, so replay is repository-relative."""

    arguments: list[str] = []
    pending = False
    for argument in command:
        if pending:
            arguments.append(_recorded_path(argument, repo_root))
            pending = False
        elif argument in _PATH_OPTIONS:
            arguments.append(argument)
            pending = True
        elif "=" in argument and argument.split("=", 1)[0] in _PATH_OPTIONS:
            option, value = argument.split("=", 1)
            arguments.append(f"{option}={_recorded_path(value, repo_root)}")
        else:
            arguments.append(argument)
    return arguments


def _recorded_path(value: str, repo_root: Path) -> str:
    # ``run.py`` resolves a relative argument against the invocation directory, so resolve it the same way before
    # making it repository-relative; resolving against ``repo_root`` would record a different file than the one read.
    return _relative(Path(value).resolve(), repo_root)


def _git(repo_root: Path, *arguments: str) -> str | None:
    try:
        return subprocess.check_output(  # noqa: S603
            ["git", *arguments],  # noqa: S607
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return None
