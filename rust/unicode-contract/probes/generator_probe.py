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

"""Execute the fixed generator in a disposable tree and compare its one table."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
import unicodedata
from pathlib import Path

from candidate_inventory import GENERATOR, TABLES, git_bytes, identity
from fixture_codec import dump_json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--temp-root", type=Path, required=True)
    arguments = parser.parse_args()
    arguments.temp_root.mkdir(parents=True, exist_ok=True)

    version = unicodedata.unidata_version
    if version not in TABLES:
        result = {
            "schema": "powercontext.unicode-contract.generator-result.v1",
            "evidence_kind": "BLOCKED_UNKNOWN_INTERPRETER_UCD",
            "candidate": arguments.candidate,
            "python": sys.version,
            "executable": sys.executable,
            "unicodedata_version": version,
            "status": "BLOCKED",
            "reason": "fixed generator has no table name for this runtime UCD",
        }
        dump_json(arguments.output, result)
        print(f"BLOCKED: unsupported runtime UCD {version}")
        raise SystemExit(2)

    generator_raw = git_bytes(arguments.candidate, GENERATOR)
    candidate_raw = git_bytes(arguments.candidate, TABLES[version])
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="pc2739-generator-", dir=arguments.temp_root) as temporary:
        root = Path(temporary)
        script = root / "scripts" / "generate_lexical_unicode.py"
        data = root / "src/powercontext/builtin/artifacts/lexical/data"
        data.mkdir(parents=True)
        script.parent.mkdir()
        script.write_bytes(generator_raw)
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            [sys.executable, str(script)],
            cwd=root,
            text=True,
            capture_output=True,
            timeout=240,
        )
        elapsed = time.perf_counter() - started
        generated_path = data / Path(TABLES[version]).name
        generated_raw = generated_path.read_bytes() if generated_path.exists() else None

    semantic_match = False
    lf_match = False
    raw_match = False
    if generated_raw is not None:
        semantic_match = json.loads(generated_raw) == json.loads(candidate_raw)
        lf_match = generated_raw.replace(b"\r\n", b"\n") == candidate_raw.replace(b"\r\n", b"\n")
        raw_match = generated_raw == candidate_raw
    status = "PASS" if completed.returncode == 0 and semantic_match and lf_match else "FAIL"
    result = {
        "schema": "powercontext.unicode-contract.generator-result.v1",
        "evidence_kind": "REAL_CPYTHON_GENERATOR_IN_DISPOSABLE_COPY",
        "candidate": arguments.candidate,
        "python": sys.version,
        "executable": sys.executable,
        "unicodedata_version": version,
        "command": [sys.executable, "<disposable>/scripts/generate_lexical_unicode.py"],
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "wall_seconds_one_sample": elapsed,
        "status": status,
        "candidate_generator_git_blob": identity(generator_raw),
        "candidate_generator_unchanged_after_run": git_bytes(arguments.candidate, GENERATOR) == generator_raw,
        "candidate_table_git_blob": identity(candidate_raw),
        "generated_table": identity(generated_raw) if generated_raw is not None else None,
        "semantic_json_match": semantic_match,
        "lf_bytes_match": lf_match,
        "raw_bytes_match": raw_match,
        "raw_mismatch_explanation": None
        if raw_match
        else "Windows text-mode CRLF is compared separately from Git LF bytes",
        "native_execution": "NOT_RUN",
    }
    dump_json(arguments.output, result)
    print(f"{status}: UCD {version}; semantic={semantic_match}; LF={lf_match}; raw={raw_match}; {elapsed:.3f}s")
    if status != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
