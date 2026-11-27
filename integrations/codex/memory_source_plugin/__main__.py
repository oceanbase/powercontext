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

"""Preview a bounded selection of legacy Codex memory files before collection."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from pydantic import ValidationError

from .files import Config, MemoryInput, scan


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Preview legacy Codex memory files for Source collection")
    operations = result.add_subparsers(dest="operation", required=True)
    preview = operations.add_parser("preview")
    preview.add_argument("--codex-home", type=Path, default=Path(os.getenv("CODEX_HOME", "~/.codex")))
    preview.add_argument("--namespace", default="codex")
    preview.add_argument("--path", default=".")
    preview.add_argument("--max-file-bytes", type=int, default=262_144)
    preview.add_argument("--max-total-bytes", type=int, default=16_777_216)
    preview.add_argument("--max-files", type=int, default=1000)
    preview.add_argument("--max-entries", type=int, default=10_000)
    return result


def main() -> int:
    options = parser().parse_args()
    try:
        config = Config(**{
            key: getattr(options, key)
            for key in ("codex_home", "namespace", "max_file_bytes", "max_total_bytes", "max_files", "max_entries")
        })
        result = asyncio.run(asyncio.to_thread(scan, config, MemoryInput(path=options.path)))
    except (ValueError, OSError, ValidationError):
        print(json.dumps({"error": "configuration_invalid"}))
        return 1
    print(
        json.dumps(
            {
                "files": [{"path": item.relative_path, "source_id": item.snapshot_id()} for item in result.captures],
                "failures": [{"path": path, "reason": reason} for path, reason in result.failures],
                "skipped_empty": list(result.skipped),
            },
            ensure_ascii=False,
        )
    )
    return 1 if result.failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
