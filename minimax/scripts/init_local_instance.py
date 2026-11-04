"""Prepare a locally built seekdb instance so the guard can run against it.

``scripts/init_db.py`` manages the pinned Docker image. When you build seekdb from
source (to test a real C++ patch) you get a fresh instance with no accounts and no
databases, so the guard cannot connect. This script does the same provisioning against
any host/port:

    ./build/bin/src/observer/seekdb --nodaemon --base-dir ~/seekdb-patched --port 2882 &
    python scripts/init_local_instance.py --port 2882 --root-password ""
    MINIMAX_DB_PORT=2882 python run.py guard --stage after

It creates:
  * database ``minimax_scenario``      + account ``minimax_test`` (guard runs SQL there)
  * database ``minimax_sdd_ctx``       + account ``minimax_ctx``  (spec store / task state)
Both are idempotent; ``--reset`` drops them first.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pymysql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from context.db import DB_NAME  # noqa: E402

SCENARIO_DB = "minimax_scenario"
SCENARIO_USER = "minimax_test"
SCENARIO_PASSWORD = "minimax_test_dev"
CTX_USER = "minimax_ctx"
CTX_PASSWORD = "minimax_ctx_dev"


def connect(host: str, port: int, user: str, password: str, database: str | None = None):
    return pymysql.connect(
        host=host, port=port, user=user, password=password, database=database,
        charset="utf8mb4", autocommit=True, connect_timeout=10,
    )


def provision(host: str, port: int, root_password: str, reset: bool, spec_db: bool) -> None:
    conn = connect(host, port, "root", root_password)
    try:
        with conn.cursor() as cur:
            if reset:
                cur.execute(f"DROP DATABASE IF EXISTS {SCENARIO_DB}")
                if spec_db:
                    cur.execute(f"DROP DATABASE IF EXISTS {DB_NAME}")
                print(f"- dropped {SCENARIO_DB}" + (f" and {DB_NAME}" if spec_db else ""))

            cur.execute(f"CREATE DATABASE IF NOT EXISTS {SCENARIO_DB} DEFAULT CHARSET utf8mb4")
            cur.execute(
                f"CREATE USER IF NOT EXISTS '{SCENARIO_USER}'@'%' IDENTIFIED BY '{SCENARIO_PASSWORD}'"
            )
            cur.execute(f"GRANT ALL PRIVILEGES ON `{SCENARIO_DB}%`.* TO '{SCENARIO_USER}'@'%'")
            cur.execute(f"GRANT CREATE ON *.* TO '{SCENARIO_USER}'@'%'")
            print(f"- scenario: {SCENARIO_DB} / {SCENARIO_USER}")

            if spec_db:
                cur.execute(f"CREATE DATABASE IF NOT EXISTS {DB_NAME} DEFAULT CHARSET utf8mb4")
                cur.execute(
                    f"CREATE USER IF NOT EXISTS '{CTX_USER}'@'%' IDENTIFIED BY '{CTX_PASSWORD}'"
                )
                cur.execute(f"GRANT ALL PRIVILEGES ON {DB_NAME}.* TO '{CTX_USER}'@'%'")
                print(f"- spec store: {DB_NAME} / {CTX_USER}")
    finally:
        conn.close()


def verify(host: str, port: int, spec_db: bool) -> int:
    problems = 0
    for user, password, database in [
        (SCENARIO_USER, SCENARIO_PASSWORD, SCENARIO_DB),
        *([(CTX_USER, CTX_PASSWORD, DB_NAME)] if spec_db else []),
    ]:
        try:
            conn = connect(host, port, user, password, database)
            with conn.cursor() as cur:
                cur.execute("SELECT version(), CURRENT_USER(), DATABASE()")
                version, current_user, current_db = cur.fetchone()
            conn.close()
            print(f"  ok {user}@{host}:{port} -> {current_db} on {version} as {current_user}")
        except Exception as exc:  # noqa: BLE001 - report, do not mask
            problems += 1
            print(f"  FAIL {user}@{host}:{port} -> {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=2882)
    parser.add_argument("--root-password", default="",
                        help="root password of that instance (omit or use --root-password= when empty)")
    parser.add_argument("--reset", action="store_true", help="drop the databases first")
    parser.add_argument("--no-spec-db", action="store_true",
                        help="skip the spec store account (guard only needs the scenario DB)")
    args = parser.parse_args()

    spec_db = not args.no_spec_db
    print(f"target: root@{args.host}:{args.port}")
    try:
        provision(args.host, args.port, args.root_password, args.reset, spec_db)
    except Exception as exc:  # noqa: BLE001 - the first failure is the diagnosis
        print(f"provisioning failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        print("检查：实例是否已启动、root 口令是否正确（本地构建默认空口令）", file=sys.stderr)
        return 1

    problems = verify(args.host, args.port, spec_db)
    if problems:
        print(f"{problems} account(s) could not connect", file=sys.stderr)
        return 1
    print("ready: MINIMAX_DB_PORT={} python run.py guard --stage after".format(args.port))
    return 0


if __name__ == "__main__":
    sys.exit(main())
