#!/usr/bin/env python3
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

"""Preserve test-owned process logs beside the CI failure reports."""

import json
import os
import shutil
import tempfile
from pathlib import Path


def main() -> None:
    report = Path(os.environ["RUNNER_TEMP"]) / "pytest-diagnostics"
    report.mkdir(parents=True, exist_ok=True)
    (report / "sampling-stop").touch()
    retained = []
    for base in Path(tempfile.gettempdir()).glob("pytest-of-*"):
        for source in base.rglob("*"):
            if source.is_symlink() or not source.is_file():
                continue
            if not (
                source.name == "server.log"
                or source.name.endswith("-phases.jsonl")
                or (source.suffix == ".log" and source.parent.name == "logs")
            ):
                continue
            relative = Path(base.name) / source.relative_to(base)
            destination = report / "process-logs" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
            retained.append(str(relative))
    (report / "process-log-index.json").write_text(json.dumps(retained, indent=2) + "\n", encoding="utf-8")
    print(f"Retained {len(retained)} test process logs")


if __name__ == "__main__":
    main()
