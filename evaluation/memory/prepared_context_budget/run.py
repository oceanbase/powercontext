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

"""Run the offline prepared-context budget comparison.

    uv run --locked python evaluation/memory/prepared_context_budget/run.py \\
        --manifest evaluation/memory/prepared_context_budget/fixtures/budget-v1.json \\
        --output .powercontext/task-2520/evaluation

The run uses no network and no model. It exits 0 for a valid comparison, whether or not an improvement is
established, and 1 when any validity check fails.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[3]
if __package__ in {None, ""}:
    sys.path.insert(0, str(_REPO_ROOT))

from evaluation.memory.prepared_context_budget.experiment import decide, evaluate_case  # noqa: E402
from evaluation.memory.prepared_context_budget.manifest import load_manifest  # noqa: E402
from evaluation.memory.prepared_context_budget.report import (  # noqa: E402
    markdown,
    report_payload,
    run_manifest,
    write_reports,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the offline prepared-context budget comparison.")
    parser.add_argument("--manifest", type=Path, required=True, help="versioned fixture manifest")
    parser.add_argument(
        "--output", type=Path, required=True, help="directory for report.json, report.md, run-manifest.json"
    )
    arguments = parser.parse_args(argv)

    manifest = load_manifest(arguments.manifest)
    results = [evaluate_case(manifest, case) for case in manifest.cases]
    decision = decide(manifest, results)
    command = ["run.py", *(sys.argv[1:] if argv is None else argv)]
    write_reports(
        arguments.output,
        report_payload(manifest, results, decision),
        markdown(manifest, results, decision),
        run_manifest(manifest, _REPO_ROOT, command),
    )
    for result in results:
        status = "ok" if not result.failed_checks else "FAILED " + ",".join(result.failed_checks)
        print(
            f"{result.case_id}: baseline={result.baseline_coverage:.4f} candidate={result.candidate_coverage:.4f} "
            f"delta={result.coverage_delta:+.4f} top_loss={result.top_item_truncation_loss:.4f} checks={status}"
        )
    print(f"manifest_sha256={manifest.sha256}")
    print(f"decision={decision.outcome} mean_coverage_delta={decision.mean_coverage_delta:.6f}")
    for reason in decision.reasons:
        print(f"reason: {reason}")
    print(f"reports: {arguments.output}")
    return 1 if decision.outcome == "invalid" else 0


if __name__ == "__main__":
    raise SystemExit(main())
