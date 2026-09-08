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

"""Execute the tutorials in fresh Python kernels and retain inspectable results."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import nbformat
from jupyter_client import KernelManager
from nbclient import NotebookClient
from nbconvert.exporters.html import HTMLExporter

DIRECTORY = Path(__file__).resolve().parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-models", action="store_true", help="Run all lessons requiring real model providers.")
    parser.add_argument(
        "--with-http", action="store_true", help="Also run lesson 11 against a separately running HTTP Server."
    )
    parser.add_argument("--only", nargs="+", help="Lesson numbers, for example --only 01 05.")
    parser.add_argument("--with-browser", action="store_true", help="Run the real Chromium dashboard walkthrough.")
    parser.add_argument("--output-dir", type=Path, default=DIRECTORY / ".powercontext" / "executed")
    parser.add_argument("--timeout", type=int, default=240, help="Per-cell deadline in seconds.")
    args = parser.parse_args()
    paths = sorted(DIRECTORY.glob("[0-9A][0-9]_*.ipynb"))
    known = {path.name[:2] for path in paths}
    if args.only and not set(args.only) <= known:
        parser.error("--only expects lesson identifiers such as 01, 22, or A1.")
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    environment = dict(os.environ)
    environment["JUPYTER_RUNTIME_DIR"] = str(output / "runtime")
    environment["IPYTHONDIR"] = str(output / "ipython")
    feature_options = {
        "generation": ("--with-models", args.with_models),
        "embedding": ("--with-models", args.with_models),
        "chat": ("--with-models", args.with_models),
        "http_server": ("--with-http", args.with_http),
        "browser": ("--with-browser", args.with_browser),
    }
    records = []
    for path in paths:
        if args.only and path.name[:2] not in args.only:
            continue
        notebook = nbformat.read(path, as_version=4)
        nbformat.validate(notebook)
        requires = notebook.metadata.get("powercontext", {}).get("requires", [])
        missing = sorted({feature_options[feature][0] for feature in requires if not feature_options[feature][1]})
        code_cells = [cell for cell in notebook.cells if cell.cell_type == "code"]
        record = {
            "notebook": path.name,
            "requires": requires,
            "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "code_sha256": hashlib.sha256(
                json.dumps([cell.source for cell in code_cells], ensure_ascii=False).encode("utf-8")
            ).hexdigest(),
            "code_cells": len(code_cells),
            "executed_cells": 0,
            "support_sha256": {
                str(support.relative_to(DIRECTORY)): hashlib.sha256(support.read_bytes()).hexdigest()
                for support in [DIRECTORY / "_tutorial.py", *sorted((DIRECTORY / "support").glob("*"))]
                if support.is_file()
            },
        }
        if missing:
            record["status"] = "not_run"
            records.append(record)
            print(f"NOT RUN {path.name}: requires {' and '.join(missing)} and {', '.join(requires)}", flush=True)
            continue
        print(f"RUN {path.name}", flush=True)
        started = time.monotonic()
        manager = KernelManager(kernel_name="python3")
        spec = manager.kernel_spec
        if spec is None:
            parser.error("Python kernel is missing; install the notebooks dependency group.")
        spec.argv = [sys.executable, "-m", "ipykernel_launcher", "-f", "{connection_file}"]
        executor = NotebookClient(
            notebook,
            km=manager,
            timeout=args.timeout,
            startup_timeout=30,
            resources={"metadata": {"path": str(DIRECTORY)}},
        )
        try:
            executor.execute(env=environment)
        except Exception as error:
            record.update(status="failed", error_type=type(error).__name__)
        else:
            record["status"] = "passed"
        finally:
            # An explicitly supplied manager is owned by this runner, including on a failed cell.
            if manager.has_kernel:
                manager.shutdown_kernel(now=True)
            manager.cleanup_resources()
        record["seconds"] = round(time.monotonic() - started, 2)
        record["executed_cells"] = sum(cell.execution_count is not None for cell in code_cells)
        nbformat.write(notebook, output / path.name)
        html, _ = HTMLExporter().from_notebook_node(notebook)
        (output / path.with_suffix(".html").name).write_text(html, encoding="utf-8")
        records.append(record)
        print(f"{str(record['status']).upper()} {path.name} ({record['seconds']}s)", flush=True)
        (output / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (output / "summary.json").write_text(json.dumps(records, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    counts = {status: sum(row["status"] == status for row in records) for status in ("passed", "failed", "not_run")}
    print(json.dumps(counts), flush=True)
    return 1 if counts["failed"] or (args.only and counts["not_run"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
