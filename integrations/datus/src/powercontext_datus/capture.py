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


"""Native tool/driver evidence, full rows and conservative offline reconciliation.

Only the frozen in-process tool profile is certified. Tool wrappers are dispatch
spans; their SQL or nested operations are counted individually. No text-based
SQL deduplication and no timing-based action matching is used.
"""

# ruff: noqa: TRY003
from __future__ import annotations

import functools
import importlib
import json
import uuid
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from typing_extensions import override

from powercontext_datus.freeze import IntegrityError, digest_json
from powercontext_datus.observer import NativeTrace
from powercontext_datus.oracle import Table

_parent: ContextVar[str | None] = ContextVar("powercontext_operation", default=None)


def encode_cell(value: Any) -> Any:
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise IntegrityError("nonfinite database value")
        return {"decimal": str(value)}
    if value is None or type(value) in (str, int, float, bool):
        # Reject NaN and unsupported types; never silently stringify result data.
        json.dumps(value, allow_nan=False)
        return value
    raise IntegrityError("unsupported database cell; freeze an explicit conversion policy")


def table_from_json(value: dict[str, Any]) -> Table:
    def cell(value: Any) -> Any:
        if isinstance(value, dict) and set(value) == {"decimal"}:
            return Decimal(value["decimal"])
        return value

    return Table(tuple(value["columns"]), tuple(tuple(cell(v) for v in row) for row in value["rows"]))


