"""Database boundary: every read and write of the system goes through this module.

Connection parameters come only from the environment (``MINIMAX_DB_*``), never from
hard-coded hosts, so the same code runs against a local seekdb, a CI seekdb container,
or an OceanBase cluster.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from typing import Any, Iterator

import pymysql

DB_NAME = "minimax_sdd_ctx"

SCHEMA = """
CREATE TABLE IF NOT EXISTS spec (
  spec_id VARCHAR(32) NOT NULL PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  category VARCHAR(32) NOT NULL,
  kind VARCHAR(16) NOT NULL,              -- must-change | must-keep
  state VARCHAR(16) NOT NULL,             -- open | done
  statement TEXT NOT NULL,
  evidence TEXT NOT NULL,
  repro TEXT NOT NULL,
  tests VARCHAR(255) NOT NULL,            -- comma separated check names
  depends_on VARCHAR(255) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS spec_source (
  source_id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
  spec_id VARCHAR(32) NOT NULL,
  repo_path VARCHAR(512) NOT NULL,
  symbol VARCHAR(128) NOT NULL DEFAULT '',
  line_no INT NOT NULL DEFAULT 0,
  quote TEXT NOT NULL,
  note TEXT NOT NULL,
  captured_at DATETIME(6) NOT NULL,
  KEY idx_spec_source_spec (spec_id),
  KEY idx_spec_source_path (repo_path)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS test_case (
  case_id VARCHAR(64) NOT NULL PRIMARY KEY,
  spec_id VARCHAR(32) NOT NULL,
  check_name VARCHAR(64) NOT NULL,
  title VARCHAR(255) NOT NULL,
  oracle_key VARCHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_test_case_spec (spec_id)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS task (
  task_id VARCHAR(64) NOT NULL PRIMARY KEY,
  title VARCHAR(255) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  updated_at DATETIME(6) NOT NULL
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS task_state (
  task_id VARCHAR(64) NOT NULL PRIMARY KEY,
  revision INT NOT NULL,
  payload JSON NOT NULL,
  updated_at DATETIME(6) NOT NULL
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS task_state_history (
  history_id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  revision INT NOT NULL,
  payload JSON NOT NULL,
  reason VARCHAR(64) NOT NULL DEFAULT '',
  created_at DATETIME(6) NOT NULL,
  KEY idx_history_task_rev (task_id, revision)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS artifact (
  artifact_id VARCHAR(96) NOT NULL PRIMARY KEY,
  task_id VARCHAR(64) NOT NULL,
  kind VARCHAR(32) NOT NULL,
  content LONGTEXT NOT NULL,
  content_sha VARCHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_artifact_task (task_id),
  KEY idx_artifact_kind (kind)
) DEFAULT CHARSET = utf8mb4;

CREATE TABLE IF NOT EXISTS session_log (
  log_id BIGINT NOT NULL PRIMARY KEY AUTO_INCREMENT,
  task_id VARCHAR(64) NOT NULL,
  role VARCHAR(64) NOT NULL,
  action VARCHAR(64) NOT NULL,
  content LONGTEXT NOT NULL,
  created_at DATETIME(6) NOT NULL,
  KEY idx_session_log_task (task_id)
) DEFAULT CHARSET = utf8mb4;
"""


def db_config() -> dict[str, Any]:
    """Read the connection settings that ``scripts/init_db.py`` and the CLI share."""
    return {
        "host": os.environ.get("MINIMAX_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MINIMAX_DB_PORT", "2883")),
        "user": os.environ.get("MINIMAX_DB_USER", "minimax_ctx"),
        "password": os.environ.get("MINIMAX_DB_PASSWORD", "minimax_ctx_dev"),
        "database": os.environ.get("MINIMAX_DB_NAME", DB_NAME),
        "charset": "utf8mb4",
    }


class DatabaseUnavailable(RuntimeError):
    """The server or the account is not reachable; the message says what to do next."""


def connect(**overrides: Any) -> pymysql.connections.Connection:
    settings = db_config()
    settings.update(overrides)
    try:
        return pymysql.connect(
            host=settings["host"],
            port=settings["port"],
            user=settings["user"],
            password=settings["password"],
            database=settings["database"],
            charset=settings["charset"],
            autocommit=True,
            connect_timeout=10,
        )
    except pymysql.err.OperationalError as exc:
        code = exc.args[0] if exc.args else 0
        where = f"{settings['host']}:{settings['port']} as {settings['user']}"
        if code == 2003:
            hint = (
                "seekdb is not running on that port. Start it with\n"
                "  docker start minimax-context-seekdb      (existing container)\n"
                "  python scripts/init_db.py                (create/recreate it)\n"
                "  python scripts/init_local_instance.py --port <port>   (self-built server)\n"
                "or point MINIMAX_DB_PORT at the instance you do have."
            )
        elif code == 1045:
            hint = (
                "the account exists but the password was rejected. Re-run "
                "python scripts/init_db.py (or init_local_instance.py) to reset it, "
                "or export MINIMAX_DB_USER / MINIMAX_DB_PASSWORD."
            )
        elif code == 1049:
            hint = (
                f"database {settings['database']} is missing. Run "
                "python scripts/init_db.py --reset (or build_specs.py after creating it)."
            )
        else:
            hint = "check the server state and the MINIMAX_DB_* settings."
        raise DatabaseUnavailable(f"cannot connect to seekdb ({where}): {exc}\n{hint}") from exc


@contextmanager
def session(**overrides: Any) -> Iterator[pymysql.cursors.Cursor]:
    conn = connect(**overrides)
    try:
        with conn.cursor() as cur:
            yield cur
    finally:
        conn.close()


def execute_many(statements: list[str], **overrides: Any) -> None:
    conn = connect(**overrides)
    try:
        with conn.cursor() as cur:
            for statement in statements:
                cur.execute(statement)
    finally:
        conn.close()


def ensure_schema() -> None:
    """Create the schema if it is missing; safe to call on every start."""
    statements = [stmt.strip() for stmt in SCHEMA.split(";") if stmt.strip()]
    execute_many(statements)


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)
