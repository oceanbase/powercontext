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


"""Synthetic driver probe: native PyMySQL cursor and real SQLite/SQLAlchemy execution."""

from __future__ import annotations

import importlib
import io
import json
from contextlib import suppress
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from powercontext_datus.capture import ExecutionTrace, reconcile
from pymysql.connections import Connection
from pymysql.cursors import Cursor
from pymysql.err import ProgrammingError
from sqlalchemy import create_engine, text

actions = importlib.import_module("datus.schemas.action_history")
ActionHistory = actions.ActionHistory
ActionHistoryManager = actions.ActionHistoryManager
ActionRole = actions.ActionRole
ActionStatus = actions.ActionStatus


class SyntheticCursor(Cursor):
    def _query(self, q):
        if q == "BROKEN":
            raise ProgrammingError("synthetic failure")  # noqa: TRY003 - deliberately injected driver failure
        self.description = (("value", 3, None, None, None, None, True),)
        self.rowcount = 1
        self.rownumber = 0
        self._rows = ((Decimal("1.00"),),)
        self._result = None


def main():
    stream = io.StringIO()
    with ExecutionTrace(stream=stream, run_id="fixture", task_id="driver", attempt_id="1") as trace:
        trace.start_question("Synthetic driver fixture")
        history = ActionHistoryManager()
        history.add_action(
            ActionHistory(
                action_id="call",
                role=ActionRole.TOOL,
                action_type="composite",
                messages="",
                input={},
                status=ActionStatus.PROCESSING,
            )
        )
        with trace.operation("composite", {}, call_id="call"):
            with trace.operation("nested-schema-operation", {}):
                engine = create_engine("sqlite://")
                with engine.connect() as connection:
                    result = connection.execute(text("SELECT 1 AS value UNION ALL SELECT 2"))
                    result.fetchmany(1)
                    result.fetchall()
                engine.dispose()
            cursor = SyntheticCursor(SimpleNamespace(_result=None))
            cursor.execute("SELECT 1.00")
            cursor.fetchone()
            cursor.fetchone()
            with suppress(ProgrammingError):
                cursor.execute("BROKEN")
            cursor.execute("SELECT 1.00")
            cursor.fetchall()
            # Exercise the real Connection.query method with a synthetic
            # transport, representing handshake SQL that creates no Cursor.
            connection: Any = Connection(defer_connect=True)
            connection._execute_command = lambda *args: None
            connection._read_query_result = lambda **kwargs: 1
            connection._result = SimpleNamespace(description=(("value",),), rows=((9,),))
            connection.query("SELECT 9 AS value")
        history.add_action(
            ActionHistory(
                action_id="complete_call",
                role=ActionRole.TOOL,
                action_type="composite",
                messages="",
                input={},
                status=ActionStatus.SUCCESS,
            )
        )
        trace.emit("answer_submitted", answer="component only")
    records = [json.loads(line) for line in stream.getvalue().splitlines()]
    result = reconcile(records)
    assert result["trace_complete"], result
    assert result["steps"] == 5, result
    assert sum(v["failed"] for v in result["operations"]) == 1
    assert result["sql_results"][0]["rows"] == [[1], [2]]
    assert result["sql_results"][1]["rows"] == [[{"decimal": "1.00"}]]
    assert result["sql_results"][2]["rows"] == [[{"decimal": "1.00"}]]
    assert result["sql_results"][3]["rows"] == [[9]]
    print(json.dumps({"evidence_kind": "synthetic_driver_component", "result": result}))


if __name__ == "__main__":
    main()
