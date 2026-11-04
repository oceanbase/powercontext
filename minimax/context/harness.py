"""Environment harness: run SQL against a disposable seekdb instance.

The harness talks to a real server, records every statement with its result or error
code, and never rewrites a failure into a success.  It is deliberately independent of
the source tree so it can be pointed at the baseline build and at the patched build.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import pymysql

SCENARIO_DB = "minimax_scenario"


@dataclass
class SqlResult:
    statement: str
    ok: bool
    code: int
    message: str
    rows: list[list[Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "statement": self.statement,
            "ok": self.ok,
            "code": self.code,
            "message": self.message,
            "rows": self.rows,
        }


def connection_settings() -> dict[str, Any]:
    return {
        "host": os.environ.get("MINIMAX_DB_HOST", "127.0.0.1"),
        "port": int(os.environ.get("MINIMAX_DB_PORT", "2883")),
        "user": os.environ.get("MINIMAX_DB_USER", "minimax_ctx"),
        "password": os.environ.get("MINIMAX_DB_PASSWORD", "minimax_ctx_dev"),
        "database": os.environ.get("MINIMAX_SCENARIO_DB", SCENARIO_DB),
        "charset": "utf8mb4",
    }


def default_tests_path() -> Path:
    return Path(os.environ.get("MINIMAX_TESTS", Path(__file__).resolve().parents[1] / "tests"))


def load_suite(tests_path: Path) -> list[dict[str, Any]]:
    cases = []
    for path in sorted(tests_path.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        if "cases" not in payload:
            continue
        cases.extend(payload["cases"])
    return cases


def run_sql(statement: str, settings: dict[str, Any]) -> SqlResult:
    """Execute one statement in its own session so one error cannot hide the next."""
    try:
        conn = pymysql.connect(
            host=settings["host"],
            port=settings["port"],
            user=settings["user"],
            password=settings["password"],
            database=settings["database"],
            charset=settings["charset"],
            autocommit=True,
            connect_timeout=10,
        )
    except Exception as exc:  # noqa: BLE001 - connection failures are part of the result
        return SqlResult(statement, False, -1, f"connection failed: {exc}")
    try:
        with conn.cursor() as cur:
            cur.execute(statement)
            rows = [list(row) for row in cur.fetchall()] if cur.description else []
        return SqlResult(statement, True, 0, "", rows)
    except Exception as exc:  # noqa: BLE001 - SQL errors are data for the harness
        code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else -1
        message = exc.args[1] if len(exc.args) > 1 else str(exc)
        return SqlResult(statement, False, int(code), str(message))
    finally:
        conn.close()


def reset_scenario_database(settings: dict[str, Any]) -> None:
    """Create an empty scenario database so every check starts from a known state."""
    root = dict(settings)
    root.pop("database", None)
    conn = pymysql.connect(
        host=root["host"], port=root["port"], user=root["user"], password=root["password"],
        charset="utf8mb4", autocommit=True,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS {settings['database']}")
            cur.execute(f"CREATE DATABASE {settings['database']}")
    finally:
        conn.close()


def digest(case: dict[str, Any], results: Iterable[SqlResult]) -> str:
    payload = json.dumps(
        {
            "case": case["id"],
            "statements": [result.to_dict() for result in results],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def compare(expected: dict[str, Any], results: list[SqlResult]) -> tuple[bool, list[str]]:
    """Compare a case result with the golden oracle recorded from a reference run."""
    problems: list[str] = []
    want_codes = expected.get("codes", [])
    if len(want_codes) != len(results):
        problems.append(f"expected {len(want_codes)} statements, got {len(results)}")
    for index, (want, got) in enumerate(zip(want_codes, results)):
        ok, code = want[0], want[1]
        if bool(ok) != got.ok:
            problems.append(
                f"#{index}: expected {'success' if ok else f'error {code}'}, "
                f"got {'success' if got.ok else f'error {got.code} ({got.message})'}"
            )
        elif not ok and int(code) != got.code:
            problems.append(f"#{index}: expected error {code}, got error {got.code}")
    want_rows = expected.get("rows")
    if want_rows is not None:
        got_rows = [[str(value) if value is not None else None for value in row] for result in results
                    for row in result.rows]
        if want_rows != got_rows:
            problems.append(f"rows differ: expected {want_rows}, got {got_rows}")
    return (not problems), problems


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    """Run one case from a clean database and return its raw observations."""
    settings = connection_settings()
    started = time.time()
    reset_scenario_database(settings)
    results: list[SqlResult] = []
    for statement in case["sql"]:
        results.append(run_sql(statement, settings))
        if not results[-1].ok and case.get("stop_on_error", True):
            break
    return {
        "case_id": case["id"],
        "spec_id": case["spec_id"],
        "check": case["check"],
        "statements": [result.to_dict() for result in results],
        "result_digest": digest(case, results),
        "elapsed_ms": int((time.time() - started) * 1000),
        "server": f"{settings['host']}:{settings['port']}",
    }


def run_suite(tests_path: Path | None = None) -> list[dict[str, Any]]:
    cases = load_suite(tests_path or default_tests_path())
    return [run_case(case) for case in cases]
