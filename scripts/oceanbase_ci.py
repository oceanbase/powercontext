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

"""Bound startup and preserve diagnostics for disposable OceanBase CI containers."""

from __future__ import annotations

import argparse
import json
import math
import signal
import subprocess
import time
from pathlib import Path
from typing import IO


def sql(container: str, statement: str, *, database: str = "oceanbase", system: bool = False) -> list[str]:
    # Bound the client inside the container too: killing docker exec alone does
    # not guarantee that its remote process exits. These are disposable CI credentials.
    return [
        "docker",
        "exec",
        container,
        "timeout",
        "--kill-after=1s",
        "8s",
        "obclient",
        "-h127.0.0.1",
        "-P2881",
        "-uroot@sys" if system else "-uroot@test",
        *([] if system else ["-ppowercontext-e2e"]),
        "--connect-timeout=5",
        "--batch",
        "--skip-column-names",
        f"-D{database}",
        "--init-command=SET ob_query_timeout=5000000",
        "-e",
        statement,
    ]


def capture(command: list[str], path: Path, deadline: float, *, probe_timeout: float = 10) -> int:
    """Each probe has a local deadline within the caller's overall budget."""
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 124
    with path.open("w") as output:
        try:
            result = subprocess.run(  # noqa: S603 -- fixed CI commands, no shell
                command, stdout=output, stderr=subprocess.STDOUT, timeout=min(probe_timeout, remaining), check=False
            )
        except subprocess.TimeoutExpired:
            output.write("\nCI probe deadline exceeded.\n")
            return 124
        except OSError as error:
            output.write(f"\nCI probe unavailable: {type(error).__name__}\n")
            return 127
        else:
            return result.returncode


def collect(container: str, output: Path, *, budget: float = 30) -> None:
    """Best-effort bounded snapshots; omit statement text, row keys and container env."""
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    queries = {
        "sessions": "SELECT ID, USER, DB, COMMAND, TIME, STATE FROM information_schema.PROCESSLIST LIMIT 100",
        "locks": "SELECT TENANT_ID, TRANS_ID, TYPE, ID1, LMODE, REQUEST, CTIME, BLOCK FROM oceanbase.GV$OB_LOCKS LIMIT 100",
        "transactions": "SELECT TENANT_ID, SESSION_ID, TX_ID, STATE, ACTIVE_TIME, EXPIRE_TIME FROM oceanbase.GV$OB_TRANSACTION_SCHEDULERS LIMIT 100",
    }
    commands = {name: sql(container, query) for name, query in queries.items()}
    commands.update({
        "processes": ["docker", "top", container, "-eo", "pid,ppid,stat,etime,comm"],
        "resources": ["docker", "stats", "--no-stream", container],
        "disk": ["docker", "exec", container, "df", "-h", "/root/demo"],
        "state": ["docker", "inspect", "--format", "{{json .State}}", container],
    })
    results = {
        name: capture(command, output / f"{name}.txt", started + budget, probe_timeout=min(3, budget / len(commands)))
        for name, command in commands.items()
    }
    (output / "snapshot.json").write_text(
        json.dumps(
            {
                "captured_at_unix_seconds": time.time(),
                "elapsed_seconds": time.monotonic() - started,
                "exit_codes": results,
            },
            indent=2,
        )
        + "\n"
    )


