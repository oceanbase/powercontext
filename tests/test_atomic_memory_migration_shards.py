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

"""Exercise the migration runner through real pytest collection and execution."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
RUNNER = ROOT / "scripts/run_atomic_memory_clean_migration.py"
CHECKER = ROOT / "scripts/check_atomic_memory_migration_shards.py"
pytestmark = pytest.mark.skipif(sys.platform == "win32" or not shutil.which("timeout"), reason="Linux CI runner")


def write_suite(directory: Path, count: int, *, backend: str = "oceanbase", outcome: str = "pass") -> None:
    (directory / "test_cases.py").write_text(
        f"""import pathlib
import pytest

@pytest.mark.parametrize('case', range({count}))
def test_{backend}_case(case, request):
    with pathlib.Path('executed.txt').open('a') as output:
        output.write(request.node.nodeid + '\\n')
    if case == 1:
        if {outcome!r} == 'skip':
            pytest.skip('synthetic skip')
        if {outcome!r} == 'fail':
            pytest.fail('synthetic failure')
        if {outcome!r} == 'interrupt':
            raise KeyboardInterrupt
""",
        encoding="utf-8",
    )


def run_runner(
    directory: Path,
    output: Path,
    *arguments: str,
    backend: str = "oceanbase",
    tests: tuple[str, ...] = ("test_cases.py",),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), backend, "--output", str(output), *arguments, "--tests", *tests],
        cwd=directory,
        env={**os.environ, "PYTEST_ADDOPTS": "", "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1"},
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def test_three_shards_execute_every_collected_node_exactly_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The temporary suites do not load the repository's CI-only pytest options.
    monkeypatch.setenv("PYTEST_ADDOPTS", "--require-dsh-config-runtime")
    write_suite(tmp_path, 5)
    reports = []
    for index in range(3):
        output = tmp_path / f"shard-{index}"
        result = run_runner(tmp_path, output, "--shard", f"{index}/3")
        assert result.returncode == 0, result.stdout + result.stderr
        reports.append(json.loads((output / "summary.json").read_text()))

    nodes = reports[0]["collected_nodes"]
    assert len(nodes) == 5
    for index, report in enumerate(reports):
        assert report["collected_nodes"] == nodes
        assert report["passed_nodes"] == nodes[index::3]
        assert report["complete"] is True
    actual = (tmp_path / "executed.txt").read_text().splitlines()
    assert Counter(actual) == Counter(nodes)
    assert Counter(node for report in reports for node in report["passed_nodes"]) == Counter(actual)

    # Verify actual runner reports through the aggregate CLI's artifact layout.
    for index, report in enumerate(reports):
        artifact = tmp_path / "artifacts" / f"shard-{index}"
        for group in ("scope-search", "background-metrics", "clean-migration", "startup-snapshot", "long-query"):
            destination = artifact / group
            destination.mkdir(parents=True)
            (destination / "summary.json").write_text(json.dumps(report), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(CHECKER), "--reports", str(tmp_path / "artifacts"), "--shards", "3"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_valid_collection_can_produce_an_empty_shard(tmp_path: Path) -> None:
    write_suite(tmp_path, 1)
    output = tmp_path / "empty-shard"
    result = run_runner(tmp_path, output, "--shard", "2/3")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert len(report["collected_nodes"]) == 1
    assert report["passed_nodes"] == []
    assert report["complete"] is True
    assert not (tmp_path / "executed.txt").exists()


@pytest.mark.parametrize("backend", ["seekdb", "oceanbase"])
def test_default_runs_the_full_collection(tmp_path: Path, backend: str) -> None:
    write_suite(tmp_path, 2, backend=backend)
    output = tmp_path / "unsharded"
    result = run_runner(tmp_path, output, backend=backend)

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert report["backend"] == backend
    assert (report["shard_index"], report["shard_count"]) == (0, 1)
    assert len(report["passed_nodes"]) == 2
    assert report["passed_nodes"] == report["collected_nodes"]
    assert report["complete"] is True


@pytest.mark.parametrize("shard", ["-1/3", "3/3", "0/0", "0/-1", "x/3", "0/3/1", "1"])
def test_invalid_shard_is_rejected_before_collection(tmp_path: Path, shard: str) -> None:
    output = tmp_path / "invalid"
    result = run_runner(tmp_path, output, f"--shard={shard}")

    assert result.returncode == 2
    assert "INDEX/COUNT" in result.stderr
    assert not output.exists()


@pytest.mark.parametrize("invalid_collection", ["duplicate", "missing-suite", "empty"])
def test_full_collection_must_be_valid_even_for_an_empty_shard(tmp_path: Path, invalid_collection: str) -> None:
    write_suite(tmp_path, 1)
    tests = ("test_cases.py",)
    if invalid_collection == "duplicate":
        (tmp_path / "conftest.py").write_text(
            "def pytest_collection_modifyitems(items): items.append(items[0])\n", encoding="utf-8"
        )
    elif invalid_collection == "missing-suite":
        (tmp_path / "test_other.py").write_text("def test_seekdb_case(): pass\n", encoding="utf-8")
        tests = ("test_cases.py", "test_other.py")
    else:
        write_suite(tmp_path, 1, backend="seekdb")
    output = tmp_path / "invalid-collection"
    result = run_runner(tmp_path, output, "--shard", "2/3", tests=tests)

    assert result.returncode == 1
    report = json.loads((output / "summary.json").read_text())
    assert report["complete"] is False
    assert report["passed_nodes"] == []
    assert not (tmp_path / "executed.txt").exists()


def test_collect_only_does_not_claim_execution(tmp_path: Path) -> None:
    write_suite(tmp_path, 2)
    output = tmp_path / "collected"
    result = run_runner(tmp_path, output, "--shard", "0/3", "--collect-only")

    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads((output / "summary.json").read_text())
    assert len(report["collected_nodes"]) == 2
    assert report["passed_nodes"] == []
    assert report["complete"] is False
    assert not (tmp_path / "executed.txt").exists()


@pytest.mark.parametrize("outcome", ["fail", "skip", "interrupt"])
def test_unsuccessful_case_is_not_counted_as_passed_and_is_not_retried(tmp_path: Path, outcome: str) -> None:
    write_suite(tmp_path, 3, outcome=outcome)
    output = tmp_path / "failed"
    result = run_runner(tmp_path, output)

    assert result.returncode == 1
    report = json.loads((output / "summary.json").read_text())
    assert report["passed_nodes"] == report["collected_nodes"][:1]
    assert report["complete"] is False
    assert (tmp_path / "executed.txt").read_text().splitlines() == report["collected_nodes"][:2]
