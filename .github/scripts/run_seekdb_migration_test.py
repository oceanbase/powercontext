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

"""Bound one native migration test and capture lock evidence before terminating it."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from contextlib import suppress
from pathlib import Path

_QUERIES = {
    "sessions": "SELECT ID, COMMAND, TIME, STATE FROM information_schema.PROCESSLIST LIMIT 100",
    "locks": "SELECT * FROM oceanbase.V$OB_LOCKS LIMIT 100",
    "transactions": "SELECT * FROM oceanbase.V$OB_TRANSACTION_SCHEDULERS LIMIT 100",
    "startup_jobs": "SELECT * FROM oceanbase.DBA_OB_ROOTSERVICE_JOBS LIMIT 100",
}


def archive_native_logs(root: Path, output: Path) -> None:
    # Preserve the complete available history, including rotated logs, rather
    # than dropping the transaction origin when lock-wait messages fill a tail.
    # Only archive logs from disposable test instances, never database files.
    with tarfile.open(output, "w:gz", compresslevel=1) as archive:
        for native in sorted(root.glob("pc-*/seekdb/log/*.log*")):
            if native.is_file() and not native.is_symlink():
                try:
                    archive.add(native, arcname=str(native.relative_to(root)), recursive=False)
                except FileNotFoundError:
                    # Fixture cleanup or log rotation can race collection.
                    print(f"Log disappeared during collection: {native}", flush=True)


def snapshot(root: Path) -> None:
    import pymysql

    # Connect to the existing socket directly. Opening a pylibseekdb lifecycle
    # handle here could restart the server or change the lifetime under test.
    for socket in sorted(root.glob("pc-*/seekdb/run/sql.sock"))[:4]:
        print(json.dumps({"socket": str(socket)}), flush=True)
        try:
            connection = pymysql.connect(
                unix_socket=str(socket),
                user="root",
                database="test",
                autocommit=True,
                connect_timeout=2,
                read_timeout=2,
                write_timeout=2,
            )
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SET SESSION ob_query_timeout = 2000000")
                    for name, query in _QUERIES.items():
                        try:
                            cursor.execute(query)
                        except pymysql.MySQLError as error:
                            print(json.dumps({"query": name, "error": type(error).__name__}), flush=True)
                            continue
                        print(
                            json.dumps(
                                {
                                    "query": name,
                                    "columns": [column[0] for column in cursor.description],
                                    "rows": cursor.fetchall(),
                                },
                                default=str,
                                ensure_ascii=False,
                            ),
                            flush=True,
                        )
            finally:
                connection.close()
        except pymysql.MySQLError as error:
            print(json.dumps({"error": type(error).__name__, "code": error.args[0]}), flush=True)


def stop(process: subprocess.Popen[bytes]) -> None:
    # Include native children in the test's process group when pytest cannot
    # reach fixture cleanup. Never kill other tests or an existing user server.
    with suppress(ProcessLookupError):
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def diagnostics(root: Path, output: Path) -> None:
    with output.open("a", encoding="utf-8") as log:
        probe = subprocess.Popen(  # noqa: S603 -- fixed interpreter and this checked-in diagnostic script
            [sys.executable, "-u", __file__, "--snapshot", str(root)],
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            probe.wait(timeout=10)
        except subprocess.TimeoutExpired:
            stop(probe)
            log.write("Database snapshot exceeded its diagnostic deadline.\n")
        # The holder may have acquired its lock during startup. A tail alone
        # loses that origin when the waiter emits minutes of repetitive logs.
        for native in sorted(root.glob("pc-*/seekdb/log/*.log*"))[:4]:
            if not native.is_file() or native.is_symlink():
                continue
            # The test may finish and remove its temporary database while the
            # snapshot is running; that must not turn a passing test red.
            with suppress(OSError):
                log.write(f"\nNative log: {native}\n")
                with native.open("rb") as source:
                    log.write(source.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
                    if native.stat().st_size > 2 * 1024 * 1024:
                        log.write("\n--- tail ---\n")
                        source.seek(max(source.tell(), native.stat().st_size - 2 * 1024 * 1024))
                        log.write(source.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--archive-native-logs", type=Path)
    parser.add_argument("--diagnose-after", type=float, default=90)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--diagnostics", type=Path, default=Path("seekdb-live-diagnostics.log"))
    parser.add_argument("node", nargs="?")
    args = parser.parse_args()
    if args.snapshot is not None:
        snapshot(args.snapshot)
        return 0
    if args.archive_native_logs is not None:
        archive_native_logs(Path(os.environ["TMPDIR"]), args.archive_native_logs)
        return 0
    if not args.node or not 0 < args.diagnose_after < args.timeout:
        parser.error("provide a pytest node and 0 < diagnose-after < timeout")
    root = Path(os.environ["TMPDIR"])
    started = time.monotonic()
    process = subprocess.Popen(  # noqa: S603 -- pytest node comes from CI collection, without a shell
        [
            sys.executable,
            "-m",
            "pytest",
            "-vv",
            "-o",
            "faulthandler_timeout=120",
            "--durations=20",
            "--log-cli-level=INFO",
            args.node,
        ],
        start_new_session=True,
    )
    try:
        try:
            return process.wait(timeout=args.diagnose_after)
        except subprocess.TimeoutExpired:
            print(f"Capturing seekdb lock diagnostics for {args.node}", flush=True)
            try:
                diagnostics(root, args.diagnostics)
            except OSError as error:
                print(f"Diagnostic capture failed: {type(error).__name__}", flush=True)
        try:
            return process.wait(timeout=max(0, args.timeout - (time.monotonic() - started)))
        except subprocess.TimeoutExpired:
            print(f"seekdb test exceeded {args.timeout:g}s: {args.node}", flush=True)
            return 124
    finally:
        if process.poll() is None:
            stop(process)


if __name__ == "__main__":
    raise SystemExit(main())
