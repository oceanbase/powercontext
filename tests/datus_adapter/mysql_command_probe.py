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

"""Native PyMySQL command serialization with synthetic responses, never live SQL."""

from __future__ import annotations

import io
import json
from contextlib import suppress
from types import SimpleNamespace
from typing import Any

from powercontext_datus.capture import ExecutionTrace, reconcile
from pymysql.connections import Connection
from pymysql.constants import COMMAND
from pymysql.err import OperationalError
from pymysql.protocol import MysqlPacket


def main():
    conn: Any = Connection(defer_connect=True)
    conn.server_status = 0
    conn._sock = object()
    sent = []
    fail_ack = False

    def write(packet):
        sent.append(packet[4])

    def packet(*args, **kwargs):
        if fail_ack:
            raise OperationalError(2013, "synthetic ACK failure")
        return MysqlPacket(b"\x00\x00\x00\x00\x00\x00\x00", conn.encoding)

    def query_result(**kwargs):
        conn._result = SimpleNamespace(
            description=(("value",),),
            rows=((9,),),
            unbuffered_active=False,
            has_next=False,
        )
        return 1

    # Keep the real command serializer and native transaction/SET/ping methods.
    conn._write_bytes = write
    conn._read_packet = packet
    conn._read_query_result = query_result
    stream = io.StringIO()
    with ExecutionTrace(stream=stream, run_id="component", task_id="commands", attempt_id="1") as trace:
        trace.start_question("Synthetic driver command coverage")
        conn.query("SELECT 9 AS value")
        conn.rollback()
        conn.ping(reconnect=False)
        conn.set_character_set("utf8mb4")
        conn.autocommit(True)
        fail_ack = True
        with suppress(OperationalError):
            conn.rollback()
        trace.emit("answer_submitted", answer="component only")
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    result = reconcile(records)
    assert sent == [
        COMMAND.COM_QUERY,
        COMMAND.COM_QUERY,
        COMMAND.COM_PING,
        COMMAND.COM_QUERY,
        COMMAND.COM_QUERY,
        COMMAND.COM_QUERY,
    ]
    commands = [r for r in records if r["kind"] == "mysql_command_started"]
    broken = (
        [
            r.copy()
            for r in records
            if not (r["kind"] == "mysql_command_finished" and r["command_id"] == commands[-1]["command_id"])
        ]
        if commands
        else records
    )
    for index, record in enumerate(broken, 1):
        record["sequence"] = index
    conn._sock = None
    print(
        json.dumps({
            "commands": commands,
            "reconciled": result,
            "missing_ack": reconcile(broken),
            "bypassed": bypassed(),
            "send_failure": send_failure(),
            "cursor": cursor_calls(),
        })
    )


def bypassed():
    conn: Any = Connection(defer_connect=True)
    conn._execute_command = lambda *args: None

    def fail():
        raise OperationalError(2013, "synthetic bypassed response")

    conn._read_ok_packet = fail
    stream = io.StringIO()
    with ExecutionTrace(stream=stream, run_id="fixture", task_id="bypass", attempt_id="1") as trace:
        trace.start_question("Synthetic overridden command boundary")
        with suppress(OperationalError):
            conn.rollback()
        trace.emit("answer_submitted", answer="component only")
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    assert any(r["kind"] == "mysql_method_finished" and r["status"] == "failure" for r in records)
    return reconcile(records)


def send_failure():
    conn: Any = Connection(defer_connect=True)
    # A closed native connection fails at the actual command boundary.
    stream = io.StringIO()
    with ExecutionTrace(stream=stream, run_id="fixture", task_id="closed", attempt_id="1") as trace:
        trace.start_question("Synthetic closed connection")
        with suppress(Exception):
            conn.rollback()
        trace.emit("answer_submitted", answer="component only")
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    return reconcile(records)


def cursor_calls():
    conn: Any = Connection(defer_connect=True)
    conn._sock = object()
    conn._write_bytes = lambda *args: None

    def read(**kwargs):
        conn._result = SimpleNamespace(
            description=(("value",),),
            rows=((9,),),
            affected_rows=1,
            warning_count=0,
            insert_id=0,
            unbuffered_active=False,
            has_next=False,
        )
        return 1

    conn._read_query_result = read
    stream = io.StringIO()
    with ExecutionTrace(stream=stream, run_id="fixture", task_id="cursor", attempt_id="1") as trace:
        trace.start_question("Repeated native cursor SQL attempts")
        with trace.operation("sql-wrapper", {}):
            cursor = conn.cursor()
            cursor.execute("SELECT 9 AS value")
            cursor.fetchall()
            cursor.execute("SELECT 9 AS value")
            cursor.fetchall()
        trace.emit("answer_submitted", answer="component only")
    conn._sock = None
    return reconcile([json.loads(line) for line in stream.getvalue().splitlines()])


if __name__ == "__main__":
    main()
