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


"""Linux per-question mount/process isolation. No host home, proc or evaluator mount."""

# ruff: noqa: TRY003, S603 - executable and argv are explicitly constructed, never a shell.
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from powercontext_datus.execution import cpuinfo_file, execution_identity
from powercontext_datus.freeze import IntegrityError, snapshot, verify_snapshot


@dataclass(frozen=True)
class Sandbox:
    python: Path
    bridge: Path
    execution: str | None = None

    def __post_init__(self) -> None:
        # The interpreter prefix is mounted as well as its venv. A system
        # interpreter would silently reintroduce a broad /usr or / root mount.
        if self.python.resolve().parent.parent in {Path("/"), Path("/usr"), Path("/usr/local")}:
            raise IntegrityError("a dedicated standalone Python prefix is required")

    def execution_identity(self) -> dict[str, Any]:
        return execution_identity(json.loads(self.execution) if self.execution is not None else None)

    def bind_execution(self, identity: dict[str, Any]) -> Sandbox:
        # A serialized value avoids retaining a mutable caller-owned manifest.
        return replace(self, execution=json.dumps(identity, sort_keys=True))

    def command(
        self,
        *,
        common: Path,
        skills: Path,
        database: Path | None = None,
        network: bool = False,
        module: str = "powercontext_datus.worker",
        cpuinfo_fd: int,
    ) -> list[str]:
        execution = self.execution_identity()
        binary = execution["launcher"]["resolved"]
        python = self.python.absolute()
        if not python.is_file() or not (python.parent.parent / "pyvenv.cfg").is_file():
            raise IntegrityError("a locked Datus virtual environment is required")
        base = python.resolve().parent.parent
        command = [binary, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL"]
        if network:
            command.append("--share-net")
        for destination, root in execution["mounts"].items():
            if root is not None:
                command += ["--ro-bind", root["resolved"], destination]
        command += ["--ro-bind-data", str(cpuinfo_fd), "/proc/cpuinfo"]
        command += [
            "--ro-bind",
            str(base),
            str(base),
            "--ro-bind",
            str(python.parent.parent),
            "/runtime",
            "--ro-bind",
            str(self.bridge.resolve()),
            "/bridge",
            "--ro-bind",
            str(common.resolve()),
            "/inputs/common.txt",
            "--ro-bind",
            str(skills.resolve()),
            "/skills",
            "--tmpfs",
            "/work",
            "--dir",
            "/work/home",
            "--tmpfs",
            "/tmp",  # noqa: S108 - a fresh private tmpfs, not the host temporary directory.
            "--dev",
            "/dev",
            "--chdir",
            "/work",
            "--setenv",
            "HOME",
            "/work/home",
            "--setenv",
            "PATH",
            "/runtime/bin:/usr/bin:/bin",
            "--setenv",
            "PYTHONPATH",
            "/bridge",
            "--setenv",
            "PYTHONDONTWRITEBYTECODE",
            "1",
            "--setenv",
            "LANG",
            "C.UTF-8",
            "--setenv",
            "PYTHONPYCACHEPREFIX",
            "/work/pycache",
            "--setenv",
            "OPENAI_AGENTS_DISABLE_TRACING",
            "1",
            "--setenv",
            "LITELLM_LOCAL_MODEL_COST_MAP",
            "True",
            "--setenv",
            "TOKENIZERS_PARALLELISM",
            "false",
        ]
        if database is not None:
            if database.is_symlink() or not database.is_file():
                raise IntegrityError("database snapshot must be a regular file")
            command += ["--ro-bind", str(database.resolve()), "/inputs/database.sqlite"]
        return [*command, "/runtime/bin/python", "-m", module]

    def run(
        self,
        request: dict[str, Any],
        *,
        common: Path,
        skills: Path,
        database: Path | None = None,
        network: bool = False,
        timeout: float = 120,
        module: str = "powercontext_datus.worker",
    ) -> dict[str, Any]:
        expected = self.execution_identity() if self.execution is None else json.loads(self.execution)
        pinned = self.bind_execution(expected)
        before = snapshot(skills)
        common_before = common.read_bytes()
        start = time.monotonic()
        try:
            with cpuinfo_file(expected["cpuinfo"]) as cpuinfo_fd:
                command = pinned.command(
                    common=common,
                    skills=skills,
                    database=database,
                    network=network,
                    module=module,
                    cpuinfo_fd=cpuinfo_fd,
                )
                result = subprocess.run(
                    command,
                    input=json.dumps(request),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    env={"PATH": os.defpath},
                    pass_fds=(cpuinfo_fd,),
                    check=False,
                )
            status, stdout, stderr = result.returncode, result.stdout, result.stderr
        except subprocess.TimeoutExpired as error:
            # subprocess.run kills and reaps bwrap; its PID namespace kills its
            # descendants. Preserve partial evidence, including unfinished attempts.
            status = None
            stdout = error.stdout or b""
            stderr = error.stderr or b""
            stdout = stdout.decode(errors="replace") if isinstance(stdout, bytes) else stdout
            stderr = stderr.decode(errors="replace") if isinstance(stderr, bytes) else stderr
        records = []
        malformed = False
        for line in stdout.splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                malformed = True
                continue
            if isinstance(record, dict) and {"kind", "sequence", "run_id", "task_id", "attempt_id"} <= record.keys():
                records.append(record)
            else:
                malformed = True
        # Third-party stderr can contain sensitive connector exception messages.
        # Keep only the presence/size; structured trace preserves bounded failures.
        run = {
            "returncode": status,
            "records": records,
            "stdout": stdout,
            "malformed_output": malformed,
            "stderr_bytes": len(stderr.encode()),
            "timeout": status is None,
            "wall_seconds": time.monotonic() - start,
            "process_started": True,
            "not_started": False,
            "state_valid": True,
        }
        # Validation cannot erase an already-launched process or its trace.
        # Keep even timeout/partial output, then invalidate the evidence.
        try:
            pinned.execution_identity()
            verify_snapshot(skills, before)
            if common.read_bytes() != common_before:
                run.update(state_valid=False, control_failure="leakage/state_drift")
        except (IntegrityError, OSError) as error:
            run.update(
                state_valid=False, control_failure="leakage/state_drift", validation_error_type=type(error).__name__
            )
        return run
