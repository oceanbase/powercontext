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

"""Capture native startup, shutdown and transaction evidence for a SeekDB migration stall."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import platform
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from types import ModuleType
from typing import Any
from xml.etree import ElementTree

import pytest

NODE = (
    "tests/builtin/persistence/test_mysql_migration_runner.py::"
    "test_mysql_committed_revision_recovers_before_evidence_advance[seekdb]"
)

SCENARIOS = {
    "migration": NODE,
    "restart-recovery": (
        "tests/e2e/test_seekdb_restart_recovery.py::test_metadata_inspection_survives_last_client_restarts"
    ),
    "fixture-recovery": (
        "tests/e2e/test_seekdb_restart_recovery.py::test_saved_fault_recovers_and_remains_usable_after_reopen"
    ),
}


def record(directory: Path, event: str, **values: object) -> None:
    with (directory / "lifecycle.jsonl").open("a") as output:
        output.write(
            json.dumps({"time": datetime.now(UTC).isoformat(), "event": event, **values}, ensure_ascii=False) + "\n"
        )


def pytest_sessionstart(session: pytest.Session) -> None:
    """Optional pytest plugin: observe only this subprocess's native instance lifetime."""
    from powercontext.builtin.persistence.seekdb import profile
    from tests.seekdb_support import RestartingInstance

    directory = Path(os.environ["POWERCONTEXT_SEEKDB_PROBE"])
    restart = os.environ.get("POWERCONTEXT_SEEKDB_REQUIRE_RESTART") == "1"
    original = profile._open_instance

    class ObservedInstance:
        def __init__(self, instance: Any, path: Path, native_pid: int) -> None:
            self.instance = instance
            self.path = path
            self.native_pid = native_pid

        def connection_options(self) -> Any:
            return self.instance.connection_options()

        def close(self) -> None:
            record(directory, "closing", path=str(self.path), native_pid=self.native_pid)
            try:
                self.instance.close()
                if restart:
                    record(directory, "native_exited", path=str(self.path), native_pid=self.native_pid)
            finally:
                record(directory, "close_returned", path=str(self.path), native_pid=self.native_pid)

    async def observed_open(module: ModuleType, path: Path) -> profile._SeekDBInstance:
        record(directory, "opening", path=str(path))
        instance = await original(module, path)
        try:
            native_pid = int((path / "run/seekdb.pid").read_text().strip())
        except (OSError, ValueError):
            instance.close()
            raise
        if restart:
            instance = RestartingInstance(instance, native_pid)
        options = dict(instance.connection_options())
        # Embedded scratch instance only. Never capture credentials or external connection settings.
        socket = options.get("unix_socket")
        (directory / "socket.json").write_text(json.dumps({"unix_socket": socket}))
        record(directory, "opened", path=str(path), native_pid=native_pid)
        return ObservedInstance(instance, path, native_pid)

    patch = pytest.MonkeyPatch()
    patch.setattr(profile, "_open_instance", observed_open)
    session.config.add_cleanup(patch.undo)


class NativeLogs:
    """Read through open file handles so rotation and fixture deletion retain early evidence."""

    def __init__(self, temporary: Path, output: Path) -> None:
        self.temporary = temporary
        self.output = output
        self.files: dict[tuple[int, int], tuple[Any, Any]] = {}

    def poll(self, *, drain: bool = False) -> None:
        for path in self.temporary.glob("pc-*/seekdb/log/*"):
            if not path.is_file() or ".log" not in path.name:
                continue
            try:
                source = path.open("rb")
                stat = os.fstat(source.fileno())
            except FileNotFoundError:
                continue
            identity = stat.st_dev, stat.st_ino
            if identity in self.files:
                source.close()
                continue
            name = f"native-{len(self.files):03d}-{path.name}.gz"
            destination = gzip.open(self.output / name, "wb", compresslevel=1)  # noqa: SIM115 -- retained across rotations
            self.files[identity] = source, destination
            record(self.output, "native_log", path=str(path), archive=name)
        for source, destination in self.files.values():
            remaining = 8
            while drain or remaining:
                data = source.read(1024 * 1024)
                if not data:
                    break
                destination.write(data)
                remaining -= 1

    def close(self) -> None:
        self.poll(drain=True)
        for source, destination in self.files.values():
            source.close()
            destination.close()


