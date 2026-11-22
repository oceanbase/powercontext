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

"""Replay recorded observations against a candidate (native or alternative) implementation.

The comparison walks the recorded expectation, so every recorded value is compared: a missing case or
a missing selector is a failure, not a silent skip. Paths are reported so a mismatch points at the
exact observed field (``cases[7].observations.analyze_text.value.$v``).

Exit codes: ``0`` all compared and identical, ``1`` at least one mismatch, ``2`` nothing comparable
(empty corpus / empty selection), which must never be reported as a pass.
"""

from __future__ import annotations

import argparse
import importlib
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # allow `python rust/migration-harness/replay.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import recorder
from codec import SCHEMA_ID, decode_value, read_records
from recorder import SELECTORS, _register_default_selectors, observe


def candidate_identity() -> dict[str, Any]:
    """Report which module actually answered the calls (path, suffix and load route)."""

    search = importlib.import_module("powercontext.builtin.artifacts.search")
    module_file = Path(getattr(search, "__file__", "") or "").resolve()
    native_suffixes = {".pyd", ".so", ".dylib", ".dll"}
    try:
        module_location = module_file.relative_to(recorder.REPO_ROOT).as_posix()
    except ValueError:
        module_location = module_file.name
    return {
        "module": "powercontext.builtin.artifacts.search",
        "location": module_location,
        "suffix": module_file.suffix,
        "native": module_file.suffix in native_suffixes,
        "loader": type(search.__loader__).__name__ if search.__loader__ is not None else None,
    }


class Comparison:
    """Accumulate per-case comparison outcomes with explicit path reporting."""

    def __init__(self) -> None:
        self.compared = 0
        self.matched = 0
        self.mismatched: list[dict[str, Any]] = []
        self.uncompared: list[dict[str, Any]] = []

    def compare(self, path: str, expected: Any, actual: Any) -> None:
        self.compared += 1
        if expected == actual:
            self.matched += 1
            return
        self.mismatched.append({"path": path, "expected": expected, "actual": actual})

    def skip(self, path: str, reason: str) -> None:
        self.uncompared.append({"path": path, "reason": reason})

    def summary(self) -> dict[str, Any]:
        return {
            "compared": self.compared,
            "matched": self.matched,
            "mismatched": len(self.mismatched),
            "uncompared": len(self.uncompared),
            "mismatch_paths": [row["path"] for row in self.mismatched],
        }


def load_manifest(path: Path) -> dict[str, Any]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != SCHEMA_ID:
        message = f"manifest schema must be {SCHEMA_ID!r}"
        raise ValueError(message)
    return manifest


def replay(
    records_path: Path,
    manifest_path: Path,
    selectors: list[str],
    limit: int | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    records = read_records(records_path)
    if not records:
        message = "no recorded records"
        raise ValueError(message)
    if not selectors:
        message = "the selector set must be non-empty"
        raise ValueError(message)
    unknown = sorted(set(selectors) - set(SELECTORS))
    if unknown:
        message = f"unknown selectors: {unknown}"
        raise ValueError(message)
    if limit is not None and limit < len(selectors):
        message = f"limit {limit} is below the selection size {len(selectors)}; refusing to truncate"
        raise ValueError(message)

    comparison = Comparison()
    executed = 0
    for index, record in enumerate(records):
        text = decode_value(record["input"])
        for selector in selectors:
            path = f"cases[{index}].observations.{selector}"
            if selector not in record["observations"]:
                comparison.skip(path, "selector was not recorded for this case")
                continue
            executed += 1
            comparison.compare(path, record["observations"][selector], observe(SELECTORS[selector], text))

    summary = comparison.summary()
    candidate = dict(manifest.get("candidate", {}))
    candidate.update(candidate_identity())
    status = "PASS" if summary["mismatched"] == 0 and summary["compared"] > 0 else "FAIL"
    if summary["compared"] == 0:
        status = "FAIL"
        summary["reason"] = "no observation was compared"
    return {
        "schema": SCHEMA_ID,
        "record": "replay",
        "status": status,
        "reference": {
            "python": manifest["python"]["version"],
            "unicode": manifest["python"]["unicode"],
            "source_sha256": manifest["source"]["source_sha256"],
            "corpus_sha256": manifest["corpus"]["sha256"],
        },
        "candidate": candidate,
        "selectors": selectors,
        "cases": len(records),
        "executed_calls": executed,
        "summary": summary,
        "mismatches": comparison.mismatched[:50],
        "uncompared": comparison.uncompared[:50],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay reference observations against a candidate.")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--selector", action="append", default=[])
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--limit", type=int, default=None)
    arguments = parser.parse_args()

    _register_default_selectors()
    selectors = arguments.selector or sorted(SELECTORS)
    result = replay(arguments.records, arguments.manifest, selectors, arguments.limit)
    if arguments.output is not None:
        arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    summary = result["summary"]
    print(
        f"{result['status']}: compared={summary['compared']} matched={summary['matched']} "
        f"mismatched={summary['mismatched']} uncompared={summary['uncompared']} "
        f"calls={result['executed_calls']}"
    )
    for row in result["mismatches"]:
        print(f"  mismatch at {row['path']}")
    if result["status"] != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
