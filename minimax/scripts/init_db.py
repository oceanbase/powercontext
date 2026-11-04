"""Bootstrap a disposable seekdb instance and its application account.

The competition submission must run from a clean state, so the bootstrap is fully
scripted: pull the pinned image, start the server with a known root password, then
create the system database and the two least-privilege accounts the system uses
(``minimax_ctx`` for stored specs and state, ``minimax_test`` for the scenario
database that the harness recreates on every run).

Usage:
    python scripts/init_db.py [--port 2883] [--reset] [--skip-docker]
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.db import DB_NAME, db_config  # noqa: E402

IMAGE = "oceanbase/seekdb:1.4.0-100000172026082615"
CONTAINER = "minimax-context-seekdb"
ROOT_PASSWORD = "minimax_root_pw"
APP_USER = "minimax_ctx"
APP_PASSWORD = "minimax_ctx_dev"
TEST_USER = "minimax_test"
TEST_PASSWORD = "minimax_test_dev"
TEST_DB_PREFIX = "minimax_scenario"


def run(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    print("+", " ".join(command), flush=True)
    return subprocess.run(command, text=True, capture_output=True, **kwargs)


def docker(*args: str) -> subprocess.CompletedProcess:
    return run(["docker", *args])


def wait_sql_ready(host: str, port: int, password: str, timeout: int = 180) -> None:
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            conn = pymysql.connect(
                host=host, port=port, user="root", password=password,
                charset="utf8mb4", autocommit=True, connect_timeout=5,
            )
            conn.close()
            return
        except Exception as exc:  # noqa: BLE001 - readiness probe
            last_error = exc
            time.sleep(2)
    raise RuntimeError(f"seekdb did not become ready on {host}:{port}: {last_error}")


def start_container(port: int) -> None:
    exists = docker("inspect", CONTAINER)
    if exists.returncode == 0:
        state = docker("inspect", "-f", "{{.State.Status}}", CONTAINER).stdout.strip()
        if state != "running":
            docker("start", CONTAINER)
    else:
        started = docker(
            "run", "-d", "--name", CONTAINER,
            "-e", f"ROOT_PASSWORD={ROOT_PASSWORD}",
            "-p", f"{port}:2881",
            IMAGE,
        )
        if started.returncode != 0:
            raise RuntimeError(f"failed to start seekdb container: {started.stderr}")
    wait_sql_ready("127.0.0.1", port, ROOT_PASSWORD)


def provision_database(host: str, port: int, reset: bool) -> None:
    if reset:
        conn = pymysql.connect(
            host=host, port=port, user="root", password=ROOT_PASSWORD,
            charset="utf8mb4", autocommit=True, connect_timeout=10,
        )
        try:
            with conn.cursor() as cur:
                cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
                print(f"- dropped database {DB_NAME}")
        finally:
            conn.close()
    auth = f"'{host}', {APP_USER}, {APP_PASSWORD}"
    conn = pymysql.connect(
        host=host, port=port, user="root", password=ROOT_PASSWORD,
        charset="utf8mb4", autocommit=True, connect_timeout=10,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} DEFAULT CHARSET utf8mb4")
            cur.execute(f"CREATE USER IF NOT EXISTS '{APP_USER}'@'%' IDENTIFIED BY '{APP_PASSWORD}'")
            cur.execute(f"GRANT ALL PRIVILEGES ON {DB_NAME}.* TO '{APP_USER}'@'%'")
            cur.execute(f"CREATE USER IF NOT EXISTS '{TEST_USER}'@'%' IDENTIFIED BY '{TEST_PASSWORD}'")
            cur.execute(f"GRANT ALL PRIVILEGES ON `{TEST_DB_PREFIX}%`.* TO '{TEST_USER}'@'%'")
            cur.execute(f"GRANT CREATE ON *.* TO '{TEST_USER}'@'%'")
            print(f"- database {DB_NAME} and accounts {APP_USER}/{TEST_USER} are ready")
    finally:
        conn.close()
    del auth


def verify_application_login() -> str:
    settings = db_config()
    conn = pymysql.connect(
        host=settings["host"], port=settings["port"], user=settings["user"],
        password=settings["password"], database=settings["database"],
        charset="utf8mb4", autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT version(), CURRENT_USER()")
            version, current_user = cur.fetchone()
        return f"{version} as {current_user}"
    finally:
        conn.close()


def verify_test_login() -> str:
    conn = pymysql.connect(
        host="127.0.0.1", port=int(os.environ.get("MINIMAX_DB_PORT", "2883")),
        user=TEST_USER, password=TEST_PASSWORD, charset="utf8mb4", autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute("CREATE DATABASE IF NOT EXISTS minimax_scenario_probe")
            cur.execute("DROP DATABASE minimax_scenario_probe")
            cur.execute("SELECT CURRENT_USER()")
            return str(cur.fetchone()[0])
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=int(os.environ.get("MINIMAX_DB_PORT", "2883")))
    parser.add_argument("--reset", action="store_true", help="drop the database before creating it")
    parser.add_argument("--skip-docker", action="store_true", help="server already running")
    args = parser.parse_args()
    os.environ["MINIMAX_DB_PORT"] = str(args.port)

    if not args.skip_docker:
        start_container(args.port)
    else:
        wait_sql_ready("127.0.0.1", args.port, ROOT_PASSWORD)
    provision_database("127.0.0.1", args.port, args.reset)

    print("verifying stored-context login ...", flush=True)
    print("connected:", verify_application_login())
    print("verifying scenario login ...", flush=True)
    print("connected:", verify_test_login())
    return 0


if __name__ == "__main__":
    sys.exit(main())
