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

"""Run backend acceptance in isolated Linux CI processes; default to clean migration."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

SUITES = (
    "tests/e2e/test_atomic_memory_migration_evidence.py",
    "tests/e2e/test_atomic_memory_grant_migration.py",
    "tests/e2e/test_atomic_memory_migration_vector.py",
)


def parse_shard(value: str) -> tuple[int, int]:
    """Parse a zero-based INDEX/COUNT shard before collection or execution."""
    try:
        index, count = map(int, value.split("/"))
        if count > 0 and 0 <= index < count:
            return index, count
    except ValueError:
        pass
    message = "shard must be INDEX/COUNT with COUNT > 0 and 0 <= INDEX < COUNT"
    raise argparse.ArgumentTypeError(message)


def run_pytest(arguments: list[str], log: Path) -> int:
    # GNU timeout also terminates native child processes; never retry a failed node.
    command = [
        "timeout",
        "--signal=TERM",
        "--kill-after=10s",
        "180s",
        sys.executable,
        "-m",
        "pytest",
        "--color=no",
        *arguments,
    ]
    with log.open("w") as output:
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False).returncode  # noqa: S603


def run_node(node: str, directory: Path) -> bool:
    directory.mkdir()
    (directory / "node.txt").write_text(node + "\n")
    report = directory / "junit.xml"
    log = directory / "pytest.log"
    print(f"RUN {node}", flush=True)
    code = run_pytest(
        [
            "-vv",
            "-ra",
            "-o",
            # The CPython watchdog walks other threads without the GIL and can
            # itself segfault (python/cpython#116008). Keep native fault dumps;
            # GNU timeout above enforces the per-process deadline independently.
            "faulthandler_timeout=0",
            "--durations=20",
            "-o",
            f"cache_dir={directory / 'pytest-cache'}",
            f"--junitxml={report}",
            node,
        ],
        log,
    )
    passed = False
    try:
        cases = list(ElementTree.parse(report).iter("testcase"))  # noqa: S314 -- locally produced pytest report
        passed = (
            code == 0
            and len(cases) == 1
            and not any(case.find(tag) is not None for case in cases for tag in ("skipped", "failure", "error"))
        )
    except (OSError, ElementTree.ParseError):
        pass
    status = f"{'PASS' if passed else 'FAIL'} exit={code} {node}"
    (directory / "result.txt").write_text(status + "\n")
    print(status, flush=True)
    if not passed:
        print(log.read_text(errors="replace")[-16000:], flush=True)
    return passed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backend", choices=("seekdb", "oceanbase"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--collect-only", action="store_true")
    parser.add_argument(
        "--shard", type=parse_shard, default=(0, 1), help="Run zero-based shard INDEX/COUNT (default: 0/1)"
    )
    parser.add_argument("--tests", nargs="+", default=SUITES, help="Pytest files to run instead of clean migration")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shard_index, shard_count = args.shard
    passed_nodes: list[str] = []
    summary = {
        "backend": args.backend,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "collected_nodes": [],
        "passed_nodes": passed_nodes,
        "complete": False,
    }
    summary_path = output / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    collection = output / "collection.log"
    if run_pytest(["--collect-only", "-q", *args.tests, "-k", args.backend], collection):
        print(collection.read_text(errors="replace"), flush=True)
        return 1
    nodes = [
        line for line in collection.read_text().splitlines() if line.startswith(tuple(f"{s}::" for s in args.tests))
    ]
    summary["collected_nodes"] = nodes
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if (
        not nodes
        or len(set(nodes)) != len(nodes)
        or any(not any(n.startswith(f"{s}::") for n in nodes) for s in args.tests)
    ):
        print("Each backend suite must collect a nonempty, unique set of nodes.", flush=True)
        return 1
    (output / "nodes.txt").write_text("\n".join(nodes) + "\n")
    print(f"Collected {len(nodes)} {args.backend} cases.", flush=True)
    if args.collect_only:
        return 0
    selected = nodes[shard_index::shard_count]
    print(f"Shard {shard_index}/{shard_count}: running {len(selected)} cases.", flush=True)
    # SeekDB must exit between cases. OceanBase cases share one tenant, so keep
    # their schema creation and cleanup separate from other test cases.
    try:
        for index, node in enumerate(selected, 1):
            if not run_node(node, output / f"{index:03d}"):
                return 1
            passed_nodes.append(node)
        summary["complete"] = True
    finally:
        # Only record passes after run_node has checked the exit code and JUnit.
        # A killed process leaves the initial, incomplete report behind.
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"All {len(selected)} {args.backend} cases passed without skips.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
