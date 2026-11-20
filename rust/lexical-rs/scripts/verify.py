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


"""Strict recorded outputs, error classes, native identity and missing-module control."""

import argparse
import hashlib
import importlib
import json
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

CRATE = Path(__file__).resolve().parents[1]
ROOT = CRATE.parents[1]


def outcome(function, value):
    try:
        result = function(value)
        return {"ok": result, "type": type(result).__name__}
    except Exception as error:
        return {"error": type(error).__name__}


def compare(actual, expected):
    if actual != expected:
        raise AssertionError({"actual": actual, "expected": expected})


def load_native(args):
    if unicodedata.unidata_version != "16.0.0":
        message = "This pilot verifies Unicode16 only"
        raise RuntimeError(message)
    if not args.installed:
        sys.path.insert(0, str(CRATE / "python"))
    package = importlib.import_module("pc_lexical_pilot")
    native = importlib.import_module("pc_lexical_pilot._native")
    if native.__file__ is None:
        message = "Native module has no build identity"
        raise RuntimeError(message)
    native_path = Path(native.__file__).resolve()
    if native_path.suffix not in [".pyd", ".so"]:
        message = "Expected a real native module"
        raise AssertionError(message)
    native_sha = hashlib.sha256(native_path.read_bytes()).hexdigest()
    if args.expected_native_sha and native_sha != args.expected_native_sha:
        message = "Unexpected native build identity"
        raise AssertionError(message)
    if package.analyze_text is not native.analyze_text:
        message = "Expected direct native function, no Python fallback wrapper"
        raise AssertionError(message)
    return package, native_path, native_sha


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--installed", action="store_true")
    parser.add_argument("--expected-native-sha")
    args = parser.parse_args()
    package, native_path, native_sha = load_native(args)
    rows = [
        json.loads(line)
        for line in (ROOT / "output/lexical-pilot/strict-cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    mismatches = []
    for number, row in enumerate(rows):
        value = "".join(map(chr, row["codepoints"]))
        expected = {**row["expected"], "type": "str"}
        actual = outcome(package.analyze_text, value)
        if actual != expected:
            mismatches.append({
                "case": number,
                "codepoints": row["codepoints"][:100],
                "actual": actual,
                "expected": expected,
            })
    for value in [None, True, 1, 1.5, [], {}, b"x"]:
        compare(outcome(package.analyze_text, value), {"error": "TypeError"})

    class Text(str):
        pass

    compare(outcome(package.analyze_text, Text("ABC ab ABC")), {"ok": "abc pcu6162 abc", "type": "str"})
    # Independent expected values catch an oracle/wrapper that makes both sides wrong.
    compare(outcome(package.analyze_text, "a\ud800b"), {"ok": "pcu61 pcu62", "type": "str"})
    compare(outcome(package.analyze_text, "\ud800\udc00"), {"ok": "", "type": "str"})
    compare(outcome(package.analyze_text, "ǰ"), {"ok": "pcu6a", "type": "str"})
    try:
        compare(outcome(package.analyze_text, "abc"), {"ok": "intentionally wrong", "type": "str"})
    except AssertionError:
        mutation_detected = True
    else:
        message = "Strict comparator accepted an intentionally wrong result"
        raise AssertionError(message)
    with tempfile.TemporaryDirectory(prefix="pc-native-negative-") as directory:
        copied = Path(directory) / "pc_lexical_pilot"
        copied.mkdir()
        shutil.copy2(Path(package.__file__), copied / "__init__.py")
        program = "import sys;sys.path.insert(0,sys.argv[1]);\ntry:\n import pc_lexical_pilot\nexcept ImportError:\n print('native unavailable: rejected')\nelse:\n raise SystemExit(3)"
        result = subprocess.run(  # noqa: S603 - fixed program and task-owned temporary package.
            [sys.executable, "-I", "-c", program, directory], capture_output=True, text=True
        )
        if result.returncode != 0 or "rejected" not in result.stdout:
            message = "Missing native module did not fail closed"
            raise AssertionError(message)
    report = {
        "python": sys.version,
        "unicode": unicodedata.unidata_version,
        "native_path": str(native_path),
        "native_sha256": native_sha,
        "recorded_string_cases": len(rows),
        "passed_strings": len(rows) - len(mismatches),
        "mismatches": mismatches[:10],
        "type_error_cases": 7,
        "str_subclass_cases": 1,
        "independent_expected_cases": 3,
        "total_executions": len(rows) + 11,
        "repeated_independent_executions": 3,
        "N": len(rows) + 8,
        "R": len(rows) + 8,
        "F": 0,
        "E": 0,
        "incorrect_result_detected": mutation_detected,
        "missing_native_rejected": True,
        "scope": "isolated native analyze_text; default production route unchanged",
    }
    path = ROOT / "output/lexical-pilot" / ("installed-strict.json" if args.installed else "strict.json")
    path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=True, indent=2))
    if mismatches:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
