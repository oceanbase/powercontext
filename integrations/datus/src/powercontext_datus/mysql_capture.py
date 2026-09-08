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


"""Observe native PyMySQL calls that bypass SQLAlchemy cursor events."""

from __future__ import annotations

import functools
import importlib
import uuid
from contextvars import ContextVar
from typing import Any

from powercontext_datus.mysql_commands import command_scope, observe_commands

_cursor_call: ContextVar[dict[str, Any] | None] = ContextVar("powercontext_mysql_cursor", default=None)


def observe_pymysql(trace: Any) -> None:
    # Lazy imports keep evaluator-only utilities independent of Datus packages.
    from powercontext_datus.capture import _parent

    cls: Any = importlib.import_module("pymysql.cursors").Cursor
    execute = cls.execute

    @functools.wraps(execute)
    def observed_execute(self: Any, query: Any, args: Any = None) -> Any:
        cursor = self
        linked = getattr(cursor, "_powercontext_engine_span", None)
        span = linked or str(uuid.uuid4())
        cursor._powercontext_raw_span = span
        cursor._powercontext_raw_linked = bool(linked)
        cursor._powercontext_raw_complete = False
        trace.emit("dbapi_execute", driver_span_id=span, engine_linked=bool(linked), sql=query, parameters=args)
        if not linked:
            trace.emit("sql_started", driver_span_id=span, sql=query, parameters=args)
            trace.emit(
                "sql_link",
                driver_span_id=span,
                parent_id=_parent.get(),
                online=trace.online,
                dialect="mysql",
                executemany=False,
            )
        token = _cursor_call.set({"span": span, "queries": 0})
        try:
            result = execute(cursor, query, args)
        except BaseException as error:
            if not linked:
                trace.emit("sql_failure", driver_span_id=span, error_type=type(error).__name__)
            raise
        finally:
            _cursor_call.reset(token)
        if not linked:
            trace.emit("sql_success", driver_span_id=span)
            if cursor.description is None:
                trace.emit("sql_rows", driver_span_id=span, columns=[], rows=[], complete=True)
                cursor._powercontext_raw_complete = True
        return result

    cls.execute = observed_execute
    trace._stack.callback(setattr, cls, "execute", execute)

    for name in ("fetchone", "fetchmany", "fetchall"):
        observe_fetch(trace, cls, name)
    observe_connection_query(trace)
    observe_commands(trace, importlib.import_module("pymysql.connections").Connection)


def observe_fetch(trace: Any, cls: Any, name: str) -> None:
    from powercontext_datus.capture import encode_cell

    original = getattr(cls, name)

    @functools.wraps(original)
    def fetch(cursor: Any, *args: Any, **kwargs: Any) -> Any:
        rows = original(cursor, *args, **kwargs)
        span = getattr(cursor, "_powercontext_raw_span", None)
        if (
            span
            and not cursor._powercontext_raw_linked
            and not cursor._powercontext_raw_complete
            and cursor.description is not None
        ):
            values = ([rows] if rows is not None else []) if name == "fetchone" else rows
            trace.emit(
                "sql_rows",
                driver_span_id=span,
                columns=[c[0] for c in cursor.description],
                rows=[[encode_cell(v) for v in row] for row in values],
                complete=name == "fetchall" or not values,
            )
            cursor._powercontext_raw_complete = name == "fetchall" or not values
        return rows

    setattr(cls, name, fetch)
    trace._stack.callback(setattr, cls, name, original)


def observe_connection_query(trace: Any) -> None:
    """Capture connection.query calls, including handshake SET statements.

    The first query inside a cursor execution shares its engine/cursor span.
    Further queries and calls made without a cursor are distinct operations.
    """
    from powercontext_datus.capture import _parent, encode_cell

    cls: Any = importlib.import_module("pymysql.connections").Connection
    original = cls.query

    @functools.wraps(original)
    def query(self: Any, sql: Any, unbuffered: bool = False) -> Any:
        call = _cursor_call.get()
        linked = call is not None and call["queries"] == 0
        span = call["span"] if linked else str(uuid.uuid4())
        if call is not None:
            call["queries"] += 1
        statement = sql.decode(self.encoding) if isinstance(sql, bytes) else sql
        trace.emit("dbapi_query", driver_span_id=span, cursor_linked=linked, sql=statement, unbuffered=unbuffered)
        if unbuffered:
            trace.emit("coverage_failure", reason="unbuffered_mysql_not_certified")
        if not linked:
            trace.emit("sql_started", driver_span_id=span, sql=statement, parameters=None)
            trace.emit(
                "sql_link",
                driver_span_id=span,
                parent_id=_parent.get(),
                online=trace.online,
                dialect="mysql",
                executemany=False,
            )
        try:
            with command_scope(trace, self, query_span=span):
                value = original(self, sql, unbuffered)
        except BaseException as error:
            if not linked:
                trace.emit("sql_failure", driver_span_id=span, error_type=type(error).__name__)
            raise
        if not linked:
            trace.emit("sql_success", driver_span_id=span)
            result = self._result
            if getattr(result, "has_next", False):
                trace.emit("coverage_failure", reason="multiple_mysql_results_not_certified")
            if not unbuffered and result is not None:
                trace.emit(
                    "sql_rows",
                    driver_span_id=span,
                    columns=[c[0] for c in (result.description or [])],
                    rows=[[encode_cell(v) for v in row] for row in (result.rows or [])],
                    complete=True,
                )
        return value

    cls.query = query
    trace._stack.callback(setattr, cls, "query", original)
