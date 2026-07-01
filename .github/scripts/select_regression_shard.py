#!/usr/bin/env python3
"""Select a balanced file-level shard for regression tests."""

from __future__ import annotations

import argparse
import ast
import os
from pathlib import Path


def test_weight(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    count = sum(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name.startswith("test_")
        for node in ast.walk(tree)
    )
    return max(count, 1)


def select_files(mode: str) -> list[Path]:
    files = sorted(Path("tests/regression").glob("test_*.py"))
    if mode == "scenarios":
        return [path for path in files if path.name.startswith("test_scenario")]
    if mode == "non-scenarios":
        return [path for path in files if not path.name.startswith("test_scenario")]
    raise ValueError(f"unknown shard mode: {mode}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("scenarios", "non-scenarios"), required=True)
    parser.add_argument("--shard-index", type=int, required=True)
    parser.add_argument("--shard-total", type=int, required=True)
    parser.add_argument("--output", default="selected-tests.txt")
    args = parser.parse_args()
    if args.shard_total <= 0:
        parser.error("--shard-total must be greater than zero")
    if args.shard_index < 0 or args.shard_index >= args.shard_total:
        parser.error("--shard-index must be between zero and shard-total - 1")

    shards = [{"weight": 0, "files": []} for _ in range(args.shard_total)]
    weighted_files = sorted(
        ((path, test_weight(path)) for path in select_files(args.mode)),
        key=lambda item: (-item[1], item[0].as_posix()),
    )

    for path, weight in weighted_files:
        shard = min(shards, key=lambda item: (item["weight"], len(item["files"])))
        shard["files"].append(path)
        shard["weight"] += weight

    selected = [path.as_posix() for path in shards[args.shard_index]["files"]]
    Path(args.output).write_text(
        "\n".join(selected) + ("\n" if selected else ""),
        encoding="utf-8",
    )

    print(
        f"{args.mode} shard {args.shard_index + 1}/{args.shard_total}: "
        f"{len(selected)} files, estimated weight {shards[args.shard_index]['weight']}"
    )
    for path in selected:
        print(f"  {path}")

    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as output:
            output.write(f"has_tests={'true' if selected else 'false'}\n")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
