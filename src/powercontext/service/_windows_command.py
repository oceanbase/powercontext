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

"""Capture output from the Windows service's native command-line tools."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence


def run_windows_command(command: Sequence[str], *, timeout: float) -> subprocess.CompletedProcess[str]:
    """Run icacls, whoami, or schtasks with isolated console settings and strict decoding."""
    # A hidden console uses the system OEM code page rather than inheriting a
    # caller's chcp setting. Python's UTF-8 mode must not select the decoder.
    result = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        timeout=timeout,
        check=False,
    )
    try:
        # Decode in this thread: Windows subprocess reader-thread exceptions
        # otherwise leave stdout/stderr as None instead of reaching the caller.
        stdout = result.stdout.decode("oem")
        stderr = result.stderr.decode("oem")
    except (LookupError, UnicodeError) as error:
        # LookupError covers the OEM codec being unavailable off Windows, so a
        # misplaced call still reaches the caller as a command failure.
        raise subprocess.SubprocessError(  # noqa: TRY003
            f"cannot decode {command[0]} output using the Windows OEM code page"
        ) from error
    return subprocess.CompletedProcess(
        result.args,
        result.returncode,
        stdout.replace("\r\n", "\n").replace("\r", "\n"),
        stderr.replace("\r\n", "\n").replace("\r", "\n"),
    )
