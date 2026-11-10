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

"""Reports preserve failed/cancelled acceptance and exact-attempt evidence."""

# ruff: noqa: RUF001 -- Assertions use the report's Chinese punctuation.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import ci_nightly_report
import pytest
import yaml


@pytest.fixture
def metadata(monkeypatch):
    run = {
        "id": 42,
        "run_attempt": 2,
        "status": "completed",
        "name": "Main",
        "html_url": "https://github.com/example/repo/actions/runs/42",
        "head_sha": "a" * 40,
        "head_branch": "test-branch",
        "event": "schedule",
        "conclusion": "failure",
        "run_started_at": "2026-11-10T18:00:00Z",
        "updated_at": "2026-11-10T18:05:00Z",
    }
    jobs = [
        {
            "id": index,
            "name": f"backend ({index})",
            "status": "completed",
            "conclusion": "success",
            "html_url": f"https://github.com/example/repo/actions/runs/42/job/{index}",
            "started_at": "2026-11-10T18:01:00Z",
            "completed_at": "2026-11-10T18:05:00Z",
            "steps": [],
        }
        for index in range(101)
    ]
    jobs[100].update(
        name="OceanBase acceptance",
        conclusion="failure",
        steps=[{"name": "Run native regressions", "conclusion": "failure"}],
    )
    artifacts = [{"id": 7, "name": "native-evidence", "expired": True, "expires_at": "2026-11-11T18:00:00Z"}]

    def api(path, *, paginate=False):
        root = "repos/example/repo/actions/runs/42"
        if path == f"{root}/attempts/2":
            return run
        if path == f"{root}/attempts/2/jobs?per_page=100" and paginate:
            return [{"total_count": len(jobs), "jobs": jobs[:100]}, {"total_count": len(jobs), "jobs": jobs[100:]}]
        if path == f"{root}/artifacts?per_page=100" and paginate:
            return [{"total_count": len(artifacts), "artifacts": artifacts}]
        pytest.fail(f"Unexpected API request: {path}")

    monkeypatch.setattr(ci_nightly_report, "api", api)
    return run, jobs, artifacts


@pytest.mark.parametrize("conclusion", ["success", "failure", "cancelled"])
@pytest.mark.parametrize("event", ["schedule", "workflow_dispatch"])
def test_report_retains_source_conclusion_and_all_attempt_jobs(metadata, conclusion, event):
    run, jobs, _artifacts = metadata
    run.update(conclusion=conclusion, event=event)
    jobs[0]["conclusion"] = "skipped"
    jobs[1]["conclusion"] = "cancelled"
    report = ci_nightly_report.collect("example/repo", 42, 2)
    rendered = ci_nightly_report.render(report)
    assert report["errors"] == []
    assert len(report["jobs"]) == 101
    assert f"Main 结论：{conclusion}" in rendered
    markdown_event = event.replace("_", "\\_")
    assert f"触发方式：{markdown_event}" in rendered
    assert "2026-11-11 02:00:00" in rendered
    assert "a" * 40 in rendered
    assert "已获取 101 个任务" in rendered
    assert "OceanBase acceptance" in rendered  # Failure on page two must not disappear.
    assert "Run native regressions" in rendered
    assert "| skipped | 1 |" in rendered
    assert "| cancelled | 1 |" in rendered
    assert "/attempts/2)" in rendered
    assert "00:04:00" in rendered
    assert "已过期" in rendered
    assert "/artifacts/7)" not in rendered


def test_api_failure_saves_incomplete_report_and_exits_nonzero(tmp_path, monkeypatch, metadata):
    original = ci_nightly_report.api

    def unavailable_jobs(path, *, paginate=False):
        if "/jobs?" in path:
            raise subprocess.CalledProcessError(1, ["gh", "api"])
        return original(path, paginate=paginate)

    monkeypatch.setattr(ci_nightly_report, "api", unavailable_jobs)
    monkeypatch.setattr(
        sys, "argv", ["report", "--repo", "example/repo", "--run-id", "42", "--attempt", "2", "--output", str(tmp_path)]
    )
    assert ci_nightly_report.main() == 1
    rendered = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "Main 结论：failure" in rendered
    assert "报告数据：不完整" in rendered
    assert "没有可列出的任务" in rendered
    assert json.loads((tmp_path / "report.json").read_text())["errors"]


def test_incomplete_pagination_does_not_certify_a_partial_report(monkeypatch, metadata):
    original = ci_nightly_report.api

    def truncated(path, *, paginate=False):
        data = original(path, paginate=paginate)
        return data[:1] if "/jobs?" in path else data

    monkeypatch.setattr(ci_nightly_report, "api", truncated)
    report = ci_nightly_report.collect("example/repo", 42, 2)
    assert report["errors"]
    assert "报告数据：不完整" in ci_nightly_report.render(report)


@pytest.mark.parametrize("field,value", [("run_attempt", 3), ("status", "in_progress"), ("event", "pull_request")])
def test_report_rejects_the_wrong_source_attempt(metadata, field, value):
    metadata[0][field] = value
    report = ci_nightly_report.collect("example/repo", 42, 2)
    assert report["errors"]
    assert "Main 结论：unknown" in ci_nightly_report.render(report)


@pytest.mark.parametrize(
    "event,expected", [("schedule", True), ("workflow_dispatch", True), ("pull_request", False), ("push", False)]
)
def test_report_workflow_accepts_daily_and_manual_completions(event, expected):
    root = Path(__file__).parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/nightly-report.yml").read_text())
    assert workflow[True]["workflow_run"] == {"workflows": ["Main"], "types": ["completed"]}
    job = workflow["jobs"]["report"]
    expression = job["if"].removeprefix("${{").removesuffix("}}")
    for conclusion in ("success", "failure", "cancelled"):
        result = eval(  # noqa: S307 -- trusted checked-in expression, fixed event metadata and no builtins
            expression,
            {"__builtins__": {}},
            {
                "fromJSON": json.loads,
                "contains": lambda values, item: item in values,
                "github": SimpleNamespace(
                    event=SimpleNamespace(workflow_run=SimpleNamespace(event=event, conclusion=conclusion))
                ),
            },
        )
        assert result is expected
    assert workflow["permissions"] == {"actions": "read", "contents": "read"}
    checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
    assert "ref" not in checkout["with"]  # Never execute the manually selected branch's report code.
