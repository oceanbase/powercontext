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

"""Report a completed Main attempt using read-only GitHub Actions metadata."""

# ruff: noqa: RUF001 -- The human-readable report uses Chinese punctuation.

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

BEIJING = timezone(timedelta(hours=8))


def api(path: str, *, paginate: bool = False) -> Any:
    command = ["gh", "api", path]
    if paginate:
        command.extend(["--paginate", "--slurp"])
    response = subprocess.run(  # noqa: S603 -- validated repository and numeric IDs, no shell
        command, check=True, capture_output=True, text=True, timeout=120
    )
    return json.loads(response.stdout)


def records(path: str, key: str) -> list[dict[str, Any]]:
    pages = api(f"{path}?per_page=100", paginate=True)
    rows = [row for page in pages for row in page[key]]
    if not pages or len(rows) != pages[0]["total_count"] or len({row["id"] for row in rows}) != len(rows):
        message = f"Incomplete {key} pagination"
        raise ValueError(message)
    return rows


def collect(repository: str, run_id: int, attempt: int) -> dict[str, Any]:
    root = f"repos/{repository}/actions/runs/{run_id}"
    report: dict[str, Any] = {
        "repository": repository,
        "run_id": run_id,
        "attempt": attempt,
        "run": {},
        "jobs": [],
        "artifacts": [],
        "errors": [],
    }
    collecting = "source attempt"
    try:
        run = api(f"{root}/attempts/{attempt}")
        if run["id"] != run_id or run["run_attempt"] != attempt or run["status"] != "completed":
            report["errors"].append("The requested completed attempt was not returned.")
            return report
        if run["name"] != "Main" or run["event"] not in {"schedule", "workflow_dispatch"}:
            report["errors"].append("Expected scheduled or manual Main validation.")
            return report
        report["run"] = {
            key: run[key]
            for key in ("html_url", "head_sha", "head_branch", "event", "conclusion", "run_started_at", "updated_at")
        }
        collecting = "attempt jobs"
        report["jobs"] = records(f"{root}/attempts/{attempt}/jobs", "jobs")
        # The artifacts endpoint is scoped to a run, not an attempt. Keep this
        # inventory separate from the attempt-specific job results.
        collecting = "run artifacts"
        report["artifacts"] = records(f"{root}/artifacts", "artifacts")
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError) as error:
        report["errors"].append(f"Could not collect {collecting} from GitHub ({type(error).__name__}).")
    return report


def cell(value: Any) -> str:
    text = html.escape(str(value))
    return re.sub(r"([\\`*_|\[\]])", r"\\\1", text).replace("\n", " ").replace("\r", " ")


def beijing_time(value: str | None) -> str:
    if not value:
        return "—"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(BEIJING).strftime("%Y-%m-%d %H:%M:%S")


def duration(start: str | None, end: str | None) -> str:
    if not start or not end:
        return "—"
    seconds = max(
        0,
        int(
            (
                datetime.fromisoformat(end.replace("Z", "+00:00"))
                - datetime.fromisoformat(start.replace("Z", "+00:00"))
            ).total_seconds()
        ),
    )
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def render(report: dict[str, Any]) -> str:
    run = report["run"]
    jobs = report["jobs"]
    errors = report["errors"]
    conclusion = run.get("conclusion") or "unknown"
    lines = [
        "# CI 验收报告",
        "",
        f"- 仓库：{cell(report['repository'])}",
        f"- 源运行：{report['run_id']}，第 {report['attempt']} 次执行",
        f"- **Main 结论：{cell(conclusion)}**",
        f"- **报告数据：{'不完整，请查看报告任务日志' if errors else '完整'}**",
    ]
    if run:
        url = run["html_url"]
        lines.extend([
            f"- [打开源运行和日志]({url}/attempts/{report['attempt']})",
            f"- 触发方式：{cell(run['event'])}；分支：{cell(run['head_branch'])}",
            f"- 验证提交：`{run['head_sha']}`",
            f"- 开始时间（北京时间）：{beijing_time(run['run_started_at'])}",
            f"- 最近更新时间（北京时间）：{beijing_time(run['updated_at'])}",
        ])
    lines.extend([
        "",
        "报告任务成功只表示报告生成成功；验收结论以上方 Main 结果为准。",
        "任务数量不是测试用例数量，跳过或取消不代表验收通过。重跑时仅列出本次执行的任务。",
    ])
    if errors:
        lines.extend(["", "## 数据缺口", "", *[f"- {cell(error)}" for error in errors]])
    counts = Counter(job.get("conclusion") or job["status"] for job in jobs)
    lines.extend(["", "## 任务结果", "", f"已获取 {len(jobs)} 个任务。", "", "| 结果 | 任务数 |", "| --- | ---: |"])
    lines.extend(f"| {cell(result)} | {count} |" for result, count in sorted(counts.items()))
    lines.extend([
        "",
        "| 任务 / 日志 | 结果 | 执行耗时（时:分:秒） |",
        "| --- | --- | ---: |",
    ])
    for job in sorted(jobs, key=lambda item: (item["name"], item["id"])):
        result = job.get("conclusion") or job["status"]
        elapsed = "—" if result == "skipped" else duration(job.get("started_at"), job.get("completed_at"))
        lines.append(f"| [{cell(job['name'])}]({job['html_url']}) | {cell(result)} | {elapsed} |")
    if not jobs:
        lines.extend(["", "没有可列出的任务；请结合 Main 结论和报告数据完整性判断。"])
    lines.extend(["", "## 异常步骤", ""])
    failed_steps = [
        f"- [{cell(job['name'])} → {cell(step['name'])}]({job['html_url']})：{cell(step['conclusion'])}"
        for job in jobs
        for step in job.get("steps", [])
        if step.get("conclusion") not in {None, "success", "skipped", "neutral"}
    ]
    lines.extend(failed_steps or ["未获取到异常步骤记录；这不覆盖未启动、跳过或缺失的任务。"])
    lines.extend([
        "",
        "## 原始证据",
        "",
        "以下为源运行当前保留的 artifacts，可能包含其他执行次数的产物；不能据此认定本次验收通过。",
        "各产物按源任务设置过期，报告的 30 天保留期不会延长原始证据的保留期。",
        "",
        "| Artifact | 状态 | 过期时间（北京时间） |",
        "| --- | --- | --- |",
    ])
    for artifact in sorted(report["artifacts"], key=lambda item: item["name"]):
        label = cell(artifact["name"])
        expired = artifact["expired"]
        if not expired:
            label = f"[{label}]({run['html_url']}/artifacts/{artifact['id']})"
        lines.append(f"| {label} | {'已过期' if expired else '可下载'} | {beijing_time(artifact['expires_at'])} |")
    if not report["artifacts"]:
        lines.extend(["", "未获取到保留中的产物。失败发生在上传前时可能没有产物，请检查源任务日志。"])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--run-id", type=int, required=True)
    parser.add_argument("--attempt", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("--repo must be owner/name")
    if args.run_id < 1 or args.attempt < 1:
        parser.error("--run-id and --attempt must be positive")
    report = collect(args.repo, args.run_id, args.attempt)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output / "report.md").write_text(render(report), encoding="utf-8")
    print(f"Main conclusion: {report['run'].get('conclusion', 'unknown')}; report complete: {not report['errors']}")
    for error in report["errors"]:
        print(error)
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
