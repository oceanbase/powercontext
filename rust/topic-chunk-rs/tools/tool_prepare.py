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

"""Prepare official-tool requests and identity without rerecording the frozen golden."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import runpy
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    harness = runpy.run_path(str(ROOT / "python/powercontext_topic_chunk_rs/_harness.py"))
    harness["check_reference"]()
    api = runpy.run_path(str(ROOT / "python/powercontext_topic_chunk_rs/_tool_protocol.py"))
    cases = harness["cases"]()
    golden = [json.loads(line) for line in (ROOT / "corpus/golden.jsonl").read_text().split("\n") if line]
    if len(cases) != len(golden) or len({case["id"] for case in cases}) != len(cases):
        message = "Invalid frozen case inventory"
        raise RuntimeError(message)
    inputs, identities = [], []
    for case, expected in zip(cases, golden, strict=True):
        value = harness["materialize"](case)
        call: dict[str, Any] = {"schema": 1, "id": case["id"]}
        if case.get("missing"):
            call["missing"] = True
        else:
            call["value"] = harness["encode"](value)
        request = api["canonical"](call)
        identity, decoded = api["decode_request"](request)
        restored = None if decoded.get("missing") else decoded["invalid"]
        if (
            identity != case["id"]
            or identity != expected["id"]
            or bool(decoded.get("missing")) != bool(case.get("missing"))
            or (not case.get("missing") and (type(restored) is not type(value) or restored != value))
        ):
            message = "Tool request did not round-trip exactly"
            raise RuntimeError(message)
        inputs.append({"fn": "tool_outcome", "args": [request]})
        identities.append({"id": identity, "sha256": hashlib.sha256(request.encode("ascii")).hexdigest()})
    inputs.append({"fn": "tool_outcome", "args": ["{}"]})
    source_files = [
        "Cargo.toml",
        "Cargo.lock",
        "rust-anything.json",
        "corpus/cases.json",
        "corpus/manifest.json",
        "corpus/golden.jsonl",
        "src/lib.rs",
        "src/port.rs",
        "src/unicode_tables.rs",
        "tools/tool_reference.py",
        "tools/tool_prepare.py",
        "tools/tool_check.py",
    ]
    package = ROOT / "python/powercontext_topic_chunk_rs"
    candidate_sources = {
        name: harness["digest"](package / name)
        for name in ("__init__.py", "_tool_entry.py", "_tool_protocol.py", "_harness.py", "_adapter.py", "_monitor.py")
    }
    source_files += [f"python/powercontext_topic_chunk_rs/{name}" for name in candidate_sources]
    context = {
        "schema": 1,
        "root": str(ROOT),
        "requests": identities,
        "candidate_sources": candidate_sources,
        "protocol_rejection": {
            "sha256": hashlib.sha256(b"{}").hexdigest(),
            "class": "builtins.ValueError",
            "text": "Invalid tool request",
            "native_delta": 0,
        },
        "source_hashes": {name: harness["digest"](ROOT / name) for name in source_files},
    }
    manifest = json.loads((ROOT / "rust-anything.json").read_text())
    reference = ROOT / "tools/tool_reference.py"
    line = next(
        node.lineno
        for node in ast.parse(reference.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "tool_outcome"
    )
    if manifest["exports"][0]["line"] != line:
        message = "Tool manifest reference line is stale"
        raise RuntimeError(message)
    inputs_path, context_path = args.output / "inputs.jsonl", args.output / "context.json"
    inputs_path.write_text("".join(json.dumps(row, ensure_ascii=True) + "\n" for row in inputs))
    context_path.write_text(json.dumps(context, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps({
            "inputs": str(inputs_path),
            "context": str(context_path),
            "context_sha256": harness["digest"](context_path),
            "cases": len(inputs),
        })
    )


if __name__ == "__main__":
    main()
