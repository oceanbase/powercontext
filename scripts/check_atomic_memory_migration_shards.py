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

"""Verify complete, exactly-once OceanBase acceptance across downloaded shard artifacts."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

GROUPS = ("scope-search", "background-metrics", "clean-migration", "startup-snapshot", "long-query")


def verify_group(paths: list[Path], shards: int) -> int:
    """Require matching collections and successful, disjoint execution of every node."""
    collection: list[str] | None = None
    passed: list[str] = []
    indices: set[int] = set()
    for path in paths:
        report = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(report, dict):
            message = f"{path}: expected a summary object"
            raise TypeError(message)
        index = report.get("shard_index")
        if (
            type(index) is not int
            or not 0 <= index < shards
            or index in indices
            or type(report.get("shard_count")) is not int
            or report["shard_count"] != shards
            or report.get("backend") != "oceanbase"
            or report.get("complete") is not True
        ):
            message = f"{path}: incomplete summary or invalid shard identity"
            raise ValueError(message)
        nodes = report.get("collected_nodes")
        actual = report.get("passed_nodes")
        if (
            not isinstance(nodes, list)
            or not nodes
            or not all(isinstance(node, str) and node for node in nodes)
            or len(set(nodes)) != len(nodes)
            or not isinstance(actual, list)
            or not all(isinstance(node, str) for node in actual)
        ):
            message = f"{path}: invalid collection or passed nodes"
            raise ValueError(message)
        if collection is None:
            collection = nodes
        if nodes != collection:
            message = f"{path}: full collections differ between shards"
            raise ValueError(message)
        if actual != nodes[index::shards]:
            message = f"{path}: passed nodes differ from the assigned shard"
            raise ValueError(message)
        indices.add(index)
        passed.extend(actual)
    if indices != set(range(shards)) or Counter(passed) != Counter(collection or []):
        message = "shard coverage must contain every collected node exactly once"
        raise ValueError(message)
    return len(passed)


def verify_reports(directory: Path, shards: int) -> None:
    """Verify every acceptance group in exactly one artifact per expected shard."""
    artifacts = sorted(path for path in directory.iterdir() if path.is_dir())
    if len(artifacts) != shards:
        message = f"expected {shards} shard artifacts, found {len(artifacts)}"
        raise ValueError(message)
    for group in GROUPS:
        count = verify_group([artifact / group / "summary.json" for artifact in artifacts], shards)
        print(f"{group}: all {count} OceanBase cases passed exactly once without skips.", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reports", type=Path, required=True, help="Directory containing one artifact directory per shard"
    )
    parser.add_argument("--shards", type=int, required=True)
    args = parser.parse_args()
    if args.shards < 1:
        parser.error("--shards must be positive")
    try:
        verify_reports(args.reports, args.shards)
    except (OSError, TypeError, ValueError) as error:
        print(f"Shard verification failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
