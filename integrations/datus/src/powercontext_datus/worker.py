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


"""Private sandbox worker. Input is one question and approved public configuration."""

# ruff: noqa: TRY003
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any

from powercontext_datus.capture import ExecutionTrace
from powercontext_datus.freeze import IntegrityError
from powercontext_datus.workflow import build_graph, run_graph

ANSWER_PROTOCOL = (
    "Return the native GenSQL JSON envelope with sql and output. "
    'The entire output must be a JSON string encoding {"columns": [...], "rows": [[...]]}, '
    "containing the complete answer table. Preserve NULL, duplicate rows and column order. "
    "Do not include prose or unsupported claims in output."
)


def configuration(public: dict[str, Any], credentials: dict[str, str]) -> Any:
    cls = importlib.import_module("datus.configuration.agent_config").AgentConfig
    model = public["model"]
    if model["type"] != "openai":
        raise IntegrityError("this trace profile certifies only the native OpenAI model adapter")
    if not credentials.get("model_api_key"):
        raise IntegrityError("task-authorized model credential required")
    return cls(
        nodes={},
        home="/work/home/.datus",
        project_root="/work",
        session_dir="/work/sessions",
        plugins_enabled=False,
        active_plugins={},
        config_mutable=False,
        sql_read_only=True,
        bash={"enabled": False},
        filesystem={"strict": True},
        target="evaluation",
        models={
            "evaluation": {**model, "api_key": credentials["model_api_key"], "save_llm_trace": False, "max_retry": 1}
        },
        agentic_nodes={
            "gen_sql": {
                "model": "evaluation",
                "skills": "*",
                "subagents": "",
                "max_turns": public["max_turns"],
                "mcp": "",
            }
        },
    )


def connector_for(public: dict[str, Any], credentials: dict[str, str], fixture: bool) -> Any:
    database = public["database"]
    if fixture:
        cls = importlib.import_module("datus_sqlalchemy.connector").SQLAlchemyConnector

        class FixtureConnector(cls):
            def get_databases(self):
                return [database["name"]]

        connector = FixtureConnector("sqlite:///file:/inputs/database.sqlite?mode=ro&uri=true", dialect="sqlite")
        connector._default_database = database["name"]
        return connector
    if database["type"] != "mysql":
        raise IntegrityError("live profile requires the pinned native MySQL adapter")
    cls = importlib.import_module("datus_mysql").MySQLConnector
    if not credentials.get("db_password"):
        raise IntegrityError("task-authorized read-only database credential required")
    return cls({
        "host": database["host"],
        "port": database["port"],
        "database": database["name"],
        "username": database["username"],
        "password": credentials["db_password"],
    })


def probe_boundary(paths: list[str]) -> dict[str, Any]:
    denied = []
    for value in paths:
        try:
            Path(value).read_bytes()
        except (FileNotFoundError, PermissionError):
            denied.append(value)
    writable = Path("/work/probe")
    writable.write_text("private")
    immutable = False
    try:
        Path("/inputs/common.txt").write_text("changed")
    except PermissionError:
        immutable = True
    except OSError as error:
        immutable = error.errno == 30
    return {
        "denied": denied,
        "all_denied": len(denied) == len(paths),
        "frozen_read_only": immutable,
        "proc_absent": not Path("/proc/self").exists(),
        "private_write": writable.read_text() == "private",
    }


def execute_request(request: dict[str, Any], trace: ExecutionTrace) -> None:
    boundary = probe_boundary(request["denied_paths"])
    trace.emit("isolation_probe", **boundary)
    if not boundary["all_denied"] or not boundary["frozen_read_only"] or not boundary["proc_absent"]:
        raise IntegrityError("OS isolation probe failed")
    if request.get("probe_only"):
        return
    public = request["public"]
    trace.observe_model_http(public["model"]["base_url"])
    fixture = request["evidence_kind"] == "component_fixture"
    if request["evidence_kind"] not in {"component_fixture", "independent_development", "independent_learning"}:
        raise IntegrityError("formal evaluation is not admitted by this worker")
    config = configuration(public, request["credentials"])
    connector = connector_for(public, request["credentials"], fixture)
    try:
        # Establish the fixed database session before question injection. This
        # is question-independent setup; all raw preparation SQL remains in the
        # trace with online=false. Reconnects after injection still count.
        with connector._conn():
            pass
        graph, nodes = build_graph(
            config, connector, Path("/skills"), request["skill_names"], trace, session_id=str(uuid.uuid4())
        )
        common = Path("/inputs/common.txt").read_text() + "\n\n" + ANSWER_PROTOCOL
        asyncio.run(
            run_graph(
                graph,
                nodes,
                {
                    **request["task"],
                    "database": public["database"]["name"],
                    "current_date": public["current_date"],
                },
                trace,
                common=common,
                expected_effective=request.get("effective_sha256"),
                prepare_only=request.get("prepare_only", False),
            )
        )
    finally:
        connector.close()


def main() -> None:
    request = json.load(sys.stdin)
    # Keep a write-only pipe, then suppress all third-party stdout/log output.
    sink = sys.stdout
    sys.stdout = open(os.devnull, "w")  # noqa: SIM115 - process-owned for its lifetime
    logging.disable(logging.CRITICAL)
    structlog = importlib.import_module("structlog")
    structlog.configure(wrapper_class=structlog.make_filtering_bound_logger(logging.CRITICAL))
    with ExecutionTrace(
        stream=sink, run_id=request["run_id"], task_id=request["task"]["task_id"], attempt_id=request["attempt_id"]
    ) as trace:
        try:
            execute_request(request, trace)
        except Exception as error:
            trace.emit("worker_failure", error_type=type(error).__name__)
            raise


if __name__ == "__main__":
    main()
