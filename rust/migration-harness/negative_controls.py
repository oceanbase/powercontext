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

"""Negative controls: prove the differential comparator fails for the right reason.

Four controls run on disposable copies only (the delivered files are fingerprinted before and after):

1. ``wrong_value``   - rewrite one recorded observation value in a copy of the records.
2. ``wrong_order``   - swap two recorded cases in a copy, then replay in the original order.
3. ``wrong_type``    - replace one recorded observation with a differently typed observation.
4. ``missing_native``- replay with an implausible native selector, so the harness must fail closed
   instead of silently falling back to the Python reference.

Each control must fail with a *target* mismatch on the touched field. An unrelated ImportError or a
crash before comparison is a control failure, not evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:  # allow `python rust/migration-harness/negative_controls.py`
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import recorder
import replay
from codec import SCHEMA_ID, decode_value, dump_record, encode_value, read_records


def identity(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    return {"bytes": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}


def copy_identity(paths: list[Path]) -> dict[str, Any]:
    return {path.name: identity(path) for path in paths}


def write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.write_bytes(("\n".join(dump_record(record) for record in records) + "\n").encode("utf-8"))


def control_wrong_value(records: list[dict[str, Any]], directory: Path) -> dict[str, Any]:
    records_path = directory / "wrong-value.ndjson"
    mutated = [dict(record) for record in records]
    index = next(i for i, record in enumerate(mutated) if "analyze_text" in record["observations"])
    observation = dict(mutated[index]["observations"]["analyze_text"])
    text = decode_value(observation["value"])
    observation["value"] = encode_value(f"{text} harness-negative-control")
    mutated[index]["observations"] = {**mutated[index]["observations"], "analyze_text": observation}
    write_records(records_path, mutated)
    return {
        "records": records_path,
        "expected_path": f"cases[{index}].observations.analyze_text",
    }


def control_wrong_order(records: list[dict[str, Any]], directory: Path) -> dict[str, Any]:
    records_path = directory / "wrong-order.ndjson"
    mutated = [dict(record) for record in records]
    # Swap the *observations* of two cases while keeping their inputs in place: a harness that pairs
    # records by position instead of by case identity would accept this, and must not.
    if mutated[0]["id"] == mutated[1]["id"]:
        message = "wrong_order control needs two distinct cases"
        raise AssertionError(message)
    mutated[0]["observations"], mutated[1]["observations"] = (
        mutated[1]["observations"],
        mutated[0]["observations"],
    )
    if mutated[0]["input"] == mutated[1]["input"]:
        message = "wrong_order control needs two cases with different inputs"
        raise AssertionError(message)
    write_records(records_path, mutated)
    return {"records": records_path, "expected_path": "cases[0].observations"}


def control_wrong_type(records: list[dict[str, Any]], directory: Path) -> dict[str, Any]:
    records_path = directory / "wrong-type.ndjson"
    mutated = [dict(record) for record in records]
    index = next(i for i, record in enumerate(mutated) if "analyze_text" in record["observations"])
    mutated[index]["observations"] = {
        **mutated[index]["observations"],
        # The reference returns a str; a candidate returning bytes must fail as a type difference.
        "analyze_text": {"outcome": "returned", "value": encode_value(b"not-the-reference-result")},
    }
    write_records(records_path, mutated)
    return {
        "records": records_path,
        "expected_path": f"cases[{index}].observations.analyze_text",
    }


def run_replay(records_path: Path, manifest_path: Path, selectors: list[str]) -> dict[str, Any]:
    try:
        return replay.replay(records_path, manifest_path, selectors)
    except (ValueError, RuntimeError, KeyError, TypeError) as error:
        return {"status": "ERROR", "error": f"{type(error).__name__}: {error}", "summary": {"mismatched": 0}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prove the differential comparator fails without false passes.")
    parser.add_argument("--records", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    arguments = parser.parse_args()

    delivered = [arguments.records, arguments.manifest]
    before = copy_identity(delivered)
    recorder._register_default_selectors()  # the closed selector set must be populated before replay
    manifest = json.loads(arguments.manifest.read_text(encoding="utf-8"))
    selectors = list(manifest["selectors"])
    records = read_records(arguments.records)
    arguments.temp_root.mkdir(parents=True, exist_ok=True)

    controls: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="pc2737-negative-", dir=arguments.temp_root) as temporary:
        directory = Path(temporary)
        for name, builder in (
            ("wrong_value", control_wrong_value),
            ("wrong_order", control_wrong_order),
            ("wrong_type", control_wrong_type),
        ):
            plan = builder(records, directory)
            result = run_replay(plan["records"], arguments.manifest, selectors)
            paths = result.get("summary", {}).get("mismatch_paths", [])
            controls[name] = {
                "expected_path_prefix": plan["expected_path"],
                "status": result["status"],
                "mismatched": result.get("summary", {}).get("mismatched", 0),
                "mismatch_paths": paths,
                "target_hit": any(path.startswith(plan["expected_path"]) for path in paths),
                "error": result.get("error"),
            }

        # Missing candidate: the harness must fail closed instead of silently replaying Python.
        missing = run_replay(arguments.records, arguments.manifest, ["analyze_text_native_missing"])
        controls["missing_native"] = {
            "status": missing["status"],
            "error": missing.get("error"),
            "fail_closed": missing["status"] == "ERROR",
            "target_hit": missing["status"] == "ERROR",
        }

    after = copy_identity(delivered)
    polluted = before != after
    passed = (
        all(
            controls[name]["target_hit"] and not controls[name].get("error")
            for name in ("wrong_value", "wrong_order", "wrong_type")
        )
        and controls["missing_native"]["fail_closed"]
    )
    status = "PASS" if passed and not polluted else "FAIL"
    result = {
        "schema": SCHEMA_ID,
        "record": "negative-controls",
        "evidence_kind": "REAL_TARGETED_ASSERTION_FAILURES_IN_DISPOSABLE_COPIES",
        "python": sys.version,
        "selectors": selectors,
        "controls": controls,
        "delivered_before": before,
        "delivered_after": after,
        "delivered_unchanged": not polluted,
        "status": status,
    }
    arguments.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(
        f"{status}: "
        + "; ".join(f"{name}={controls[name]['status']}" for name in controls)
        + f"; delivered_unchanged={not polluted}"
    )
    if status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
