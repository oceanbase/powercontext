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
import math
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path
from xml.etree import ElementTree

from oceanbase_ci import run_command

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


def read_durations(path: Path | None) -> dict[str, float] | None:
    if path is None:
        return None
    values = json.loads(path.read_text())["durations_seconds"]
    if (
        not isinstance(values, dict)
        or not values
        or any(
            not isinstance(node, str) or type(seconds) not in (int, float) or not math.isfinite(seconds) or seconds <= 0
            for node, seconds in values.items()
        )
    ):
        raise ValueError("Expected positive, finite per-node durations")  # noqa: TRY003
    return values


def assigned_nodes(nodes: list[str], index: int, count: int, durations: dict[str, float] | None) -> list[str]:
    """Pack expensive cases first within an equal per-shard case ceiling."""
    if durations is None:
        return nodes[index::count]
    fallback = statistics.median(durations.values())
    loads = [0.0] * count
    sizes = [0] * count
    capacity = math.ceil(len(nodes) / count)
    assignments: dict[str, int] = {}
    for node in sorted(nodes, key=lambda node: (-durations.get(node, fallback), node)):
        shard = min(
            (shard for shard in range(count) if sizes[shard] < capacity),
            key=lambda shard: (loads[shard], shard),
        )
        assignments[node] = shard
        sizes[shard] += 1
        loads[shard] += durations.get(node, fallback)
    # Preserve collection order within each independent database instance.
    return [node for node in nodes if assignments[node] == index]


def run_pytest(
    arguments: list[str],
    log: Path,
    *,
    timeout_seconds: int = 180,
    container: str | None = None,
    phase_limits: dict[str, float] | None = None,
) -> int:
    # GNU timeout also terminates native child processes; never retry a failed node.
    command = [sys.executable, "-m", "pytest", "--color=no", *arguments]
    if phase_limits:
        command.extend(["-p", "pytest_ci_phases"])
    with log.open("w") as output:
        if container or phase_limits:
            return run_command(
                command,
                container=container,
                output=log.with_suffix(".diagnostics"),
                timeout=timeout_seconds,
                stdout=output,
                phase_limits=phase_limits,
            )
        command = ["timeout", "--signal=TERM", "--kill-after=10s", f"{timeout_seconds}s", *command]
        return subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, check=False).returncode  # noqa: S603


def run_node(
    node: str,
    directory: Path,
    *,
    timeout_seconds: int,
    container: str | None = None,
    phase_limits: dict[str, float] | None = None,
) -> bool:
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
        timeout_seconds=timeout_seconds,
        container=container,
        phase_limits=phase_limits,
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
    parser.add_argument("--durations", type=Path, help="Measured OceanBase per-node durations for balanced assignment")
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=180,
        help="Per-case deadline, or execution-only deadline when --initialization-timeout is set (default: 180)",
    )
    parser.add_argument("--initialization-timeout", type=int, help="Separate fixture/schema initialization budget")
    parser.add_argument("--cleanup-timeout", type=int, default=60, help="Separate cleanup budget (default: 60)")
    parser.add_argument(
        "--shard", type=parse_shard, default=(0, 1), help="Run zero-based shard INDEX/COUNT (default: 0/1)"
    )
    parser.add_argument("--tests", nargs="+", default=SUITES, help="Pytest files to run instead of clean migration")
    args = parser.parse_args()
    if args.test_timeout <= 0:
        parser.error("--test-timeout must be a positive number of seconds")
    if args.initialization_timeout is not None and (args.initialization_timeout <= 0 or args.cleanup_timeout <= 0):
        parser.error("Initialization and cleanup timeouts must be positive")
    phase_limits = None
    case_timeout = args.test_timeout
    if args.initialization_timeout is not None:
        phase_limits = {
            "collection": 60,
            "initialization": args.initialization_timeout,
            "execution": args.test_timeout,
            "cleanup": args.cleanup_timeout,
        }
        case_timeout = sum(phase_limits.values())
    if args.durations and args.backend != "oceanbase":
        parser.error("Duration-based scheduling is only enabled for OceanBase")
    durations = read_durations(args.durations)
    container = os.environ.get("OCEANBASE_CI_CONTAINER") if args.backend == "oceanbase" else None
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    shard_index, shard_count = args.shard
    passed_nodes: list[str] = []
    elapsed_seconds: dict[str, float] = {}
    summary = {
        "backend": args.backend,
        "shard_index": shard_index,
        "shard_count": shard_count,
        "collected_nodes": [],
        "passed_nodes": passed_nodes,
        "elapsed_seconds": elapsed_seconds,
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
    selected = assigned_nodes(nodes, shard_index, shard_count, durations)
    print(f"Shard {shard_index}/{shard_count}: running {len(selected)} cases.", flush=True)
    # SeekDB must exit between cases. OceanBase cases share one tenant, so keep
    # their schema creation and cleanup separate from other test cases.
    try:
        for index, node in enumerate(selected, 1):
            started = time.monotonic()
            passed = run_node(
                node,
                output / f"{index:03d}",
                timeout_seconds=case_timeout,
                container=container,
                phase_limits=phase_limits,
            )
            elapsed_seconds[node] = round(time.monotonic() - started, 3)
            if not passed:
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