class ExecutionTrace(NativeTrace):
    """Write-only pipe to the evaluator; the Agent receives no evidence path."""

    def __init__(self, *, stream: Any, run_id: str, task_id: str, attempt_id: str) -> None:
        super().__init__(None, stream=stream, run_id=run_id, task_id=task_id, attempt_id=attempt_id)
        self.online = False
        self._sql: dict[str, dict[str, Any]] = {}

    def emit(self, kind: str, **payload: Any) -> None:
        self._write(kind, payload)

    def start_question(self, question: str) -> None:
        self.online = True
        self.emit("question_injected", question_sha256=digest_json(question))

    @contextmanager
    def operation(self, name: str, inputs: Any, *, call_id: str | None = None):
        operation_id = str(uuid.uuid4())
        self.emit(
            "operation_started",
            operation_id=operation_id,
            parent_id=_parent.get(),
            name=name,
            inputs=inputs,
            call_id=call_id,
            online=self.online,
        )
        token = _parent.set(operation_id)
        try:
            yield operation_id
        except BaseException as error:
            self.emit(
                "operation_finished", operation_id=operation_id, status="failure", error_type=type(error).__name__
            )
            raise
        else:
            self.emit("operation_finished", operation_id=operation_id, status="success")
        finally:
            _parent.reset(token)

    def wrap_tool(self, tool: Any) -> Any:
        original = tool.on_invoke_tool

        @functools.wraps(original)
        async def invoke(context: Any, arguments: str) -> Any:
            call_id = getattr(context, "tool_call_id", None)
            with self.operation(tool.name, json.loads(arguments), call_id=call_id) as operation_id:
                result = await original(context, arguments)
                self.emit("tool_returned", operation_id=operation_id, value=result)
                return result

        tool.on_invoke_tool = invoke
        return tool

    @override
    def _before_sql(
        self, _conn: Any, _cursor: Any, statement: str, parameters: Any, context: Any, _executemany: bool
    ) -> None:
        super()._before_sql(_conn, _cursor, statement, parameters, context, _executemany)
        span_id = context._powercontext_span_id
        if _conn.engine.dialect.name == "mysql":
            _cursor._powercontext_engine_span = span_id
        self._sql[span_id] = {"rows": [], "complete": False}
        self.emit(
            "sql_link",
            driver_span_id=span_id,
            parent_id=_parent.get(),
            online=self.online,
            dialect=_conn.engine.dialect.name,
            executemany=_executemany,
        )

    @override
    def _after_sql(
        self, _conn: Any, _cursor: Any, _statement: str, _parameters: Any, context: Any, _executemany: bool
    ) -> None:
        super()._after_sql(_conn, _cursor, _statement, _parameters, context, _executemany)
        if _cursor.description is None:
            span = context._powercontext_span_id
            self._sql[span].update(columns=[], rows=[], complete=True)
            self.emit("sql_rows", driver_span_id=span, columns=[], rows=[], complete=True)
        if hasattr(_cursor, "_powercontext_engine_span"):
            del _cursor._powercontext_engine_span

    @override
    def _sql_error(self, context: Any) -> None:
        if getattr(context.execution_context, "_powercontext_span_id", None) is not None:
            super()._sql_error(context)
        else:
            # Connection-initialization SQL is recorded at the PyMySQL boundary.
            self.emit("engine_connection_error", error_type=type(context.original_exception).__name__)
        cursor = getattr(context.execution_context, "cursor", None)
        if cursor is not None and hasattr(cursor, "_powercontext_engine_span"):
            del cursor._powercontext_engine_span

    def _observe_fetch(self, cls: Any, name: str) -> None:
        original = getattr(cls, name)

        @functools.wraps(original)
        def fetch(result: Any, *args: Any, **kwargs: Any) -> Any:
            rows = original(result, *args, **kwargs)
            span = getattr(result.context, "_powercontext_span_id", None)
            if span in self._sql:
                values = ([rows] if rows is not None else []) if name == "_fetchone_impl" else rows
                columns = list(result.keys())
                encoded = [[encode_cell(v) for v in row] for row in values]
                complete = name == "_fetchall_impl" or not values
                state = self._sql[span]
                if not state["complete"]:
                    state["rows"].extend(encoded)
                    state.update(columns=columns, complete=complete)
                    self.emit("sql_rows", driver_span_id=span, columns=columns, rows=encoded, complete=complete)
            return rows

        setattr(cls, name, fetch)
        self._stack.callback(setattr, cls, name, original)

    def observe_skill_loads(self, manager: Any) -> None:
        original = manager.load_skill

        def load(*args: Any, **kwargs: Any) -> Any:
            # An explicitly dispatched load_skill is already an operation.
            if _parent.get() is not None:
                return original(*args, **kwargs)
            with self.operation("load_skill", {"name": args[0] if args else kwargs.get("skill_name")}):
                return original(*args, **kwargs)

        manager.load_skill = load
        self._stack.callback(setattr, manager, "load_skill", original)

    def observe_model_http(self, base_url: str) -> None:
        """Preserve each transport attempt, including SDK-internal retries.

        Credentials and headers never enter the sidecar. This is an HTTP
        destination gate for the pinned in-process clients, not a network jail.
        """
        httpx = importlib.import_module("httpx")
        allowed = urlsplit(base_url)

        def start(request: Any) -> str:
            target = urlsplit(str(request.url))
            if (target.scheme, target.netloc) != (allowed.scheme, allowed.netloc):
                self.emit("coverage_failure", reason="unexpected_http_destination")
                raise IntegrityError("HTTP destination differs from frozen model endpoint")
            attempt = str(uuid.uuid4())
            self.emit(
                "http_started", http_attempt_id=attempt, method=request.method, path=target.path, online=self.online
            )
            return attempt

        original_async = httpx.AsyncClient.send
        original_sync = httpx.Client.send

        async def send_async(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            attempt = start(request)
            try:
                response = await original_async(client, request, *args, **kwargs)
            except BaseException as error:
                self.emit("http_failure", http_attempt_id=attempt, error_type=type(error).__name__)
                raise
            self.emit("http_finished", http_attempt_id=attempt, status_code=response.status_code)
            return response

        def send_sync(client: Any, request: Any, *args: Any, **kwargs: Any) -> Any:
            attempt = start(request)
            try:
                response = original_sync(client, request, *args, **kwargs)
            except BaseException as error:
                self.emit("http_failure", http_attempt_id=attempt, error_type=type(error).__name__)
                raise
            self.emit("http_finished", http_attempt_id=attempt, status_code=response.status_code)
            return response

        httpx.AsyncClient.send = send_async
        httpx.Client.send = send_sync
        self._stack.callback(setattr, httpx.AsyncClient, "send", original_async)
        self._stack.callback(setattr, httpx.Client, "send", original_sync)

    @override
    def __enter__(self) -> ExecutionTrace:
        super().__enter__()
        try:
            from powercontext_datus.mysql_capture import observe_pymysql

            observe_pymysql(self)
            cls = importlib.import_module("sqlalchemy.engine").CursorResult
            for method in ("_fetchall_impl", "_fetchone_impl", "_fetchmany_impl"):
                self._observe_fetch(cls, method)
        except BaseException:
            self.__exit__(IntegrityError, None, None)
            raise
        return self


def _indexed(records: list[dict[str, Any]], kinds: set[str], key: str, issues: set[str]) -> dict[str, Any]:
    selected = [r for r in records if r["kind"] in kinds]
    result = {r[key]: r for r in selected}
    if len(result) != len(selected):
        issues.add("duplicate_" + key)
    return result


def _validate_capture(records: list[dict[str, Any]], issues: set[str]) -> None:
    if not records or [v.get("sequence") for v in records] != list(range(1, len(records) + 1)):
        issues.add("sequence")
    identities = {(v.get("run_id"), v.get("task_id"), v.get("attempt_id")) for v in records}
    if len(identities) != 1 or any(not all(identity) for identity in identities):
        issues.add("identity")
    markers = ("capture_started", "question_injected", "answer_submitted", "capture_finished")
    positions = [[i for i, r in enumerate(records) if r["kind"] == marker] for marker in markers]
    if any(len(p) != 1 for p in positions) or positions != sorted(positions):
        issues.add("capture_interval")
    if records and (records[-1]["kind"] != "capture_finished" or records[-1].get("interrupted")):
        issues.add("interrupted")
    issues.update(r["reason"] for r in records if r["kind"] == "coverage_failure")


def _check_native_actions(records: list[dict[str, Any]], starts: dict[str, Any], issues: set[str]) -> None:
    call_ids = [r["call_id"] for r in starts.values() if r["call_id"]]
    if len(call_ids) != len(set(call_ids)):
        issues.add("reused_native_call_id")
    actions = [r["action"] for r in records if r["kind"] == "action_received" and r["action"].get("role") == "tool"]
    native_ids = {r.get("action_id", "").removeprefix("complete_") for r in actions}
    if native_ids != set(call_ids):
        issues.add("unmatched_native_action")
    terminals = {
        r.get("action_id", "").removeprefix("complete_") for r in actions if r.get("status") in {"success", "failed"}
    }
    if terminals != set(call_ids):
        issues.add("unfinished_native_action")
    names = {r["call_id"]: r["name"] for r in starts.values() if r["call_id"]}
    if any(r.get("action_type") != names.get(r.get("action_id", "").removeprefix("complete_")) for r in actions):
        issues.add("native_action_identity_changed")


def _dispatch_operations(
    starts: dict[str, Any], finishes: dict[str, Any], returns: dict[str, Any], parents: set[str], issues: set[str]
) -> list[dict[str, Any]]:
    operations = []
    for op_id, start in starts.items():
        end = finishes.get(op_id)
        if end is None or end["sequence"] < start["sequence"]:
            issues.add("unfinished_operation")
        if start["parent_id"] is not None and start["parent_id"] not in starts:
            issues.add("unknown_parent")
        if op_id in parents:
            continue
        value = returns.get(op_id, {}).get("value")
        if isinstance(value, str):
            with suppress(ValueError):
                value = json.loads(value)
        failed = end is None or end["status"] != "success"
        failed |= isinstance(value, dict) and value.get("success") in (False, 0)
        operations.append({
            "operation_id": op_id,
            "name": start["name"],
            "failed": failed,
            "call_id": start["call_id"],
            "parent_id": start["parent_id"],
        })
    return operations


def _full_result(span: str, sql: str, records: list[dict[str, Any]], issues: set[str]) -> dict[str, Any] | None:
    chunks = [r for r in records if r["kind"] == "sql_rows" and r["driver_span_id"] == span]
    if not chunks or not chunks[-1]["complete"]:
        issues.add("incomplete_result")
        return None
    if any(c["columns"] != chunks[0]["columns"] for c in chunks) or any(c["complete"] for c in chunks[:-1]):
        issues.add("result_columns_or_lifecycle_changed")
        return None
    return {
        "operation_id": span,
        "sql": sql,
        "columns": chunks[0]["columns"],
        "rows": [row for c in chunks for row in c["rows"]],
    }


def _driver_operations(
    records: list[dict[str, Any]], starts: dict[str, Any], links: dict[str, Any], issues: set[str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sql = _indexed(records, {"sql_started"}, "driver_span_id", issues)
    terminals = _indexed(records, {"sql_success", "sql_failure"}, "driver_span_id", issues)
    operations, results = [], []
    online_sequence = next((r["sequence"] for r in records if r["kind"] == "question_injected"), float("inf"))
    if {span for span, r in sql.items() if r["sequence"] > online_sequence} != set(links):
        issues.add("unlinked_sql")
    for span, link in links.items():
        end = terminals.get(span)
        if span not in sql or end is None or end["sequence"] < sql[span]["sequence"]:
            issues.add("unfinished_sql")
            continue
        if link["dialect"] not in {"mysql", "sqlite"} or link["executemany"]:
            issues.add("unsupported_driver")
        if link["parent_id"] is not None and link["parent_id"] not in starts:
            issues.add("unknown_sql_parent")
        failed = end["kind"] == "sql_failure"
        operations.append({"operation_id": span, "name": "sql", "failed": failed, "parent_id": link["parent_id"]})
        if not failed:
            result = _full_result(span, sql[span]["sql"], records, issues)
            if result is not None:
                results.append(result)
    return operations, results


def _reconcile_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Certify only matching native dispatch/driver lifecycles over the whole interval."""
    issues: set[str] = set()
    _validate_capture(records, issues)
    starts = {k: r for k, r in _indexed(records, {"operation_started"}, "operation_id", issues).items() if r["online"]}
    finishes = _indexed(records, {"operation_finished"}, "operation_id", issues)
    links = {k: r for k, r in _indexed(records, {"sql_link"}, "driver_span_id", issues).items() if r["online"]}
    returns = _indexed(records, {"tool_returned"}, "operation_id", issues)
    _check_native_actions(records, starts, issues)
    _check_mysql_commands(records, starts, links, issues)
    parents = {r["parent_id"] for r in [*starts.values(), *links.values()] if r["parent_id"]}
    operations = _dispatch_operations(starts, finishes, returns, parents, issues)
    driver_ops, results = _driver_operations(records, starts, links, issues)
    operations.extend(driver_ops)
    if not operations:
        issues.add("no_operations")
    return {
        "steps": len(operations) if not issues else None,
        "trace_complete": not issues,
        "trace_issues": sorted(issues),
        "operations": operations,
        "sql_results": results,
    }


def _check_mysql_commands(
    records: list[dict[str, Any]], starts: dict[str, Any], links: dict[str, Any], issues: set[str]
) -> None:
    commands = _indexed(records, {"mysql_command_started"}, "command_id", issues)
    finishes = _indexed(records, {"mysql_command_finished"}, "command_id", issues)
    if set(commands) != set(finishes):
        issues.add("unfinished_mysql_command")
    sql_ends = _indexed(records, {"sql_success", "sql_failure"}, "driver_span_id", issues)
    op_ends = _indexed(records, {"operation_finished"}, "operation_id", issues)
    for identifier, command in commands.items():
        end = finishes.get(identifier)
        if end is None or end["sequence"] < command["sequence"] or end["status"] not in {"success", "failure"}:
            issues.add("unfinished_mysql_command")
            continue
        if not command["online"]:
            continue
        span = command["driver_span_id"]
        if span is not None:
            terminal = sql_ends.get(span)
            linked = span in links
            failed = terminal is not None and terminal["kind"] == "sql_failure"
        else:
            terminal = op_ends.get(command["operation_id"])
            linked = command["operation_id"] in starts
            failed = terminal is not None and terminal["status"] == "failure"
        if not linked or terminal is None or failed != (end["status"] == "failure"):
            issues.add("unlinked_mysql_command")


def reconcile(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Keep corrupt/incomplete sidecars in the denominator with unknown steps."""
    try:
        return _reconcile_records(records)
    except (KeyError, ValueError, TypeError):
        return {
            "steps": None,
            "trace_complete": False,
            "trace_issues": ["malformed_record"],
            "operations": [],
            "sql_results": [],
        }
