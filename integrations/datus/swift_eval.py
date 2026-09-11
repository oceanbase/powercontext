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

"""POWE-129 swift-mode evaluator: native Datus graph on the authorized test stack.

Runs the frozen GenSQL -> ExecuteSQL -> Output graph from powercontext_datus
against the user-authorized read-only MySQL instance and model endpoint, one
question at a time, and records per-question tool-call steps and correctness.

Differences from the formal admission path (justified by the 2026-09-11 user
authorization on POWE-3): no TLS pinning (this DB endpoint offers no TLS in its
greeting), no admission approvals, no sandbox worker. The frozen graph, tool
profile and capture/step accounting are reused unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import importlib
import io
import json
import os
import sys
import time
import uuid
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "integrations" / "datus" / "src"))

from powercontext_datus.capture import ExecutionTrace, reconcile  # noqa: E402
from powercontext_datus.freeze import IntegrityError, digest_json, snapshot  # noqa: E402
from powercontext_datus.workflow import build_graph, tool_manifest  # noqa: E402

ANSWER_PROTOCOL = (
    "Return the native GenSQL JSON envelope with sql and output. "
    'The entire output must be a JSON string encoding {"columns": [...], "rows": [[...]]}, '
    "containing the complete answer table. Preserve NULL, duplicate rows and column order. "
    "Do not include prose or unsupported claims in output."
)

DB_HOST = os.environ.get("SWIFT_DB_HOST", "t7qse7zfed4cg-mi.cn-hangzhou.oceanbase.aliyuncs.com")
DB_PORT = int(os.environ.get("SWIFT_DB_PORT", "3306"))
DB_USER = os.environ.get("SWIFT_DB_USER", "mock_data_readonly")
DB_PASSWORD = os.environ["SWIFT_DB_PASSWORD"]
DB_NAME = os.environ.get("SWIFT_DB_NAME", "birdbench")

MODEL_TYPE = os.environ.get("SWIFT_MODEL_TYPE", "openai")
MODEL_NAME = os.environ.get("SWIFT_MODEL_NAME", "deepseek-v4-flash-0731")
MODEL_BASE_URL = os.environ["SWIFT_MODEL_BASE_URL"]
MODEL_API_KEY = os.environ["SWIFT_MODEL_API_KEY"]

CURRENT_DATE = os.environ.get("SWIFT_CURRENT_DATE", "2026-09-12")


class ListStream:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def write(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        return None


def configuration(workdir: Path, max_turns: int) -> Any:
    cls = importlib.import_module("datus.configuration.agent_config").AgentConfig
    return cls(
        nodes={},
        home=str(workdir / "home" / ".datus"),
        project_root=str(workdir / "project"),
        session_dir=str(workdir / "sessions"),
        plugins_enabled=False,
        active_plugins={},
        config_mutable=False,
        sql_read_only=True,
        bash={"enabled": False},
        filesystem={"strict": True},
        target="evaluation",
        models={
            "evaluation": {
                "type": MODEL_TYPE,
                "model": MODEL_NAME,
                "base_url": MODEL_BASE_URL,
                "api_key": MODEL_API_KEY,
                "save_llm_trace": False,
                "max_retry": 1,
            }
        },
        agentic_nodes={
            "gen_sql": {"model": "evaluation", "skills": "*", "subagents": "", "max_turns": max_turns, "mcp": ""}
        },
    )


def plain_connector() -> Any:
    """User-authorized plain MySQL connector for the no-TLS test endpoint."""
    cls = importlib.import_module("datus_mysql").MySQLConnector
    sqlalchemy = importlib.import_module("sqlalchemy")
    pymysql = importlib.import_module("pymysql")

    class PlainConnector(cls):
        _plain_engine = None

        def _ensure_engine(self):
            with self._engine_lock:
                if self.engine is not None:
                    return self.engine

                def connect():
                    return pymysql.connect(
                        host=DB_HOST,
                        port=DB_PORT,
                        user=DB_USER,
                        password=DB_PASSWORD,
                        database=DB_NAME,
                        connect_timeout=15,
                        charset="utf8mb4",
                        autocommit=True,
                    )

                self.engine = sqlalchemy.create_engine(
                    "mysql+pymysql://",
                    creator=connect,
                    pool_size=4,
                    max_overflow=8,
                    pool_timeout=60,
                    pool_recycle=3600,
                    pool_pre_ping=True,
                )
                self._plain_engine = self.engine
                self._owns_engine = True
                return self.engine

    connector = PlainConnector(
        {"host": DB_HOST, "port": DB_PORT, "username": DB_USER, "database": DB_NAME, "password": ""}
    )
    connector.connection_string = "mysql+pymysql://"
    return connector


def gold_connector() -> Any:
    import pymysql

    return pymysql.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASSWORD,
        database=DB_NAME,
        connect_timeout=15,
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.Cursor,
    )


def normalize_cell(value: Any) -> Any:
    if isinstance(value, dict):
        if set(value) == {"decimal"}:
            return float(value["decimal"])
        return json.dumps(value, sort_keys=True, default=str)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, (datetime, date)):
        return value.isoformat(sep=" ") if isinstance(value, datetime) else value.isoformat()
    if value is None:
        return None
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value


def close_enough(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool):
        if a == b:
            return True
        scale = max(abs(a), abs(b))
        return scale != 0 and abs(a - b) / scale <= 1e-4
    return a == b


def rows_multiset(rows: list[list[Any]]) -> list[tuple]:
    return sorted((tuple(normalize_cell(c) for c in row) for row in rows), key=repr)


def multiset_match(actual: list[tuple], expected: list[tuple]) -> bool:
    if len(actual) != len(expected):
        return False
    remaining = list(expected)
    for row in actual:
        for i, cand in enumerate(remaining):
            if len(row) == len(cand) and all(close_enough(a, e) for a, e in zip(row, cand)):
                remaining.pop(i)
                break
        else:
            return False
    return not remaining


def rows_match(actual: list[list[Any]], expected: list[list[Any]]) -> bool:
    """Gold tables are answer-oriented: when gold has one column, compare the
    multiset of the actual table's first column (extra display columns and column
    order do not change the asked-for answer)."""
    actual_rows = rows_multiset(actual)
    expected_rows = rows_multiset(expected)
    if expected_rows and all(len(r) == 1 for r in expected_rows):
        actual_first = sorted((row[0] for row in actual_rows), key=repr)
        expected_first = [row[0] for row in expected_rows]
        return multiset_match([[v] for v in actual_first], [[v] for v in expected_first])
    return multiset_match(actual_rows, expected_rows)


def canonical_sql(sql: str) -> str:
    return " ".join(sql.split()).strip().rstrip(";").lower()


def load_gold(cursor: Any, sql: str) -> dict[str, Any]:
    try:
        cursor.execute(sql)
        rows = [list(r) for r in cursor.fetchall()]
        return {"ok": True, "columns": [d[0] for d in cursor.description or []], "rows": rows}
    except Exception as error:  # noqa: BLE001 - recorded per gold row
        return {"ok": False, "error": f"{type(error).__name__}: {error}"}


async def run_question(
    question: str,
    task_id: str,
    workdir: Path,
    knowledge: str,
    max_turns: int,
    trace: ExecutionTrace,
) -> dict[str, Any]:
    config = configuration(workdir, max_turns)
    connector = plain_connector()
    try:
        graph, nodes = build_graph(
            config, connector, workdir / "skills", [], trace, session_id=str(uuid.uuid4())
        )
        # Mirror the formal worker: establish the pooled session before question
        # injection so handshake/setup SQL stays offline in the trace.
        with connector._conn():
            pass
        common = knowledge + "\n\n" + ANSWER_PROTOCOL
        models = importlib.import_module("datus.schemas.node_models")
        input_cls = importlib.import_module("datus.schemas.gen_sql_agentic_node_models").GenSQLNodeInput
        nodes[0].input = input_cls(user_message="", reference_date=CURRENT_DATE)
        prompt = nodes[0]._get_system_prompt()
        tools = tool_manifest(nodes[0].tools)
        trace.emit(
            "effective_config",
            prompt=prompt,
            tools=tools,
            session_id=nodes[0].session_id,
            embedding={"enabled": False},
        )
        graph.task = models.SqlTask(
            id=task_id,
            task=question,
            database_name=DB_NAME,
            output_dir=str(workdir / "output"),
            external_knowledge=common,
            current_date=CURRENT_DATE,
            artifact_profile="benchmark_v1",
        )
        trace.start_question(question)
        history = importlib.import_module("datus.schemas.action_history").ActionHistoryManager()
        for node in nodes:
            configured = node.setup_input(graph)
            if not configured["success"]:
                raise IntegrityError("native workflow input rejected")
            async for action in node.execute_stream(history):
                trace.emit("workflow_action", node=node.type, action=action.model_dump(mode="json"))
            trace.emit(
                "node_result", node=node.type, result=node.result.model_dump(mode="json") if node.result else None
            )
            if node.result is None or not node.result.success:
                raise IntegrityError("native node failed")
            if not node.update_context(graph)["success"]:
                raise IntegrityError("native workflow context update failed")
        response = nodes[0].result.response
        trace.emit("answer_submitted", gen_sql_response=response, answer=response, output=nodes[2].result.model_dump(mode="json"))
        return {"answer": response, "output": nodes[2].result.model_dump(mode="json")}
    finally:
        connector.close()


def evaluate_one(
    question: str,
    task_id: str,
    gold: dict[str, Any],
    workdir: Path,
    knowledge: str,
    max_turns: int,
    timeout: float,
    raw_records: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    stream = ListStream()
    started = time.monotonic()
    run: dict[str, Any] = {}
    failure: str | None = None
    with ExecutionTrace(stream=stream, run_id=task_id, task_id=task_id, attempt_id=str(uuid.uuid4())) as trace:
        try:
            run = asyncio.run(
                asyncio.wait_for(
                    run_question(question, task_id, workdir, knowledge, max_turns, trace), timeout=timeout
                )
            )
        except asyncio.TimeoutError:
            failure = "timeout"
        except IntegrityError as error:
            failure = f"integrity: {error}"
        except Exception as error:  # noqa: BLE001 - recorded per question
            failure = f"{type(error).__name__}: {error}"
    elapsed = round(time.monotonic() - started, 2)
    records = [json.loads(line) for line in stream.lines]
    if raw_records is not None:
        raw_records.extend(records)
    # The user metric is the number of model-initiated tool calls (工具调用次数).
    agent_tool_calls = [r for r in records if r["kind"] == "operation_started" and r.get("call_id") and r.get("online")]
    agent_tool_steps = len(agent_tool_calls)
    tool_call_names = [r["name"] for r in agent_tool_calls]
    summary = reconcile(records)
    by_name: dict[str, int] = {}
    for op in summary["operations"]:
        label = op["name"] if op["name"] != "sql" else "sql(execute_sql)"
        by_name[label] = by_name.get(label, 0) + 1
    final_sql = None
    actual_rows = None
    actual_columns = None
    model_answer = None
    correct = False
    verdict = failure or "executed"
    if run:
        output = run.get("output") or {}
        final_sql = (output.get("sql_query_final") or "").strip() or None
        model_answer = run.get("answer")
        if final_sql and gold.get("ok"):
            matching = [r for r in summary["sql_results"] if canonical_sql(r["sql"]) == canonical_sql(final_sql)]
            if not matching:
                # The envelope's SQL text may differ cosmetically from the
                # statement the node actually executed; fall back to the last
                # completed result-bearing span.
                matching = [
                    r
                    for r in summary["sql_results"]
                    if r["rows"] and r["sql"].lstrip().upper().startswith(("SELECT", "WITH"))
                ]
            if matching:
                actual_rows = matching[-1]["rows"]
                actual_columns = matching[-1]["columns"]
                correct = rows_match(actual_rows, gold["rows"])
                if not correct:
                    verdict = "wrong_answer"
            else:
                verdict = "final_sql_not_executed"
        elif not gold.get("ok"):
            verdict = "gold_sql_failed"
    if failure:
        verdict = failure
    elif correct:
        verdict = "correct"
    return {
        "task_id": task_id,
        "question": question,
        "gold_ok": gold.get("ok", False),
        "gold_answer_preview": [normalize_cell(r[0]) if r else None for r in gold.get("rows", [])][:5] if gold.get("ok") else gold.get("error"),
        "steps": agent_tool_steps,
        "tool_call_sequence": tool_call_names,
        "bridge_steps": summary["steps"],
        "trace_complete": summary["trace_complete"],
        "trace_issues": summary["trace_issues"],
        "tool_calls": by_name,
        "correct": correct,
        "verdict": verdict,
        "final_sql": final_sql,
        "actual_columns": actual_columns,
        "actual_rows": actual_rows if actual_rows and len(actual_rows) <= 10 else (actual_rows[:10] if actual_rows else None),
        "model_answer": (model_answer[:500] if isinstance(model_answer, str) else model_answer),
        "elapsed_seconds": elapsed,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--csv", default=str(Path.home() / "tmp" / "debit_card_specializing_accuracy_qa_detailed.csv"))
    parser.add_argument("--split", choices=["test", "validation", "all"], required=True)
    parser.add_argument("--knowledge", default=str(Path(__file__).parent / "swift_knowledge.md"))
    parser.add_argument("--out", required=True)
    parser.add_argument("--workdir", required=True)
    parser.add_argument("--max-turns", type=int, default=8)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--only", default="", help="comma-separated 1-based CSV row numbers")
    args = parser.parse_args()

    with open(args.csv, encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    questions = []
    for index, row in enumerate(rows):
        split = "test" if index % 2 == 0 else "validation"
        questions.append(
            {
                "row": index + 1,
                "split": split,
                "question": row["question"],
                "answer": row["answer"],
                "example_sql": row["example_sql"],
            }
        )
    selected = [q for q in questions if args.split in (q["split"], "all")]
    if args.only:
        wanted = {int(x) for x in args.only.split(",")}
        selected = [q for q in selected if q["row"] in wanted]
    if args.limit:
        selected = selected[: args.limit]

    workdir = Path(args.workdir)
    for sub in ("home/.datus", "project", "sessions", "output", "skills"):
        (workdir / sub).mkdir(parents=True, exist_ok=True)

    knowledge = Path(args.knowledge).read_text(encoding="utf-8")

    gold_conn = gold_connector()
    gold_cur = gold_conn.cursor()
    gold_cache: dict[str, dict[str, Any]] = {}
    try:
        for q in selected:
            if q["example_sql"] not in gold_cache:
                gold_cache[q["example_sql"]] = load_gold(gold_cur, q["example_sql"])
    finally:
        gold_conn.close()

    results = []
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as sink:
        for q in selected:
            raw_path = out_path.with_name(f"{out_path.stem}.trace-{q['row']}.jsonl")
            raw_records: list[dict[str, Any]] = []
            result = evaluate_one(
                q["question"], f"row{q['row']}", gold_cache[q["example_sql"]], workdir, knowledge,
                args.max_turns, args.timeout, raw_records=raw_records,
            )
            with raw_path.open("w", encoding="utf-8") as raw_sink:
                for record in raw_records:
                    raw_sink.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
            result["split"] = q["split"]
            result["csv_row"] = q["row"]
            result["csv_answer"] = q["answer"]
            results.append(result)
            sink.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
            sink.flush()
            print(
                f"[{len(results)}/{len(selected)}] row{q['row']} steps={result['steps']} "
                f"correct={result['correct']} verdict={result['verdict']} {result['elapsed_seconds']}s",
                file=sys.stderr,
                flush=True,
            )

    total = len(results)
    correct_n = sum(r["correct"] for r in results)
    dist: dict[str, int] = {}
    for r in results:
        key = str(r["steps"]) if r["steps"] is not None else "unknown"
        dist[key] = dist.get(key, 0) + 1
    print(json.dumps({"total": total, "correct": correct_n, "accuracy": correct_n / total if total else None, "step_distribution": dist}, ensure_ascii=False))


if __name__ == "__main__":
    main()
