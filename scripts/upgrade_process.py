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

"""Own one Linux upgrade command and reap its descendants, including new sessions."""

import argparse
import ctypes
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def own_descendants(expected_parent: int) -> None:
    if sys.platform != "linux":
        raise RuntimeError("Installed-release acceptance requires Linux")  # noqa: TRY003
    # A separate, single-threaded supervisor cannot adopt unrelated gate/test
    # children. setsid() does not escape Linux subreaper ancestry.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if libc.prctl(1, signal.SIGTERM, 0, 0, 0) != 0:  # PR_SET_PDEATHSIG
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    if os.getppid() != expected_parent:
        raise RuntimeError("The gate exited before process ownership was established")  # noqa: TRY003
    signal.signal(signal.SIGCHLD, signal.SIG_DFL)


def children() -> list[int]:
    return [int(value) for value in Path(f"/proc/self/task/{os.getpid()}/children").read_text().split()]


def reap_finished(exclude: int | None = None) -> set[int]:
    reaped = set()
    for pid in children():
        if pid != exclude and os.waitpid(pid, os.WNOHANG)[0]:
            reaped.add(pid)
    return reaped


def wait_command(process: subprocess.Popen[bytes], timeout: float) -> int:
    deadline = time.monotonic() + timeout
    while (result := process.poll()) is None:
        # A live command may wait for a double-forked daemon to disappear.
        # Reap already-exited adoptees without disturbing any live descendants.
        reap_finished(exclude=process.pid)
        if time.monotonic() >= deadline:
            raise subprocess.TimeoutExpired(process.args, timeout)
        time.sleep(0.02)
    return result


def reap_descendants(shutdown_timeout: float) -> None:
    deadline = time.monotonic() + shutdown_timeout
    notified = {}
    while True:
        for pid in reap_finished():
            notified.pop(pid, None)
        owned = children()
        if not owned:
            return
        requested = signal.SIGTERM if time.monotonic() < deadline else signal.SIGKILL
        for pid in owned:
            if notified.get(pid) != requested:
                # These are our direct children, never a global PID scan. No
                # wait/reap happens between ownership inspection and signalling,
                # so even an exited child's PID cannot be reused here.
                os.kill(pid, requested)
                notified[pid] = requested
        # Killing a parent can adopt another detached generation. Keep owning
        # and reaping it before returning, rather than leaving it to init. As
        # with Popen.wait(), SIGKILL still waits for kernel-level exit/reaping;
        # an uninterruptible kernel wait must not admit the next case early.
        time.sleep(0.02)


def supervise(command: list[str], timeout: float, shutdown_timeout: float, expected_parent: int) -> int:
    def interrupted(_signal, _frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, interrupted)
    own_descendants(expected_parent)
    process = None
    try:
        process = subprocess.Popen(command)  # noqa: S603 -- fixed gate command, no shell
        try:
            result = wait_command(process, timeout)
        except subprocess.TimeoutExpired:
            print(f"Upgrade command exceeded {timeout:g} seconds; closing owned processes", file=sys.stderr)
            result = 124
        except KeyboardInterrupt:
            result = 130
    finally:
        # A repeated cancellation must not interrupt ownership cleanup.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        if process is not None and process.poll() is None:
            # Let pytest unwind fixtures and let the Server close SQL sessions
            # and seekDB normally before forcing termination.
            process.send_signal(signal.SIGINT)
            try:
                wait_command(process, shutdown_timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        reap_descendants(shutdown_timeout)
    return result if result >= 0 else 128 - result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--timeout", type=float, required=True)
    parser.add_argument("--shutdown-timeout", type=float, default=30)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command or args.timeout <= 0 or args.shutdown_timeout <= 0:
        parser.error("A command and positive time limits are required")
    return supervise(command, args.timeout, args.shutdown_timeout, args.parent_pid)


if __name__ == "__main__":
    raise SystemExit(main())
