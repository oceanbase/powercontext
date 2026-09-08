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

"""Opt-in raw native lifecycle/SQL observation for one isolated Datus process.

Use around question injection through final answer submission, never just around
GenSQL. Raw records are evidence, not automatically certified logical steps.
Store the sidecar outside all Agent-readable roots. This observer is not a sandbox.
"""

# ruff: noqa: TRY003 - precise, bounded validation errors.

from __future__ import annotations

import functools
import importlib
import json
import os
import time
import uuid
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from powercontext_datus.native import verify_runtime

_active = False


class NativeTrace:
    """Observe real Datus action mutations, including pre-rollback snapshots.

    SQLAlchemy cursor events cover hidden SQL too; repeated SQL gets a distinct
    driver span ID. Do not collapse those spans by SQL text. An evaluator still
    needs to correlate action and driver spans, classify non-tool setup traffic,
    and validate capture coverage before calling count_steps. Unsupported driver
    engines, child processes, and model usage are not covered by this observer.
    """

    def __init__(self, path: Path, *, run_id: str, task_id: str, attempt_id: str) -> None:
        if not all((run_id, task_id, attempt_id)):
            raise ValueError("run, task and attempt identifiers are required")
        self.path = path
        self.identity = {"run_id": run_id, "task_id": task_id, "attempt_id": attempt_id}
        self._stack = ExitStack()
        self._stream: Any = None
        self._sequence = 0
        self._used = False

    def _write(self, kind: str, payload: dict[str, Any]) -> None:
        self._sequence += 1
        self._stream.write(
            json.dumps(
                {
                    **self.identity,
                    "sequence": self._sequence,
                    "monotonic_ns": time.monotonic_ns(),
                    "kind": kind,
                    **payload,
                },
                ensure_ascii=False,
                allow_nan=False,
            )
            + "\n"
        )
        self._stream.flush()

    def _observe_method(self, manager_class: Any, name: str) -> None:
        original = getattr(manager_class, name)

        @functools.wraps(original)
        def observed(manager: Any, *args: Any, **kwargs: Any) -> Any:
            if name == "add_action":
                incoming = args[0] if args else kwargs["action"]
                # Native history ignores repeated IDs. Preserve the incoming
                # record even if native deduplication discards it.
                self._write("action_received", {"action": incoming.model_dump(mode="json")})
            before = [action.model_dump(mode="json") for action in manager.actions]
            self._write("actions_before", {"method": name, "actions": before})
            try:
                return original(manager, *args, **kwargs)
            finally:
                self._write(
                    "actions_after",
                    {
                        "method": name,
                        "actions": [action.model_dump(mode="json") for action in manager.actions],
                    },
                )

        setattr(manager_class, name, observed)
        self._stack.callback(setattr, manager_class, name, original)

    def _before_sql(
        self, _conn: Any, _cursor: Any, statement: str, parameters: Any, context: Any, _executemany: bool
    ) -> None:
        span_id = str(uuid.uuid4())
        context._powercontext_span_id = span_id
        # Preserve exact SQL/bind values. Unsupported serialization fails closed
        # rather than silently dropping a query from the evidence.
        self._write("sql_started", {"driver_span_id": span_id, "sql": statement, "parameters": parameters})

    def _after_sql(
        self, _conn: Any, _cursor: Any, _statement: str, _parameters: Any, context: Any, _executemany: bool
    ) -> None:
        self._write("sql_success", {"driver_span_id": context._powercontext_span_id})

    def _sql_error(self, context: Any) -> None:
        execution = context.execution_context
        self._write(
            "sql_failure",
            {
                "driver_span_id": getattr(execution, "_powercontext_span_id", None),
                "error_type": type(context.original_exception).__name__,
            },
        )

    def __enter__(self) -> NativeTrace:
        global _active
        if _active or self._used:
            raise ValueError("native observer requires one fresh recorder in one isolated process")
        verify_runtime()
        self._used = True
        _active = True
        try:
            descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            self._stream = self._stack.enter_context(os.fdopen(descriptor, "w", encoding="utf-8"))
            actions = importlib.import_module("datus.schemas.action_history")
            for name in ("add_action", "update_current_action", "update_action_by_id", "rollback_to", "clear"):
                self._observe_method(actions.ActionHistoryManager, name)
            sqlalchemy = importlib.import_module("sqlalchemy")
            events = importlib.import_module("sqlalchemy.event")
            for event, callback in (
                ("before_cursor_execute", self._before_sql),
                ("after_cursor_execute", self._after_sql),
                ("handle_error", self._sql_error),
            ):
                events.listen(sqlalchemy.engine.Engine, event, callback)
                self._stack.callback(events.remove, sqlalchemy.engine.Engine, event, callback)
            self._write("capture_started", {"datus_runtime": verify_runtime()})
        except BaseException:
            try:
                self._stack.close()
            finally:
                _active = False
            raise
        else:
            return self

    def __exit__(self, exc_type: Any, _error: Any, _traceback: Any) -> None:
        global _active
        try:
            self._write("capture_finished", {"interrupted": exc_type is not None})
        finally:
            try:
                self._stack.close()
            finally:
                _active = False
