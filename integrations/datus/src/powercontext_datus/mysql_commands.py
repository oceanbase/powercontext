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

"""Observe command submission AND native acknowledgement, including control SQL."""

from __future__ import annotations

import functools
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

_scope: ContextVar[dict[str, Any] | None] = ContextVar("powercontext_mysql_command", default=None)


def finish(trace: Any, command: dict[str, Any], error: BaseException | None = None) -> None:
    if command["finished"]:
        return
    command["finished"] = True
    failed = error is not None
    trace.emit(
        "mysql_command_finished",
        command_id=command["id"],
        status="failure" if failed else "success",
        error_type=type(error).__name__ if failed else None,
    )
    if command["linked"]:
        # The enclosing query/cursor/engine already owns this SQL lifecycle.
        return
    if command["sql"]:
        trace.emit(
            "sql_failure" if failed else "sql_success",
            driver_span_id=command["id"],
            error_type=type(error).__name__ if failed else None,
        )
        if not failed:
            trace.emit("sql_rows", driver_span_id=command["id"], columns=[], rows=[], complete=True)
    else:
        trace.emit(
            "operation_finished",
            operation_id=command["id"],
            status="failure" if failed else "success",
            error_type=type(error).__name__ if failed else None,
        )


@contextmanager
def command_scope(trace: Any, connection: Any, *, query_span: str | None = None, method: str = "query"):
    state: dict[str, Any] = {"connection": connection, "query_span": query_span, "commands": []}
    method_id = str(uuid.uuid4())
    trace.emit("mysql_method_started", method_id=method_id, name=method, online=trace.online)
    token = _scope.set(state)
    try:
        yield
    except BaseException as error:
        trace.emit("mysql_method_finished", method_id=method_id, status="failure", error_type=type(error).__name__)
        for command in state["commands"]:
            finish(trace, command, error)
        raise
    else:
        trace.emit("mysql_method_finished", method_id=method_id, status="success")
        # For the pinned methods, returning means the reply was consumed. Send
        # success alone never proves this; read failures are captured separately.
        for command in state["commands"]:
            finish(trace, command)
    finally:
        if not state["commands"]:
            trace.emit("coverage_failure", reason="mysql_command_boundary_bypassed")
        _scope.reset(token)


def observe_commands(trace: Any, cls: Any) -> None:
    from pymysql.constants import COMMAND

    from powercontext_datus.capture import _parent

    original = cls._execute_command

    @functools.wraps(original)
    def execute(connection: Any, command: int, payload: Any) -> Any:
        state = _scope.get()
        scoped = state is not None and state["connection"] is connection
        pending = state["commands"] if scoped else []
        if not scoped or any(not c["finished"] for c in pending):
            trace.emit("coverage_failure", reason="unscoped_or_overlapping_mysql_command")
        linked = scoped and state["query_span"] is not None and not pending and command == COMMAND.COM_QUERY
        identifier = state["query_span"] if linked else str(uuid.uuid4())
        sql = command == COMMAND.COM_QUERY
        entry = {"id": identifier, "sql": sql, "linked": linked, "finished": False}
        pending.append(entry)
        # Non-SQL payloads may contain authentication data. Retain only command
        # type, identity, classification and lifecycle, never raw protocol bytes.
        classification = "sql" if sql else "connection_probe" if command == COMMAND.COM_PING else "session_control"
        trace.emit(
            "mysql_command_started",
            command_id=identifier,
            command_code=command,
            classification=classification,
            driver_span_id=identifier if sql else None,
            operation_id=None if sql else identifier,
            online=trace.online,
        )
        if not linked:
            if sql:
                statement = (
                    payload.decode(connection.encoding, errors="replace") if isinstance(payload, bytes) else payload
                )
                trace.emit("sql_started", driver_span_id=identifier, sql=statement, parameters=None)
                trace.emit(
                    "sql_link",
                    driver_span_id=identifier,
                    parent_id=_parent.get(),
                    online=trace.online,
                    dialect="mysql",
                    executemany=False,
                )
            else:
                trace.emit(
                    "operation_started",
                    operation_id=identifier,
                    parent_id=_parent.get(),
                    name="mysql_" + classification,
                    inputs={"command_code": command},
                    call_id=None,
                    online=trace.online,
                )
        if command not in {COMMAND.COM_QUERY, COMMAND.COM_PING, COMMAND.COM_INIT_DB}:
            trace.emit("coverage_failure", reason="unsupported_mysql_command")
        try:
            result = original(connection, command, payload)
        except BaseException as error:
            finish(trace, entry, error)
            raise
        trace.emit("mysql_command_sent", command_id=identifier)
        return result

    cls._execute_command = execute
    trace._stack.callback(setattr, cls, "_execute_command", original)
    for name in ("rollback", "commit", "begin", "_send_autocommit_mode", "set_character_set", "ping", "select_db"):
        observe_control(trace, cls, name)
    for name in ("_read_packet", "_read_ok_packet", "_read_query_result"):
        observe_read_failure(trace, cls, name)


def observe_control(trace: Any, cls: Any, name: str) -> None:
    original = getattr(cls, name)

    @functools.wraps(original)
    def control(connection: Any, *args: Any, **kwargs: Any) -> Any:
        with command_scope(trace, connection, method=name):
            return original(connection, *args, **kwargs)

    setattr(cls, name, control)
    trace._stack.callback(setattr, cls, name, original)


def observe_read_failure(trace: Any, cls: Any, name: str) -> None:
    original = getattr(cls, name)

    @functools.wraps(original)
    def read(connection: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return original(connection, *args, **kwargs)
        except BaseException as error:
            state = _scope.get()
            if state is not None and state["connection"] is connection:
                for command in state["commands"]:
                    finish(trace, command, error)
            trace.emit("mysql_read_failure", error_type=type(error).__name__)
            raise

    setattr(cls, name, read)
    trace._stack.callback(setattr, cls, name, original)