def wait_ready(container: str, database: str, output: Path, timeout: float) -> int:
    """Wait for the tenant, configure capacity, and verify it within one deadline."""
    output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if capture(sql(container, "SELECT 1", database=database), output / "readiness.txt", deadline) == 0:
            break
        state = output / "state.txt"
        code = capture(["docker", "inspect", "--format", "{{.State.Running}}", container], state, deadline)
        if code == 0 and state.read_text().strip() == "false":
            print("OceanBase exited before becoming ready.", flush=True)
            return 1
        time.sleep(min(2, max(0, deadline - time.monotonic())))
    else:
        print(f"OceanBase did not become ready within {timeout:g} seconds.", flush=True)
        return 124

    statement = "ALTER SYSTEM SET datafile_maxsize='8G'; ALTER SYSTEM SET datafile_next='512M'; ALTER SYSTEM SET datafile_size='4G';"
    if capture(sql(container, statement, system=True), output / "configure.txt", deadline):
        return 1
    expected = {"datafile_size": "4G", "datafile_maxsize": "8G", "datafile_next": "512M"}
    parameters = output / "datafile.tsv"
    config_deadline = min(deadline, time.monotonic() + 60)
    while time.monotonic() < config_deadline:
        code = capture(
            sql(
                container,
                "SHOW PARAMETERS WHERE name IN ('datafile_size', 'datafile_maxsize', 'datafile_next');",
                system=True,
            ),
            parameters,
            config_deadline,
        )
        rows = [line.split("\t") for line in parameters.read_text().splitlines()] if parameters.exists() else []
        values = {row[4]: row[6] for row in rows if len(row) > 6}
        if code == 0 and all(values.get(key) == value for key, value in expected.items()):
            return capture(
                sql(
                    container,
                    "SET GLOBAL recyclebin=OFF; SELECT VERSION(); SHOW VARIABLES LIKE 'ob_compatibility_mode'; SHOW VARIABLES LIKE 'recyclebin';",
                    database=database,
                ),
                output / "engine.txt",
                deadline,
            )
        time.sleep(min(2, max(0, config_deadline - time.monotonic())))
    print("OceanBase datafile configuration did not become effective before its deadline.", flush=True)
    return 124


def run_command(
    command: list[str], *, container: str, output: Path, timeout: float, stdout: IO[str] | None = None
) -> int:
    """Keep GNU timeout's process deadline while collecting evidence before TERM."""
    output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    bounded = ["timeout", "--signal=TERM", "--kill-after=10s", f"{timeout:g}s", *command]
    cancelled_signal: int | None = None
    with subprocess.Popen(bounded, stdout=stdout, stderr=subprocess.STDOUT) as process:  # noqa: S603
        # Forward Actions cancellation to GNU timeout so its normal process-group
        # termination and kill-after grace remain effective through this wrapper.
        def forward_signal(number: int, _frame) -> None:
            nonlocal cancelled_signal
            cancelled_signal = number
            process.send_signal(number)

        previous = {number: signal.getsignal(number) for number in (signal.SIGINT, signal.SIGTERM)}
        for number in previous:
            signal.signal(number, forward_signal)
        try:
            try:
                code = process.wait(timeout=max(0, timeout - min(30, timeout / 2)))
            except subprocess.TimeoutExpired:
                # GNU timeout still enforces the original deadline even if a
                # diagnostic SQL probe is unresponsive. Collection never retries tests.
                collect(container, output / "before-timeout", budget=min(20, timeout / 3))
                code = process.wait()
        finally:
            for number, handler in previous.items():
                signal.signal(number, handler)
    if cancelled_signal is not None:
        code = 128 + cancelled_signal
    (output / "execution.json").write_text(
        json.dumps(
            {
                "exit_code": code,
                "cancelled_signal": cancelled_signal,
                "deadline_seconds": timeout,
                "elapsed_seconds": time.monotonic() - started,
            },
            indent=2,
        )
        + "\n"
    )
    return code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("wait", "collect", "run"))
    parser.add_argument("--container", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--database", default="powercontext_test")
    parser.add_argument("--timeout", type=float, default=600)
    # parse_known_args keeps the command after -- intact.
    args, command = parser.parse_known_args()
    if (
        not math.isfinite(args.timeout)
        or args.timeout <= 0
        or (command and (args.operation != "run" or command[0] != "--"))
    ):
        parser.error("A positive timeout and a command after -- (run only) are required")
    if args.operation == "run":
        if len(command) < 2:
            parser.error("run requires a command after --")
        return run_command(command[1:], container=args.container, output=args.output, timeout=args.timeout)
    if args.operation == "wait":
        code = wait_ready(args.container, args.database, args.output, args.timeout)
        if code:
            collect(args.container, args.output / "failure", budget=20)
        return code
    collect(args.container, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
