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

"""Run clean migration acceptance in bounded, isolated pytest processes on Linux CI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from xml.etree import ElementTree

SUITES = (
    "tests/e2e/test_atomic_memory_migration_evidence.py",
    "tests/e2e/test_atomic_memory_grant_migration.py",
    "tests/e2e/test_atomic_memory_migration_vector.py",
)


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
            "faulthandler_timeout=120",
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
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    collection = output / "collection.log"
    if run_pytest(["--collect-only", "-q", *SUITES, "-k", args.backend], collection):
        print(collection.read_text(errors="replace"), flush=True)
        return 1
    nodes = [line for line in collection.read_text().splitlines() if line.startswith(tuple(f"{s}::" for s in SUITES))]
    if (
        not nodes
        or len(set(nodes)) != len(nodes)
        or any(not any(n.startswith(f"{s}::") for n in nodes) for s in SUITES)
    ):
        print("Each clean migration suite must collect a nonempty, unique set of nodes.", flush=True)
        return 1
    (output / "nodes.txt").write_text("\n".join(nodes) + "\n")
    print(f"Collected {len(nodes)} {args.backend} clean migration cases.", flush=True)
    if args.collect_only:
        return 0
    # SeekDB must exit between cases. OceanBase cases share one tenant, so keep
    # their schema creation and cleanup separate; this suite tests migration,
    # not concurrent DDL across databases.
    for index, node in enumerate(nodes, 1):
        if not run_node(node, output / f"{index:03d}"):
            return 1
    print(f"All {len(nodes)} {args.backend} clean migration cases passed without skips.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
