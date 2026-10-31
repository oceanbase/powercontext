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

"""Validate every wheel installation role in a fresh environment on one runner."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROLES = ("core", "builtin", "cli", "builtin,cli", "server,cli")


def write_summary(output: Path, report: dict[str, Any]) -> None:
    (output / "summary.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    rows = ["## Isolated wheel installations", "", "| Role | Result | Stages |", "| --- | --- | --- |"]
    for role in report["roles"]:
        stages = ", ".join(f"{stage['name']}: {stage['status']}" for stage in role["stages"])
        rows.append(f"| `{role['extras']}` | {role['status']} | {stages} |")
    (output / "summary.md").write_text("\n".join(rows) + "\n", encoding="utf-8")


def run_roles(*, wheel: Path, python: Path, smoke_script: Path, output: Path, uv: str) -> int:
    # Refuse an existing directory so retries cannot inherit an earlier role's state.
    output.mkdir(parents=True, exist_ok=False)
    (output / "logs").mkdir()
    roles: list[dict[str, Any]] = [{"extras": extras, "status": "pending", "stages": []} for extras in ROLES]
    report: dict[str, Any] = {
        "python": str(python),
        "wheel": wheel.name,
        "status": "running",
        "roles": roles,
    }
    write_summary(output, report)
    for role in roles:
        extras = role["extras"]
        slug = extras.replace(",", "-")
        root = output / "work" / slug
        root.mkdir(parents=True)
        environment = dict(os.environ)
        # Share dependency downloads only; installed packages and application state stay per role.
        environment.setdefault("UV_CACHE_DIR", str(output / "uv-cache"))
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT"):
            environment.pop(name, None)
        for name in ("POWERCONTEXT_HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
            directory = root / name.lower()
            directory.mkdir()
            environment[name] = str(directory)
        temporary = root / "tmp"
        temporary.mkdir()
        environment.update(TMPDIR=str(temporary), TEMP=str(temporary), TMP=str(temporary), NO_COLOR="1")
        executable = root / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        requirement = str(wheel) if extras == "core" else f"{wheel}[{extras}]"
        commands = [
            ("create environment", [uv, "--no-config", "venv", "--python", str(python), str(root / "venv")]),
            ("install wheel", [uv, "--no-config", "pip", "install", "--python", str(executable), requirement]),
            ("role smoke", [str(executable), "-I", str(smoke_script), extras]),
        ]
        if extras == "cli":
            commands.extend([
                (
                    "install unrelated packages",
                    [uv, "--no-config", "pip", "install", "--python", str(executable), "sqlalchemy", "fastapi"],
                ),
                ("optional CLI isolation", [str(executable), "-I", str(smoke_script), extras]),
            ])
        stages: list[dict[str, Any]] = [{"name": name, "status": "pending"} for name, _ in commands]
        role["stages"] = stages
        role["status"] = "running"
        write_summary(output, report)
        log_path = output / "logs" / f"{slug}.log"
        with log_path.open("w", encoding="utf-8") as log:
            for stage, (name, command) in zip(stages, commands, strict=True):
                print(f"[{extras}] {name}", flush=True)
                log.write(f"\n=== {name} ===\n")
                log.flush()
                try:
                    result = subprocess.run(  # noqa: S603 -- fixed commands with explicit wheel/interpreter paths
                        command,
                        cwd=root,
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        check=False,
                        timeout=600,
                    )
                except (OSError, subprocess.TimeoutExpired) as error:
                    log.write(f"{type(error).__name__}: {error}\n")
                    stage["status"] = "failure"
                else:
                    stage["returncode"] = result.returncode
                    if result.returncode < 0:
                        raise SystemExit(128 - result.returncode)
                    stage["status"] = "success" if result.returncode == 0 else "failure"
                write_summary(output, report)
                if stage["status"] != "success":
                    break
        role["status"] = "success" if all(stage["status"] == "success" for stage in role["stages"]) else "failure"
        write_summary(output, report)
        print(f"[{extras}] {role['status']}; log: {log_path}", flush=True)
    report["status"] = "success" if all(role["status"] == "success" for role in report["roles"]) else "failure"
    write_summary(output, report)
    return 0 if report["status"] == "success" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--python", type=Path, default=Path(sys.executable))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--smoke-script", type=Path, default=Path(__file__).with_name("ci_role_smoke.py"))
    arguments = parser.parse_args()
    uv = shutil.which("uv")
    if uv is None:
        parser.error("uv must be installed on PATH")
    for value in (arguments.wheel, arguments.python, arguments.smoke_script):
        if not value.is_file():
            parser.error(f"Required file does not exist: {value}")
    return run_roles(
        wheel=arguments.wheel.resolve(),
        python=arguments.python.absolute(),
        smoke_script=arguments.smoke_script.resolve(),
        output=arguments.output_dir.resolve(),
        uv=uv,
    )


if __name__ == "__main__":
    raise SystemExit(main())
