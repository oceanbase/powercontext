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

"""Run the bounded matrix with explicit existing interpreters and locked project inputs."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from fixture_codec import dump_json

TASK_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = Path(__file__).resolve().parents[3]
CANDIDATE = "a774fd0b8b2ac1729f5dd9121c34e968dd980905"


def decode_capture(value: str | bytes | None) -> str | None:
    """Normalize captured output to text; TimeoutExpired yields bytes under text=True."""
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def execute(
    purpose: str,
    command: list[str],
    environment: dict[str, str],
    timeout: int = 300,
) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        completed = subprocess.run(  # noqa: S603 - fixed argv, no shell
            command,
            cwd=REPO_ROOT,
            env=environment,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout,
        )
        result = {
            "purpose": purpose,
            "command": command,
            "cwd": str(REPO_ROOT),
            "exit_code": completed.returncode,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "outcome": "EXECUTED_EXIT_0" if completed.returncode == 0 else "EXECUTED_NONZERO",
        }
    except subprocess.TimeoutExpired as error:
        # `TimeoutExpired` exposes raw bytes even under text=True; decode with the same
        # policy as the success path so a timeout still serializes as BLOCKED_TIMEOUT.
        result = {
            "purpose": purpose,
            "command": command,
            "cwd": str(REPO_ROOT),
            "exit_code": None,
            "stdout": decode_capture(error.stdout),
            "stderr": decode_capture(error.stderr),
            "outcome": "BLOCKED_TIMEOUT",
        }
    result["wall_seconds"] = time.perf_counter() - started
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--python", action="append", required=True, metavar="VERSION=EXISTING_PATH")
    parser.add_argument("--missing", action="append", default=[])
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--temp-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=TASK_ROOT / "evidence" / "matrix.json")
    arguments = parser.parse_args()
    arguments.temp_root.mkdir(parents=True, exist_ok=True)
    commands: list[dict[str, Any]] = []
    versions: dict[str, Any] = {}

    def run(
        version: str, purpose: str, command: list[str], environment: dict[str, str], timeout: int = 300
    ) -> dict[str, Any]:
        record = execute(purpose, command, environment, timeout)
        record["requested_python"] = version
        commands.append(record)
        dump_json(
            arguments.output,
            {
                "schema": "powercontext.unicode-contract.matrix-run.v1",
                "candidate": CANDIDATE,
                "native_execution": "NOT_RUN",
                "commands": commands,
                "versions": versions,
            },
        )
        return record

    base_environment = os.environ.copy()
    base_environment["PYTHONDONTWRITEBYTECODE"] = "1"
    inventory_output = TASK_ROOT / "evidence" / "candidate-inventory.json"
    inventory = run(
        "metadata",
        "candidate_inventory",
        [
            sys.executable,
            "-B",
            str(TASK_ROOT / "probes" / "candidate_inventory.py"),
            "--candidate",
            CANDIDATE,
            "--output",
            str(inventory_output),
        ],
        base_environment,
    )
    versions["metadata"] = {"candidate_inventory_exit_code": inventory["exit_code"], "native_execution": "NOT_RUN"}

    for specification in arguments.python:
        version, interpreter = specification.split("=", 1)
        environment = base_environment.copy()
        environment["UV_PROJECT_ENVIRONMENT"] = str(arguments.temp_root / f"env-{version.replace('.', '')}")
        versions[version] = {"requested_interpreter": interpreter, "status": "NOT_RUN", "steps": {}}
        sync = run(
            version,
            "locked_sync",
            [
                arguments.uv,
                "sync",
                "--locked",
                "--no-dev",
                "--group",
                "resource-build",
                "--extra",
                "builtin",
                "--python",
                interpreter,
            ],
            environment,
            timeout=900,
        )
        versions[version]["steps"]["locked_sync"] = sync["outcome"]
        environment_python = Path(environment["UV_PROJECT_ENVIRONMENT"]) / "Scripts" / "python.exe"
        if sync["exit_code"] != 0 or not environment_python.exists():
            versions[version]["status"] = "BLOCKED_LOCKED_ENVIRONMENT"
            continue

        common = [str(environment_python), "-B"]
        steps = [
            ("identity", [common[0], common[1], str(TASK_ROOT / "probes" / "pyver.py")], 60),
            (
                "dependency_resolution",
                [arguments.uv, "pip", "list", "--python", common[0], "--format=json"],
                60,
            ),
            (
                "record_public_entries",
                [
                    *common,
                    str(TASK_ROOT / "probes" / "contract_probe.py"),
                    "--fixtures",
                    str(TASK_ROOT / "fixtures" / "cases.json"),
                    "--output",
                    str(TASK_ROOT / "evidence" / f"cpython-{version}.json"),
                ],
                240,
            ),
            (
                "verify_public_entries",
                [
                    *common,
                    str(TASK_ROOT / "probes" / "contract_probe.py"),
                    "--fixtures",
                    str(TASK_ROOT / "fixtures" / "cases.json"),
                    "--output",
                    str(TASK_ROOT / "evidence" / f"cpython-{version}-replay.json"),
                    "--check",
                    str(TASK_ROOT / "evidence" / f"cpython-{version}.json"),
                ],
                240,
            ),
            (
                "fixed_generator",
                [
                    *common,
                    str(TASK_ROOT / "probes" / "generator_probe.py"),
                    "--candidate",
                    CANDIDATE,
                    "--output",
                    str(TASK_ROOT / "evidence" / f"generator-{version}.json"),
                    "--temp-root",
                    str(arguments.temp_root),
                ],
                300,
            ),
            (
                "negative_controls",
                [
                    *common,
                    str(TASK_ROOT / "probes" / "negative_controls.py"),
                    "--fixtures",
                    str(TASK_ROOT / "fixtures" / "cases.json"),
                    "--reference",
                    str(TASK_ROOT / "evidence" / f"cpython-{version}.json"),
                    "--output",
                    str(TASK_ROOT / "evidence" / f"negative-{version}.json"),
                    "--temp-root",
                    str(arguments.temp_root),
                ],
                360,
            ),
            (
                "existing_tests",
                [
                    *common,
                    "-m",
                    "pytest",
                    "tests/builtin/artifacts/test_text_search_admission.py",
                    "tests/builtin/artifacts/topic_memory/test_chunking.py",
                    "-q",
                    "-rs",
                ],
                300,
            ),
        ]
        all_zero = True
        for purpose, command, timeout in steps:
            record = run(version, purpose, command, environment, timeout)
            versions[version]["steps"][purpose] = record["outcome"]
            if record["exit_code"] != 0:
                all_zero = False
        versions[version]["status"] = "EXECUTED_ALL_COMMANDS_EXIT_0" if all_zero else "EXECUTED_WITH_FAILURES"

    for version in arguments.missing:
        record = run(
            version, "locate_existing_interpreter", [arguments.uv, "python", "find", version], base_environment, 60
        )
        versions[version] = {
            "requested_interpreter": None,
            "status": "BLOCKED_NO_EXISTING_INTERPRETER"
            if record["exit_code"] != 0
            else "UNEXPECTED_PATH_REVIEW_REQUIRED",
            "steps": {"locate_existing_interpreter": record["outcome"]},
            "behavior": "BLOCKED",
            "generator": "BLOCKED",
            "existing_tests": "BLOCKED",
            "native_execution": "NOT_RUN",
        }

    dump_json(
        arguments.output,
        {
            "schema": "powercontext.unicode-contract.matrix-run.v1",
            "candidate": CANDIDATE,
            "native_execution": "NOT_RUN",
            "commands": commands,
            "versions": versions,
        },
    )
    failures = [
        record for record in commands if record["purpose"] != "locate_existing_interpreter" and record["exit_code"] != 0
    ]
    print(f"recorded {len(commands)} commands; unexpected_nonzero={len(failures)}; native=NOT_RUN")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
