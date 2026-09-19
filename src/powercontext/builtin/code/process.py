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

"""Bounded subprocesses shared by Git capture and the CodeGraph adapter."""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Mapping, Sequence
from pathlib import Path
from time import monotonic

from powercontext.builtin.code.errors import CodeUnavailableError


def process_environment() -> dict[str, str]:
    """Pass runtime essentials, excluding credentials and injected tool options."""

    names = ("PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL")
    result = {name: os.environ[name] for name in names if name in os.environ}
    result.update({
        "DO_NOT_TRACK": "1",
        "CODEGRAPH_TELEMETRY": "0",
        "CODEGRAPH_NO_WATCH": "1",
        "CODEGRAPH_NO_DAEMON": "1",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_NO_LAZY_FETCH": "1",
    })
    return result


async def run_process(
    arguments: Sequence[str],
    *,
    cwd: Path,
    deadline: float,
    max_output_bytes: int,
    stdin: bytes | None = None,
    environment: Mapping[str, str] | None = None,
) -> bytes:
    """Stop the complete process group on cancellation, timeout, or oversized output."""

    remaining = deadline - monotonic()
    if remaining <= 0:
        raise CodeUnavailableError("code_timeout")
    try:
        process = await asyncio.create_subprocess_exec(
            *arguments,
            cwd=cwd,
            env=process_environment() if environment is None else environment,
            stdin=asyncio.subprocess.DEVNULL if stdin is None else asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=os.name == "posix",
        )
    except OSError:
        raise CodeUnavailableError("code_process_unavailable") from None

    try:
        async with asyncio.timeout(max(0, deadline - monotonic())):
            return await _collect(process, stdin=stdin, max_output_bytes=max_output_bytes)
    except TimeoutError:
        raise CodeUnavailableError("code_timeout") from None
    except (BrokenPipeError, ConnectionResetError):
        raise CodeUnavailableError("code_process_failed") from None
    finally:
        await _terminate(process)


async def _collect(process: asyncio.subprocess.Process, *, stdin: bytes | None, max_output_bytes: int) -> bytes:
    if stdin is not None and process.stdin is not None:
        process.stdin.write(stdin)
        await process.stdin.drain()
        process.stdin.close()
    if process.stdout is None:
        raise CodeUnavailableError("code_process_unavailable")
    output = bytearray()
    while chunk := await process.stdout.read(65536):
        if len(output) + len(chunk) > max_output_bytes:
            raise CodeUnavailableError("code_output_limit")
        output.extend(chunk)
    if await process.wait() != 0:
        raise CodeUnavailableError("code_process_failed")
    return bytes(output)


async def _terminate(process: asyncio.subprocess.Process) -> None:
    try:
        if os.name == "posix":
            # A parent can exit while its descendants retain stdout or keep
            # writing files. The process group still belongs to this request.
            os.killpg(process.pid, signal.SIGKILL)
        elif process.returncode is None:
            process.kill()
    except ProcessLookupError:
        pass
    await process.wait()
