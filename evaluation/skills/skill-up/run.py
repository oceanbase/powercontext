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

"""Run both Skill arms with a verified pin and retain qualification evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from report import native_path
from sync_skill import PROJECT, check
from validate_suite import validate


def execute(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=PROJECT, check=False, **kwargs)  # noqa: S603


def version(executable: str) -> str:
    result = execute([executable, "--version"], capture_output=True, text=True)
    if result.returncode:
        raise SystemExit(f"Cannot query version of {executable}")
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill-up", default="skill-up", help="path to skill-up v0.12.0")
    parser.add_argument("--output-dir", type=Path, help="new directory; existing results are never overwritten")
    parser.add_argument("--dry-run", action="store_true", help="validate and print the plan; no model/evidence claim")
    args = parser.parse_args()
    validate()
    pin = check()
    binary = shutil.which(args.skill_up)
    if binary is None:
        raise SystemExit("Install skill-up v0.12.0 or pass --skill-up /path/to/skill-up")
    binary = str(Path(binary).resolve())
    runner_version = version(binary)
    if runner_version != "skill-up version 0.12.0":
        raise SystemExit(f"Expected skill-up v0.12.0, got {runner_version}")
    config = str(PROJECT / "evals/eval.yaml")
    validation = execute([binary, "validate", config])
    if validation.returncode:
        return validation.returncode
    command = [binary, "run", config, "--baseline", "--iteration", "1"]
    if args.dry_run:
        return execute([*command, "--dry-run"]).returncode
    versions = {"skill_up": runner_version, "python": sys.version}
    for name in ("claude", "node", "python3", "bash", "git"):
        executable = shutil.which(name)
        if executable is None:
            raise SystemExit(f"Required executable missing: {name}; see README.md prerequisites")
        versions[name] = version(executable)
    output = args.output_dir or PROJECT / "results" / datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "skill-lock.json").write_text(json.dumps(pin, indent=2) + "\n", encoding="utf-8")
    inputs = sorted(
        path
        for directory in ("evals", "harness", "vendor")
        for path in (PROJECT / directory).rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )
    inputs += [
        PROJECT / name for name in ("run.py", "report.py", "sync_skill.py", "validate_suite.py", "skill-lock.json")
    ]
    input_hashes = {}
    for path in inputs:
        relative = path.relative_to(PROJECT)
        content = path.read_bytes()
        snapshot = native_path(output / "inputs" / relative)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        snapshot.write_bytes(content)
        input_hashes[relative.as_posix()] = hashlib.sha256(content).hexdigest()
    provenance = {
        "started_at": datetime.now(UTC).isoformat(),
        "versions": versions,
        "skill": pin,
        "input_sha256": input_hashes,
        "input_snapshot": "inputs/ contains the exact bytes hashed before execution",
        "host_shell": {name: os.environ.get(name) for name in ("SKILL_UP_BASH", "CLAUDE_CODE_GIT_BASH_PATH")},
        "profile_boundary": (
            "Fresh case workspaces inherit the active Claude user profile. "
            "User settings, native memory and globally configured Skills/MCP are not isolated."
        ),
        "mode": "mocked MCP; fixture Scope resolver; no real persistence or host approval evidence",
    }
    environment = dict(os.environ, CLAUDE_PLUGIN_ROOT=str(PROJECT / "harness/plugin-root"))
    result = execute([*command, "--output-dir", str(output)], env=environment)
    provenance["skill_up_exit_code"] = result.returncode
    provenance["finished_at"] = datetime.now(UTC).isoformat()
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
    # Preserve and report failed runs too. A baseline FAIL is data, but missing evidence is a gate failure.
    report = execute(
        [
            sys.executable,
            str(PROJECT / "report.py"),
            "--iteration",
            str(output / "iteration-1"),
            "--skill-lock",
            str(output / "skill-lock.json"),
            "--output-dir",
            str(output),
            "--engine-exit-code",
            str(result.returncode),
        ]
    )
    print(f"Evidence directory: {output}")
    return result.returncode or report.returncode


if __name__ == "__main__":
    raise SystemExit(main())
