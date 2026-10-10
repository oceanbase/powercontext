# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Parent-enforced deadlines and Darwin memory budgets for isolated parser batches."""

from __future__ import annotations

import os
import selectors
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from powercontext.builtin.code.capture import check_deadline, json_bytes, write_private
from powercontext.builtin.code.errors import CodeError
from powercontext.builtin.code.extract import text_facts


def _progress(line: bytes, count: int) -> tuple[bytes, int]:
    try:
        action, number = line.split(b":", 1)
        index = int(number)
    except ValueError as error:
        raise CodeError("code_parser_failed") from error
    if not 0 <= index < count:
        raise CodeError("code_parser_failed")
    return action, index


def _budget_failure(pid: int, remaining: float, memory_bytes: int) -> str | None:
    if remaining <= 0:
        return "parse_timeout"
    if sys.platform != "darwin":
        return None
    import psutil

    try:
        if psutil.Process(pid).memory_info().rss > memory_bytes:
            return "memory_limit"
    except psutil.NoSuchProcess:
        # Drain buffered progress after exit before deciding whether the batch failed.
        return None
    except psutil.Error as error:
        raise CodeError("code_parser_failed") from error
    return None


def _monitor(
    process: subprocess.Popen[bytes], count: int, deadline: float, parse_seconds: float, memory_bytes: int
) -> tuple[set[int], int | None, str, bytes]:
    completed: set[int] = set()
    current = None
    expires = min(deadline, time.monotonic() + 10)
    buffer = b""
    if process.stdout is None:
        raise CodeError("code_parser_failed")
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        while True:
            check_deadline(deadline)
            remaining = expires - time.monotonic()
            if reason := _budget_failure(process.pid, remaining, memory_bytes):
                return completed, current, reason, buffer
            if not selector.select(min(0.1, remaining)):
                continue
            data = os.read(process.stdout.fileno(), 4096)
            if not data:
                return completed, current, "parser_crash", buffer
            buffer += data
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                action, index = _progress(line, count)
                if action == b"begin":
                    current = index
                    expires = min(deadline, time.monotonic() + parse_seconds)
                elif action == b"end" and current == index:
                    completed.add(index)
                    current = None
                    expires = min(deadline, time.monotonic() + 10)
                    if len(completed) == count:
                        return completed, None, "complete", buffer
                else:
                    raise CodeError("code_parser_failed")


def _batch(
    job_file: Path, count: int, deadline: float, parse_seconds: float, memory_bytes: int
) -> tuple[set[int], int | None, str]:
    environment = {key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONSTARTUP"}}
    with subprocess.Popen(  # noqa: S603 - fixed module and service-owned job manifest.
        [sys.executable, "-m", "powercontext.builtin.code.worker", str(job_file)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=job_file.parent,
        env=environment,
    ) as process:
        result = None
        try:
            result = _monitor(process, count, deadline, parse_seconds, memory_bytes)
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if result is not None and result[2] == "memory_limit" and process.stdout is not None:
                trailing = result[3] + process.stdout.read()
                completed, current = _reconcile_progress(trailing, count, result[0], result[1])
                result = completed, current, result[2], b""
    if result is None:
        raise CodeError("code_parser_failed")
    return result[:3]


def _reconcile_progress(
    data: bytes, count: int, completed: set[int], current: int | None
) -> tuple[set[int], int | None]:
    """Account for progress already written before a worker is terminated."""

    while b"\n" in data:
        line, data = data.split(b"\n", 1)
        action, index = _progress(line, count)
        if action == b"begin":
            current = index
        elif action == b"end" and current == index:
            completed.add(index)
            current = None
        else:
            raise CodeError("code_parser_failed")
    return completed, current


def extract_jobs(
    staging: Path, jobs: list[dict[str, Any]], deadline: float, *, memory_bytes: int, parse_seconds: float
) -> None:
    pending = jobs
    job_file = staging / "jobs.json"
    while pending:
        check_deadline(deadline)
        job_file.unlink(missing_ok=True)
        write_private(
            job_file, json_bytes({"memory_bytes": memory_bytes, "parse_seconds": parse_seconds, "files": pending})
        )
        complete, failed, reason = _batch(job_file, len(pending), deadline, parse_seconds, memory_bytes)
        if failed is not None:
            entry = pending[failed]
            content = (staging / "source" / entry["sha256"]).read_bytes()
            facts = text_facts(entry["path"], content)
            facts["nodes"][0].update(language=entry["language"], parse_status="failed")
            facts["errors"] = [{"reason": reason, "line": 1}]
            destination = staging / "facts" / f"{entry['extraction_key']}.json"
            destination.unlink(missing_ok=True)
            write_private(destination, json_bytes(facts))
            complete.add(failed)
        if not complete:
            raise CodeError("code_parser_failed")
        pending = [entry for index, entry in enumerate(pending) if index not in complete]
    job_file.unlink()
