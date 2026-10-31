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

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

CHECKER = Path(__file__).parents[1] / "scripts/check_atomic_memory_migration_shards.py"


@pytest.fixture
def reports(tmp_path: Path) -> Path:
    root = tmp_path / "reports"
    for index in range(3):
        for group in ("scope-search", "background-metrics", "clean-migration", "startup-snapshot", "long-query"):
            count = {"background-metrics": 1, "startup-snapshot": 3}.get(group, 5)
            nodes = [f"test_{group}.py::test_oceanbase_case[{case}]" for case in range(count)]
            path = root / f"shard-{index}" / group / "summary.json"
            path.parent.mkdir(parents=True)
            path.write_text(
                json.dumps({
                    "backend": "oceanbase",
                    "shard_index": index,
                    "shard_count": 3,
                    "collected_nodes": nodes,
                    "passed_nodes": nodes[index::3],
                    "complete": True,
                }),
                encoding="utf-8",
            )
    return root


def run_checker(reports: Path, shards: str = "3") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CHECKER), "--reports", str(reports), "--shards", shards],
        capture_output=True,
        text=True,
        check=False,
    )


def test_complete_coverage_including_empty_shards_passes(reports: Path) -> None:
    result = run_checker(reports)

    assert result.returncode == 0, result.stderr
    assert "background-metrics: all 1 OceanBase cases passed exactly once" in result.stdout
    assert "clean-migration: all 5 OceanBase cases passed exactly once" in result.stdout
    assert "startup-snapshot: all 3 OceanBase cases passed exactly once" in result.stdout


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("complete", False),
        ("shard_count", 4),
        ("backend", "seekdb"),
        ("shard_index", False),
        ("collected_nodes", []),
        ("collected_nodes", [[]]),
        ("passed_nodes", []),
        ("passed_nodes", ["test_other.py::test_oceanbase_case"]),
        ("passed_nodes", ["test_long-query.py::test_oceanbase_case[0]"]),
        ("passed_nodes", ["test_long-query.py::test_oceanbase_case[0]"] * 2),
    ],
)
def test_incomplete_or_invalid_execution_fails(reports: Path, field: str, value: object) -> None:
    path = reports / "shard-0" / "long-query" / "summary.json"
    report = json.loads(path.read_text())
    report[field] = value
    path.write_text(json.dumps(report), encoding="utf-8")

    result = run_checker(reports)

    assert result.returncode == 1
    assert "Shard verification failed:" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "defect", ["missing-artifact", "extra-artifact", "missing-report", "missing-startup-report", "duplicate-index"]
)
def test_missing_or_duplicate_shard_evidence_fails(reports: Path, defect: str) -> None:
    if defect == "missing-artifact":
        shutil.rmtree(reports / "shard-2")
    elif defect == "extra-artifact":
        shutil.copytree(reports / "shard-2", reports / "shard-3")
    elif defect == "missing-report":
        (reports / "shard-2" / "long-query" / "summary.json").unlink()
    elif defect == "missing-startup-report":
        (reports / "shard-2" / "startup-snapshot" / "summary.json").unlink()
    else:
        shutil.rmtree(reports / "shard-2")
        shutil.copytree(reports / "shard-0", reports / "shard-2")

    result = run_checker(reports)

    assert result.returncode == 1
    assert "Shard verification failed:" in result.stderr


@pytest.mark.parametrize("content", ["{", "[]"])
def test_malformed_report_fails_without_a_traceback(reports: Path, content: str) -> None:
    (reports / "shard-0" / "long-query" / "summary.json").write_text(content, encoding="utf-8")

    result = run_checker(reports)

    assert result.returncode == 1
    assert "Shard verification failed:" in result.stderr
    assert "Traceback" not in result.stderr


def test_individually_successful_shards_with_different_collections_fail(reports: Path) -> None:
    path = reports / "shard-1" / "long-query" / "summary.json"
    report = json.loads(path.read_text())
    report["collected_nodes"].reverse()
    report["passed_nodes"] = report["collected_nodes"][1::3]
    path.write_text(json.dumps(report), encoding="utf-8")

    result = run_checker(reports)

    assert result.returncode == 1
    assert "full collections differ" in result.stderr


def test_swapped_assignments_fail_even_when_the_union_is_complete(reports: Path) -> None:
    first = reports / "shard-0" / "long-query" / "summary.json"
    second = reports / "shard-1" / "long-query" / "summary.json"
    a = json.loads(first.read_text())
    b = json.loads(second.read_text())
    a["passed_nodes"], b["passed_nodes"] = b["passed_nodes"], a["passed_nodes"]
    first.write_text(json.dumps(a), encoding="utf-8")
    second.write_text(json.dumps(b), encoding="utf-8")

    result = run_checker(reports)

    assert result.returncode == 1
    assert "passed nodes differ from the assigned shard" in result.stderr


def test_missing_download_directory_fails(tmp_path: Path) -> None:
    result = run_checker(tmp_path / "missing")

    assert result.returncode == 1
    assert "Shard verification failed:" in result.stderr


def test_nonpositive_shard_count_is_rejected(reports: Path) -> None:
    result = run_checker(reports, "0")

    assert result.returncode == 2
    assert "--shards must be positive" in result.stderr
