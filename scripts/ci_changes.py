#!/usr/bin/env python3
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

"""Select checks from complete PR changes or unverified branch changes."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tomllib
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]


def select_suites(paths: list[str], rules: dict[str, Any]) -> dict[str, bool]:
    selected = dict.fromkeys(rules["suites"], False)
    for path in paths:
        if any(fnmatchcase(path, pattern) for pattern in rules["full"]):
            return dict.fromkeys(selected, True)
        matches = {
            suite
            for suite, patterns in rules["suites"].items()
            if any(fnmatchcase(path, pattern) for pattern in patterns)
        }
        for group in rules["shared"].values():
            if any(fnmatchcase(path, pattern) for pattern in group["patterns"]):
                matches.update(group["suites"])
        if not matches and not any(fnmatchcase(path, pattern) for pattern in rules["ignore"]):
            # New directories must not silently bypass checks before they have an owner.
            return dict.fromkeys(selected, True)
        for suite in matches:
            selected[suite] = True
    return selected


def test_matrix(event: str, paths: list[str], rules: dict[str, Any], *, full: bool = False) -> dict[str, Any]:
    """Retain full compatibility for dependencies, CI policy and unclassified inputs."""
    shared = any(fnmatchcase(path, pattern) for path in paths for pattern in rules["full"])
    dependencies = any(path in {"pyproject.toml", "uv.lock"} for path in paths)
    unknown = any(all(select_suites([path], rules).values()) for path in paths)
    if event != "pull_request" or full or shared or dependencies or unknown:
        return {"python-version": ["3.11", "3.12", "3.13", "3.14"], "suite": ["unit", "e2e"]}
    return {
        "include": [
            {"python-version": "3.11", "suite": "unit"},
            {"python-version": "3.14", "suite": "unit"},
            {"python-version": "3.12", "suite": "e2e"},
        ]
    }


def changed_paths(base: str, head: str, *, pull_request: bool = True) -> list[str]:
    if not all(re.fullmatch(r"[0-9a-fA-F]{40}", commit) for commit in (base, head)):
        message = "Expected full base and head commit SHAs"
        raise ValueError(message)
    if not pull_request:
        # A rewritten branch must not silently drop changes from the old baseline.
        subprocess.run(["git", "merge-base", "--is-ancestor", base, head], check=True, cwd=ROOT)  # noqa: S603, S607
    comparison = f"{base}...{head}" if pull_request else f"{base}..{head}"
    # Three-dot comparison covers the whole PR, excluding unrelated base changes.
    # Disabling rename detection includes both sides of moves across suite boundaries.
    result = subprocess.run(  # noqa: S603 -- validated SHAs, no shell
        ["git", "diff", "--name-only", "--no-renames", "-z", comparison, "--"],  # noqa: S607 -- runner tool
        check=True,
        capture_output=True,
        cwd=ROOT,
    )
    return [os.fsdecode(path) for path in result.stdout.split(b"\0") if path]


def successful_baseline(workflow: str, repository: str, branch: str, head: str) -> str:
    """Find a successful ancestor for this workflow, retaining cancelled changes."""
    if (
        not re.fullmatch(r"[A-Za-z0-9_-]+\.ya?ml", workflow)
        or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
        or not re.fullmatch(r"[0-9a-fA-F]{40}", head)
    ):
        raise ValueError("Expected a workflow filename and repository owner/name")  # noqa: TRY003
    query = urlencode({"branch": branch, "event": "push", "status": "success", "per_page": 100})
    result = subprocess.run(  # noqa: S603 -- validated API path, encoded query, no shell
        ["gh", "api", f"repos/{repository}/actions/workflows/{workflow}/runs?{query}"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        cwd=ROOT,
    )
    for run in json.loads(result.stdout)["workflow_runs"]:
        candidate = run["head_sha"]
        if not re.fullmatch(r"[0-9a-fA-F]{40}", candidate):
            continue
        ancestor = subprocess.run(  # noqa: S603 -- validated commit SHAs, no shell
            ["git", "merge-base", "--is-ancestor", candidate, head],  # noqa: S607
            capture_output=True,
            cwd=ROOT,
            check=False,
        )
        if ancestor.returncode == 0:
            return candidate
    raise ValueError("No successful workflow ancestor found")  # noqa: TRY003


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", default=os.environ.get("GITHUB_EVENT_NAME", ""))
    parser.add_argument("--base", default=os.environ.get("PR_BASE_SHA", ""))
    parser.add_argument("--head", default=os.environ.get("PR_HEAD_SHA") or os.environ.get("GITHUB_SHA", ""))
    parser.add_argument("--baseline-workflow", default=os.environ.get("CI_BASELINE_WORKFLOW", ""))
    parser.add_argument("--repository", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--branch", default=os.environ.get("GITHUB_REF_NAME", ""))
    parser.add_argument(
        "--full-validation", action="store_true", default=os.environ.get("CI_FULL_VALIDATION") == "true"
    )
    arguments = parser.parse_args()
    rules = tomllib.loads((ROOT / ".github/ci-paths.toml").read_text(encoding="utf-8"))
    reason = "Full validation requested by this event."
    paths: list[str] = []
    full = arguments.full_validation
    if arguments.event_name in {"pull_request", "push"}:
        try:
            base = arguments.base
            if arguments.event_name == "push" and not base:
                base = successful_baseline(
                    arguments.baseline_workflow, arguments.repository, arguments.branch, arguments.head
                )
            paths = changed_paths(base, arguments.head, pull_request=arguments.event_name == "pull_request")
            selected = select_suites(paths, rules)
            reason = f"Compared {base} to {arguments.head} ({len(paths)} changed paths)."
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            reason = "A trustworthy change baseline is unavailable; running every CI suite."
            print(f"::warning::{reason}")
            selected = dict.fromkeys(rules["suites"], True)
            full = True
    else:
        selected = dict.fromkeys(rules["suites"], True)
        if arguments.event_name == "schedule":
            # Archive generation and lock experiments have explicit manual entries.
            for suite in ("release_schema", "seekdb_lock"):
                selected[suite] = False

    matrix = test_matrix(arguments.event_name, paths, rules, full=full)
    if rules["checks"].keys() != selected.keys():
        raise ValueError("Every CI scope must declare its required result jobs")  # noqa: TRY003
    required = sorted(
        {"quality", "upgrade-release-gate"}
        | {job for suite, jobs in rules["checks"].items() if selected[suite] for job in jobs}
    )
    print(json.dumps(selected, indent=2))
    if output_path := os.environ.get("GITHUB_OUTPUT"):
        with Path(output_path).open("a", encoding="utf-8") as output:
            output.write(f"suites={json.dumps(selected)}\n")
            output.write(f"test-matrix={json.dumps(matrix)}\n")
            output.write(f"required-jobs={json.dumps(required)}\n")
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as summary:
            summary.write(f"## CI scope\n\n{reason}\n\n| Suite | Run |\n| --- | --- |\n")
            for suite, enabled in selected.items():
                summary.write(f"| {suite} | {'yes' if enabled else 'no'} |\n")
            summary.write(f"\nPython matrix: `{json.dumps(matrix)}`\n")


if __name__ == "__main__":
    main()
