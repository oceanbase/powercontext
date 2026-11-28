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

"""Read fixed Git blobs and fingerprint #2735 Unicode inputs without checking them out."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import tomllib
from pathlib import Path
from typing import Any

from fixture_codec import dump_json

GENERATOR = "scripts/generate_lexical_unicode.py"
LEXICAL = "src/powercontext/builtin/artifacts/lexical"
TABLES = {
    "14.0.0": f"{LEXICAL}/data/unicode14.json",
    "15.0.0": f"{LEXICAL}/data/unicode15.json",
    "15.1.0": f"{LEXICAL}/data/unicode15_1.json",
    "16.0.0": f"{LEXICAL}/data/unicode16.json",
}
SELECTED_CODEPOINTS = [
    0x01F0,
    0x0130,
    0x00DF,
    0x0300,
    0x0307,
    0x0663,
    0x1C89,
    0x1C8A,
    0x1D7D8,
    0x105D2,
    0x1E4EC,
    0x20000,
]


def git_bytes(commit: str, path: str) -> bytes:
    return subprocess.run(  # noqa: S603 - fixed git argv, no shell
        ["git", "show", f"{commit}:{path}"],  # noqa: S607 - resolved via PATH deliberately
        check=True,
        capture_output=True,
    ).stdout


def identity(raw: bytes) -> dict[str, Any]:
    lf = raw.replace(b"\r\n", b"\n")
    return {
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "lf_bytes": len(lf),
        "lf_sha256": hashlib.sha256(lf).hexdigest(),
    }


def in_ranges(point: int, ranges: list[list[int]]) -> bool:
    return any(start <= point <= end for start, end in ranges)


def table_summary(raw: bytes) -> dict[str, Any]:
    data = json.loads(raw)
    selected = {}
    for point in SELECTED_CODEPOINTS:
        folded = data["casefold"].get(str(point))
        selected[f"U+{point:04X}"] = {
            "assigned": in_ranges(point, data["assigned_ranges"]),
            "word": in_ranges(point, data["word_ranges"]),
            "casefold_codepoints": [ord(character) for character in folded] if folded is not None else None,
        }
    return {
        "unicode": data["unicode"],
        "casefold_entries": len(data["casefold"]),
        "word_ranges": len(data["word_ranges"]),
        "assigned_ranges": len(data["assigned_ranges"]),
        "frequency_correction": data["frequency_correction"],
        "selected_codepoints": selected,
    }


def inventory(candidate: str) -> dict[str, Any]:
    generator_identity = identity(git_bytes(candidate, GENERATOR))
    lock_raw = git_bytes(candidate, f"{LEXICAL}/Cargo.lock")
    lock = tomllib.loads(lock_raw.decode("utf-8"))
    package_names = {"pyo3", "jieba-rs", "unicode-normalization", "serde", "serde_json", "sha2"}
    packages = [
        {key: package.get(key) for key in ("name", "version", "source", "checksum")}
        for package in lock["package"]
        if package["name"] in package_names
    ]
    tables = {}
    for version, path in TABLES.items():
        raw = git_bytes(candidate, path)
        blob_oid = subprocess.run(  # noqa: S603 - fixed git argv, no shell
            ["git", "rev-parse", f"{candidate}:{path}"],  # noqa: S607 - resolved via PATH deliberately
            check=True,
            text=True,
            capture_output=True,
        ).stdout.strip()
        tables[version] = {
            "path": path,
            "git_blob_oid": blob_oid,
            "git_blob": identity(raw),
            "summary": table_summary(raw),
        }
    sources = {}
    for path in (GENERATOR, f"{LEXICAL}/Cargo.toml", f"{LEXICAL}/src/lib.rs", f"{LEXICAL}/src/port.rs"):
        sources[path] = identity(git_bytes(candidate, path))
    return {
        "schema": "powercontext.unicode-contract.candidate-inventory.v1",
        "evidence_kind": "READ_ONLY_GIT_TABLES_AND_METADATA",
        "candidate": candidate,
        "native_execution": "NOT_RUN",
        "generator": generator_identity,
        "sources": sources,
        "lock_packages": sorted(packages, key=lambda package: package["name"]),
        "tables": tables,
        "table_total_bytes": sum(table["git_blob"]["bytes"] for table in tables.values()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    result = inventory(arguments.candidate)
    dump_json(arguments.output, result)
    print(f"inventoried {len(result['tables'])} fixed tables; {result['table_total_bytes']} bytes; native=NOT_RUN")


if __name__ == "__main__":
    main()
