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

"""Native-library compatibility probe with synthetic actions, not a benchmark."""

import importlib
import json
import logging
import sys
from pathlib import Path

from powercontext_datus.observer import NativeTrace


def main():
    logging.disable(logging.CRITICAL)
    structlog = importlib.import_module("structlog")
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    actions = importlib.import_module("datus.schemas.action_history")
    sqlalchemy = importlib.import_module("sqlalchemy")
    sql_errors = importlib.import_module("sqlalchemy.exc")
    path = Path(sys.argv[1])
    original = actions.ActionHistoryManager.add_action
    engine = sqlalchemy.create_engine("sqlite://")
    try:
        with NativeTrace(path, run_id="component", task_id="not-qa", attempt_id="one"):
            manager = actions.ActionHistoryManager()
            action = actions.ActionHistory(
                action_id="first", role="tool", action_type="read_query", input={"sql": "SELECT 1"}, status="processing"
            )
            manager.add_action(action)
            manager.add_action(action.model_copy(update={"input": {"sql": "SELECT 2"}}))
            manager.update_action_by_id("first", status="failed", output={"error": "fixture"})
            manager.rollback_to(0)
            assert manager.get_actions() == []
            with engine.connect() as conn:
                for _ in range(2):
                    assert conn.execute(sqlalchemy.text("SELECT 1")).scalar_one() == 1
                try:
                    conn.execute(sqlalchemy.text("SELECT no_such_column"))
                except sql_errors.OperationalError:
                    pass
                else:
                    raise AssertionError("invalid query succeeded")  # noqa: TRY003
    finally:
        engine.dispose()
    assert actions.ActionHistoryManager.add_action is original
    try:
        with NativeTrace(path.with_name("aborted.jsonl"), run_id="component", task_id="interrupted", attempt_id="one"):
            raise ValueError("cancelled")  # noqa: TRY301 - exercise exception-driven observer teardown.
    except ValueError:
        pass
    assert actions.ActionHistoryManager.add_action is original
    print(json.dumps({"status": "passed", "native_agent_qa_runs": 0}))


if __name__ == "__main__":
    main()
