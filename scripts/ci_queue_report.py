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

"""Read a bounded sample of Actions jobs without cancelling or changing any run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def api(path: str) -> Any:
    response = subprocess.run(  # noqa: S603 -- validated repository and encoded fixed API queries, no shell
        ["gh", "api", path],  # noqa: S607 -- standard authenticated GitHub CLI
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return json.loads(response.stdout)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="oceanbase/powercontext")
    parser.add_argument("--max-runs", type=int, default=40)
    parser.add_argument("--max-job-pages", type=int, default=3)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", arguments.repo):
        parser.error("--repo must be owner/name")
    if not 1 <= arguments.max_runs <= 100 or not 1 <= arguments.max_job_pages <= 3:
        parser.error("Use 1..100 runs and 1..3 job pages per run")
    root = f"repos/{arguments.repo}/actions"
    pending: dict[int, Any] = {}
    totals = {}
    for status in ("queued", "in_progress", "waiting", "pending"):
        data = api(f"{root}/runs?status={status}&per_page=100")
        totals[status] = data["total_count"]
        pending.update((run["id"], run) for run in data["workflow_runs"])
    # The queue bottleneck is visible in the oldest jobs. A capped sample is
    # explicit; unqueried workflows must never be reported as having no jobs.
    runs = sorted(pending.values(), key=lambda run: run["created_at"])[: arguments.max_runs]
    now = datetime.now(UTC)
    records = []
    counts: Counter[str] = Counter()
    incomplete = []
    for run in runs:
        jobs = []
        total = 0
        for page in range(1, arguments.max_job_pages + 1):
            data = api(f"{root}/runs/{run['id']}/jobs?filter=latest&per_page=100&page={page}")
            total = data["total_count"]
            jobs.extend(data["jobs"])
            if len(jobs) >= total:
                break
        if len(jobs) < total:
            incomplete.append(run["id"])
        states = Counter(job["status"] for job in jobs)
        counts.update(states)
        records.append({
            "id": run["id"],
            "workflow": run["name"],
            "status": run["status"],
            "event": run["event"],
            "head_sha": run["head_sha"],
            "branch": run["head_branch"],
            "url": run["html_url"],
            "created_at": run["created_at"],
            "workflow_age_minutes": round(
                (now - datetime.fromisoformat(run["created_at"].replace("Z", "+00:00"))).total_seconds() / 60, 1
            ),
            "job_counts": dict(states),
            "job_total": total,
            "unfinished_jobs": [
                {
                    "id": job["id"],
                    "name": job["name"],
                    "status": job["status"],
                    "labels": job["labels"],
                    "runner": job["runner_name"],
                    "url": job["html_url"],
                }
                for job in jobs
                if job["status"] != "completed"
            ],
        })
    report = {
        "captured_at": now.isoformat(),
        "repository": arguments.repo,
        "workflow_totals": totals,
        "sampled_workflows": len(runs),
        "workflow_sample_complete": len(runs) == len(pending) and all(value <= 100 for value in totals.values()),
        "job_samples_incomplete": incomplete,
        "sampled_job_counts": dict(counts),
        "runs": records,
    }
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Workflow totals: {totals}; sampled {len(runs)} workflows")
    print(f"Actual job states in sample: {dict(counts)}")
    if not report["workflow_sample_complete"] or incomplete:
        print("Partial sample: increase the run limit or query the remaining workflows separately.")
    for record in records:
        print(
            f"{record['id']} {record['workflow']}: {record['job_counts']} "
            f"(workflow age {record['workflow_age_minutes']} min; this is not job queue latency)"
        )


if __name__ == "__main__":
    main()