def snapshot(directory: Path) -> None:
    """Read live diagnostic views through the latest embedded Unix socket, never a remote endpoint."""
    import pymysql

    socket = json.loads((directory / "socket.json").read_text()).get("unix_socket")
    if not socket:
        raise RuntimeError("The probe did not publish an embedded Unix socket")  # noqa: TRY003
    queries = (
        "SELECT VERSION()",
        "SHOW FULL PROCESSLIST",
        "SELECT * FROM oceanbase.__all_virtual_trans_stat LIMIT 100",
        "SELECT * FROM oceanbase.__all_virtual_obj_lock LIMIT 100",
        "SELECT * FROM oceanbase.__all_virtual_lock_wait_stat LIMIT 100",
        "SELECT * FROM oceanbase.__all_virtual_trans_scheduler LIMIT 100",
    )
    for query in queries:
        try:
            # Reconnect after an unsupported view or a read timeout, preserving the other evidence.
            with (
                pymysql.connect(
                    unix_socket=socket,
                    user="root",
                    database="test",
                    autocommit=True,
                    connect_timeout=2,
                    read_timeout=2,
                    write_timeout=2,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute(query)
                print(
                    json.dumps({"query": query, "columns": cursor.description, "rows": cursor.fetchall()}, default=str),
                    flush=True,
                )
        except Exception as error:
            print(json.dumps({"query": query, "error": str(error)}), flush=True)


def successful_report(report: Path, code: int) -> bool:
    if code != 0 or not report.exists():
        return False
    cases = list(ElementTree.parse(report).iter("testcase"))  # noqa: S314 -- locally produced report
    return len(cases) == 1 and not any(
        case.find(tag) is not None for case in cases for tag in ("failure", "error", "skipped")
    )


def stop_process(process: subprocess.Popen[bytes]) -> None:
    """Bound shutdown of a probe child, including an unresponsive native client."""
    if process.poll() is None:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def restart_evidence(directory: Path) -> dict[str, Any]:
    """Require confirmed exit before each subsequent native generation opens."""
    lifecycle = directory / "lifecycle.jsonl"
    events = [json.loads(line) for line in lifecycle.read_text().splitlines()] if lifecycle.exists() else []
    processes: dict[str, list[int]] = {}
    running: dict[str, int] = {}
    valid = True
    for event in events:
        if event["event"] == "opened":
            path, pid = event["path"], event["native_pid"]
            pids = processes.setdefault(path, [])
            valid = valid and path not in running and pid > 0 and pid not in pids
            pids.append(pid)
            running[path] = pid
        elif event["event"] == "native_exited":
            path, pid = event["path"], event["native_pid"]
            valid = valid and running.get(path) == pid
            running.pop(path, None)
    passed = valid and not running and any(len(pids) > 1 for pids in processes.values())
    return {"passed": passed, "processes": processes}


def run_attempt(
    directory: Path,
    timeout: int,
    *,
    node: str = NODE,
    restart: bool = False,
    fixture: Path | None = None,
    keep_failed_storage: bool = False,
) -> bool:
    directory.mkdir(parents=True)
    temporary = directory / "tmp"
    temporary.mkdir()
    environment = dict(
        os.environ,
        TMPDIR=str(temporary),
        POWERCONTEXT_SEEKDB_PROBE=str(directory),
        POWERCONTEXT_SEEKDB_REQUIRE_RESTART=str(int(restart)),
        POWERCONTEXT_TEST_MIGRATION_SEEKDB="1",
        POWERCONTEXT_TEST_MIGRATION_SQL_LOG="1",
        POWERCONTEXT_TEST_SEEKDB_RESTART_RECOVERY="1",
        POWERCONTEXT_SEEKDB_RECOVERY_FIXTURE=str(fixture or ""),
        POWERCONTEXT_SEEKDB_KEEP_FAILED_STORAGE=str(int(keep_failed_storage)),
    )
    report = directory / "junit.xml"
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        "scripts.probe_seekdb_migration_lock",
        "-p",
        "no:cacheprovider",
        "-vv",
        "--log-cli-level=INFO",
        "-o",
        f"faulthandler_timeout={min(120, max(1, timeout // 2))}",
        f"--junitxml={report}",
        node,
    ]
    logs = NativeLogs(temporary, directory)
    snapshots: list[tuple[subprocess.Popen[bytes], Any, float]] = []
    started = time.monotonic()
    next_snapshot = 45.0
    timed_out = False
    process = None
    code = -1
    passed = False
    restarts = None
    try:
        with (directory / "pytest.log").open("w") as output:
            process = subprocess.Popen(  # noqa: S603
                command, env=environment, stdout=output, stderr=subprocess.STDOUT, start_new_session=True
            )
            while process.poll() is None:
                logs.poll()
                elapsed = time.monotonic() - started
                if elapsed >= next_snapshot:
                    log = (directory / f"snapshot-{int(next_snapshot)}.jsonl").open("w")
                    probe = subprocess.Popen(  # noqa: S603
                        [sys.executable, __file__, "--snapshot", str(directory)],
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        start_new_session=True,
                    )
                    snapshots.append((probe, log, time.monotonic()))
                    next_snapshot += 45
                for probe, _, created in snapshots:
                    if probe.poll() is None and time.monotonic() - created > 20:
                        probe.kill()
                if elapsed >= timeout:
                    timed_out = True
                    stop_process(process)
                    break
                time.sleep(0.1)
            code = process.wait()
        passed = not timed_out and successful_report(report, code)
        if restart and passed:
            restarts = restart_evidence(directory)
            passed = restarts["passed"]
    finally:
        if process is not None:
            stop_process(process)
        for probe, output, _ in snapshots:
            stop_process(probe)
            output.close()
        logs.close()
        # Only remove this attempt's disposable engine files, after preserving native logs.
        if not keep_failed_storage or passed:
            shutil.rmtree(temporary)
    result = {
        "passed": passed,
        "exit_code": code,
        "timed_out": timed_out,
        "seconds": time.monotonic() - started,
        "restarts": restarts,
    }
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    print(directory.name, json.dumps(result), flush=True)
    return passed


def positive_integer(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        message = "must be positive"
        raise argparse.ArgumentTypeError(message)
    return parsed


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--output", type=Path, default=Path(".powercontext/seekdb-lock-probe"))
    parser.add_argument("--attempts", type=positive_integer, default=12)
    parser.add_argument("--node", help="Exact pytest node, overriding the selected scenario.")
    parser.add_argument("--scenario", choices=SCENARIOS, default="migration")
    parser.add_argument(
        "--fixture",
        type=Path,
        help="Frozen synthetic baseline with the recovery test's sentinels; copied before opening.",
    )
    parser.add_argument(
        "--keep-failed-storage", action="store_true", help="Retain failed scratch storage for recovery acceptance."
    )
    parser.add_argument(
        "--restart", action="store_true", help="Wait for Linux native process exit before each reopen and verify it."
    )
    parser.add_argument("--timeout", type=positive_integer, default=180)
    arguments = parser.parse_args()
    if arguments.snapshot:
        return arguments
    if arguments.scenario == "fixture-recovery" and arguments.fixture is None:
        parser.error("--scenario fixture-recovery requires --fixture")
    if arguments.fixture is not None:
        arguments.fixture = arguments.fixture.resolve()
        if not (arguments.fixture / "etc/seekdb.data_version.bin").is_file():
            parser.error("--fixture must be a recognized native seekdb directory")
    node = arguments.node or SCENARIOS[arguments.scenario]
    directory = arguments.output.resolve()
    if arguments.fixture is not None and directory.is_relative_to(arguments.fixture):
        parser.error("--output must be outside the immutable --fixture directory")
    if directory.exists():
        parser.error("--output must be a new directory to preserve previous evidence")
    arguments.output = directory
    arguments.node = node
    return arguments


def main() -> int:
    arguments = parse_arguments()
    if arguments.snapshot:
        snapshot(arguments.snapshot)
        return 0
    directory = arguments.output
    node = arguments.node
    directory.mkdir(parents=True)
    binding = import_module("pylibseekdb")
    binding_file = binding.__file__
    if binding_file is None:
        message = "The SeekDB binding has no module file; cannot locate its native engine"
        raise RuntimeError(message)
    native_engine = Path(binding_file).with_name("seekdb")
    with native_engine.open("rb") as engine_file:
        native_digest = hashlib.file_digest(engine_file, "sha256").hexdigest()
    (directory / "environment.json").write_text(
        json.dumps(
            {
                "platform": platform.platform(),
                "python": sys.version,
                "pylibseekdb": version("pylibseekdb"),
                "seekdb_engine": str(native_engine),
                "seekdb_engine_sha256": native_digest,
                "node": node,
                "scenario": arguments.scenario,
                "restart": arguments.restart,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    for attempt in range(1, arguments.attempts + 1):
        attempt_directory = directory / f"attempt-{attempt:02d}"
        if not run_attempt(
            attempt_directory,
            arguments.timeout,
            node=node,
            restart=arguments.restart,
            fixture=arguments.fixture,
            keep_failed_storage=arguments.keep_failed_storage,
        ):
            return 1
        # Keep a complete successful baseline and every failure. Other successful
        # repeats retain their Python trace, lifecycle and test result only.
        if attempt > 1:
            for archive in attempt_directory.glob("native-*.gz"):
                archive.unlink()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
