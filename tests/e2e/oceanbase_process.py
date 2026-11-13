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

"""Bound native CLI phases and capture evidence before their inner deadlines."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path

from oceanbase_ci import collect, write_report

CLI_BOOTSTRAP = (
    "import faulthandler,signal; faulthandler.register(signal.SIGUSR1); from powercontext.server.cli import app; app()"
)


def _diagnose(process: subprocess.Popen[str] | subprocess.Popen[bytes], output: Path, remaining: float) -> None:
    # Only callers using the opt-in bootstrap may send this signal. It dumps
    # Python stacks without local variables; no background traceback watchdog.
    if process.poll() is None:
        with suppress(ProcessLookupError):
            process.send_signal(signal.SIGUSR1)
    container = os.environ.get("OCEANBASE_CI_CONTAINER")
    if container and remaining > 0:
        collect(container, output / "before-timeout", budget=min(20, remaining / 2))


class StartupDeadline:
    """A readiness polling deadline with one diagnostic snapshot before expiry."""

    def __init__(self, process: subprocess.Popen[str] | subprocess.Popen[bytes], output: Path, *, timeout: float):
        self.process = process
        self.output = output
        self.started = time.monotonic()
        self.deadline = self.started + timeout
        self.diagnostic_at = self.deadline - min(30, timeout / 2)
        self.captured = False

    def pending(self) -> bool:
        now = time.monotonic()
        if not self.captured and now >= self.diagnostic_at:
            self.captured = True
            _diagnose(self.process, self.output, self.deadline - now)
            write_report(self.output / "phase.json", {"phase": "startup", "elapsed_seconds": now - self.started})
        return time.monotonic() < self.deadline


def run_cli(
    command: list[str], *, env: dict[str, str], output: Path, timeout: float, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    """Preserve CLI JSON stdout and failure status, including TimeoutExpired."""
    started = time.monotonic()
    deadline = started + timeout
    stdout = stderr = ""
    timed_out = False
    cancelled_signal = None
    with subprocess.Popen(
        command, env=env, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as process:

        def cancel(number, _frame):
            nonlocal cancelled_signal
            cancelled_signal = number
            with suppress(ProcessLookupError):
                os.killpg(process.pid, number)
            # pytest treats SystemExit as an ordinary case failure and could
            # start another case after Actions cancellation. Interrupt the session.
            raise KeyboardInterrupt

        previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        for number in previous:
            signal.signal(number, cancel)
        try:
            try:
                stdout, stderr = process.communicate(timeout=timeout - min(30, timeout / 2))
            except subprocess.TimeoutExpired:
                _diagnose(process, output, deadline - time.monotonic())
                try:
                    stdout, stderr = process.communicate(timeout=max(0, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    timed_out = True
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
                    stdout, stderr = process.communicate(timeout=10)
                    raise subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr) from None
            return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
        finally:
            # Children can inherit captured pipes. Terminate the entire owned
            # group on timeout/cancellation, and reap the CLI before returning.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=10)
            for number, handler in previous.items():
                signal.signal(number, handler)
            write_report(
                output / "execution.json",
                {
                    "deadline_seconds": timeout,
                    "elapsed_seconds": time.monotonic() - started,
                    "exit_code": process.returncode,
                    "timed_out": timed_out,
                    "cancelled_signal": cancelled_signal,
                },
            )
            try:
                output.mkdir(parents=True, exist_ok=True)
                (output / "stdout.log").write_text(stdout)
                (output / "stderr.log").write_text(stderr)
            except OSError as error:
                print(f"CLI diagnostic output unavailable: {type(error).__name__}", file=sys.stderr)
