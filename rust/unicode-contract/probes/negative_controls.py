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

"""Prove targeted assertions reject surrogate-pair merging and wrong UCD goldens."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from fixture_codec import codepoints, decode_string, dump_json


def identity(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def run_check(root: Path, fixtures: Path, reference: Path, output: Path) -> dict[str, Any]:
    command = [
        sys.executable,
        str(root / "contract_probe.py"),
        "--fixtures",
        str(fixtures),
        "--output",
        str(output),
        "--check",
        str(reference),
    ]
    completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
        command,
        cwd=root.parents[2],
        text=True,
        capture_output=True,
        timeout=180,
    )
    return {
        "command": command,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "target_assertion_failed": completed.returncode != 0 and "target mismatch at" in completed.stderr,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fixtures", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.temp_root.mkdir(parents=True, exist_ok=True)
    root = Path(__file__).resolve().parent
    before = {"fixtures": identity(arguments.fixtures), "reference": identity(arguments.reference)}

    with tempfile.TemporaryDirectory(prefix="pc2739-negative-", dir=arguments.temp_root) as temporary:
        temporary_root = Path(temporary)
        pair_root = temporary_root / "pair"
        ucd_root = temporary_root / "ucd"
        pair_root.mkdir()
        ucd_root.mkdir()
        pair_fixtures = pair_root / "cases.json"
        pair_reference = pair_root / "reference.json"
        ucd_fixtures = ucd_root / "cases.json"
        ucd_reference = ucd_root / "reference.json"
        for source, destination in (
            (arguments.fixtures, pair_fixtures),
            (arguments.reference, pair_reference),
            (arguments.fixtures, ucd_fixtures),
            (arguments.reference, ucd_reference),
        ):
            shutil.copyfile(source, destination)

        pair_document = json.loads(pair_fixtures.read_text(encoding="utf-8"))
        pair_case = next(case for case in pair_document["cases"] if case["id"] == "pair_only")
        explicit_pair = decode_string(pair_case["input"])
        # This deliberately unsafe UTF-16 round-trip is confined to the disposable fixture.
        merged = explicit_pair.encode("utf-16-le", "surrogatepass").decode("utf-16-le")
        if codepoints(merged) != [0x1F600]:
            message = "negative setup did not produce the intended merged scalar"
            raise AssertionError(message)
        pair_case["input"] = {"type": "str", "codepoints": codepoints(merged)}
        dump_json(pair_fixtures, pair_document)
        pair_result = run_check(root, pair_fixtures, pair_reference, pair_root / "actual.json")
        pair_result["merged_codepoints"] = codepoints(merged)

        ucd_document = json.loads(ucd_reference.read_text(encoding="utf-8"))
        ucd_case = next(case for case in ucd_document["cases"] if case["id"] == "ucd_105d2_combining")
        property_row = next(
            row for row in ucd_case["observations"]["character_properties"] if row["codepoint"] == 0x105D2
        )
        original_assigned = property_row["assigned_by_generator_rule"]
        property_row["assigned_by_generator_rule"] = not original_assigned
        dump_json(ucd_reference, ucd_document)
        ucd_result = run_check(root, ucd_fixtures, ucd_reference, ucd_root / "actual.json")
        ucd_result["mutated_case"] = "ucd_105d2_combining"
        ucd_result["wrong_expectation"] = {
            "codepoint": "U+105D2",
            "field": "assigned_by_generator_rule",
            "reference_value": original_assigned,
            "wrong_value": not original_assigned,
        }

    after = {"fixtures": identity(arguments.fixtures), "reference": identity(arguments.reference)}
    reference_unchanged = before == after
    status = (
        "PASS"
        if pair_result["target_assertion_failed"] and ucd_result["target_assertion_failed"] and reference_unchanged
        else "FAIL"
    )
    result = {
        "schema": "powercontext.unicode-contract.negative-controls.v1",
        "evidence_kind": "REAL_TARGETED_ASSERTION_FAILURES_IN_DISPOSABLE_COPIES",
        "python": sys.version,
        "executable": sys.executable,
        "pair_merge": pair_result,
        "wrong_ucd": ucd_result,
        "reference_before": before,
        "reference_after": after,
        "reference_unchanged": reference_unchanged,
        "status": status,
        "native_execution": "NOT_RUN",
    }
    dump_json(arguments.output, result)
    print(
        f"{status}: pair_exit={pair_result['exit_code']}; ucd_exit={ucd_result['exit_code']}; "
        f"reference_unchanged={reference_unchanged}"
    )
    if status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
