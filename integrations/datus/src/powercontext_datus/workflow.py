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


"""Pinned native GenSQL -> ExecuteSQL -> Output wiring for a frozen tool profile."""

# ruff: noqa: TRY003
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from powercontext_datus.capture import ExecutionTrace
from powercontext_datus.freeze import IntegrityError, digest_json, snapshot, verify_snapshot
from powercontext_datus.native import skill_manager_for, verify_runtime

TOOL_NAMES = ["describe_table", "execute_sql", "list_tables", "load_skill"]
WORKFLOW = ["gen_sql", "execute_sql", "output"]


def tool_manifest(tools: list[Any]) -> list[dict[str, Any]]:
    result = [{"name": t.name, "description": t.description, "schema": t.params_json_schema} for t in tools]
    if sorted(t["name"] for t in result) != TOOL_NAMES:
        raise IntegrityError("effective native tool inventory differs from frozen profile")
    return sorted(result, key=lambda t: t["name"])


def build_graph(  # noqa: C901 - native-only local subclasses keep this package importable in the SDK environment.
    config: Any, connector: Any, skill_root: Path, names: list[str], trace: ExecutionTrace, *, session_id: str
) -> tuple[Any, list[Any]]:
    """Keep native execution and response parsing; restrict injection points only.

    All tools are original Datus tools. The subset is identical in both arms.
    Shared schema/business/example text is pre-imported into external_knowledge.
    There is no online index, embedding, memory, shell, MCP or child-agent tool.
    """
    verify_runtime()
    gen_cls = importlib.import_module("datus.agent.node.gen_sql_agentic_node").GenSQLAgenticNode
    execute_cls = importlib.import_module("datus.agent.node.execute_sql_node").ExecuteSQLNode
    output_cls = importlib.import_module("datus.agent.node.output_node").OutputNode
    workflow_cls = importlib.import_module("datus.agent.workflow").Workflow
    manager = skill_manager_for(skill_root, names)
    trace.observe_skill_loads(manager)

    class FrozenGenSQL(gen_cls):
        def _setup_skill_manager(self):
            self.skill_manager = manager

        def setup_tools(self):
            db_cls = importlib.import_module("datus.tools.func_tool.database").DBFuncTool
            # No KB is configured: native tools use the one frozen connector.
            self.db_func_tool = db_cls(connector, read_only=True)
            self.tools = [
                self.db_func_tool.to_function_tool(method)
                for method in (
                    self.db_func_tool.describe_table,
                    self.db_func_tool.execute_sql,
                    self.db_func_tool.list_tables,
                )
            ]
            self._ensure_skill_tools_in_tools()
            tool_manifest(self.tools)
            self.tools = [trace.wrap_tool(t) for t in self.tools]

        def _ensure_lazy_tools_mounted(self):
            tool_manifest(self.tools)

        def _setup_bash_tool(self):
            self.bash_tool = None

        def _inject_memory_context(self, base_prompt, **kwargs):
            return base_prompt

        def _build_context_rewriter(self, ctx):
            # One question/session; hidden compaction model calls are prohibited.
            return None

        async def _auto_compact(self):
            return None

        def _compose_run_hooks(self, ctx):
            base_hooks = self._compose_hooks()
            return combine_hooks(base_hooks, trace)

        def _ensure_tool_transformers(self):
            # No plugins are active; reject a configuration change before dispatch.
            if self.agent_config._active_plugins or self.mcp_servers:
                raise IntegrityError("dynamic plugin/MCP configuration is forbidden")
            tool_manifest(self.tools)

    class BoundExecute(execute_cls):
        def _sql_connector(self, database_name=""):
            if database_name and database_name != connector.database_name:
                raise IntegrityError("database routing changed")
            return connector

    class BoundOutput(output_cls):
        def _sql_connector(self, database_name=""):
            return connector

    class FrozenWorkflow(workflow_cls):
        def _init_tools(self):
            # Workflow.tools is not used by these three nodes. Avoid constructing
            # a second implicit DB/KB tool inventory.
            self.tools = []

    gen = FrozenGenSQL(
        "gen_sql",
        "Generate SQL",
        "gen_sql",
        agent_config=config,
        node_name="gen_sql",
        execution_mode="workflow",
        session_id=session_id,
    )
    execute = BoundExecute("execute_sql", "Execute SQL", "execute_sql", agent_config=config)
    output = BoundOutput("output", "Submit native result", "output", agent_config=config)
    workflow = FrozenWorkflow("powercontext-development", agent_config=config)
    for node in (gen, execute, output):
        workflow.add_node(node)
    return workflow, [gen, execute, output]


def combine_hooks(base: Any, trace: ExecutionTrace) -> Any:
    hooks_cls = importlib.import_module("agents").RunHooks

    class CaptureHooks(hooks_cls):
        async def on_llm_start(self, context, agent, system_prompt=None, input_items=None, **kwargs):
            trace.emit(
                "model_started",
                model=getattr(agent.model, "model", type(agent.model).__name__),
                input_sha256=digest_json({"system": system_prompt, "items": input_items}),
            )

        async def on_llm_end(self, context, agent, response):
            usage = getattr(response, "usage", None)
            payload = None
            if usage is not None:
                payload = {
                    name: getattr(usage, name, None)
                    for name in ("requests", "input_tokens", "output_tokens", "total_tokens")
                }
                details = getattr(usage, "input_tokens_details", None)
                # The SDK defaults absent cache accounting to zero. Preserve
                # that ambiguity instead of treating the default as a report.
                payload["cached_tokens"] = getattr(details, "cached_tokens", None) or None
                if not payload["input_tokens"] and not payload["output_tokens"]:
                    payload = None  # SDK defaults are not proof of zero usage.
            trace.emit("model_finished", usage=payload)

    # Native CompositeRunHooks is the same dispatcher used by AgenticNode.
    module = importlib.import_module("datus.tools.permission.permission_hooks")

    class Composite(module.CompositeHooks):
        async def on_llm_start(self, context, agent, system_prompt=None, input_items=None, **kwargs):
            # The pinned native composite only forwards on_llm_end.
            for hook in self.hooks_list:
                if hasattr(hook, "on_llm_start"):
                    await hook.on_llm_start(context, agent, system_prompt, input_items, **kwargs)

    return Composite([base, CaptureHooks()] if base is not None else [CaptureHooks()])


async def run_graph(
    workflow: Any,
    nodes: list[Any],
    task: dict[str, Any],
    trace: ExecutionTrace,
    *,
    common: str,
    expected_effective: str | None = None,
    prepare_only: bool = False,
) -> dict[str, Any]:
    models = importlib.import_module("datus.schemas.node_models")
    skills_before = snapshot(Path(nodes[0].skill_manager.config.directories[0]))
    input_cls = importlib.import_module("datus.schemas.gen_sql_agentic_node_models").GenSQLNodeInput
    nodes[0].input = input_cls(user_message="", reference_date=task["current_date"])
    # Render before injecting the question to freeze the complete effective prompt.
    prompt = nodes[0]._get_system_prompt()
    tools = tool_manifest(nodes[0].tools)
    tools_digest = digest_json(tools)
    effective = {"prompt": prompt, "tools": tools, "skills": skills_before, "common": common}
    effective_digest = digest_json(effective)
    if expected_effective is not None and effective_digest != expected_effective:
        raise IntegrityError("effective prompt/tools/context drifted")
    trace.emit(
        "effective_config",
        workflow=WORKFLOW,
        prompt=prompt,
        tools=tools,
        tools_sha256=tools_digest,
        session_id=nodes[0].session_id,
        skills=skills_before,
        embedding={"enabled": False},
        common_sha256=digest_json(common),
        effective_sha256=effective_digest,
    )
    if prepare_only:
        return {"effective_sha256": effective_digest}
    model = nodes[0].model
    original_stream = model.generate_with_tools_stream

    async def guarded_stream(*args, **kwargs):
        if (
            tool_manifest(kwargs.get("tools", [])) != tools
            or kwargs.get("mcp_servers")
            or any((kwargs.get("builtin_web_tools") or {}).values())
            or kwargs.get("instruction") != prompt
        ):
            trace.emit("coverage_failure", reason="model_dispatch_drift")
            raise IntegrityError("model dispatch differs from frozen effective configuration")
        async for action in original_stream(*args, **kwargs):
            yield action

    model.generate_with_tools_stream = guarded_stream
    workflow.task = models.SqlTask(
        id=task["task_id"],
        task=task["question"],
        database_name=task["database"],
        output_dir="/work/output",
        external_knowledge=common,
        current_date=task["current_date"],
        artifact_profile="benchmark_v1",
    )
    trace.start_question(task["question"])
    await execute_nodes(workflow, nodes, trace)
    verify_snapshot(Path(nodes[0].skill_manager.config.directories[0]), skills_before)
    if tool_manifest(nodes[0].tools) != tools:
        raise IntegrityError("tools changed during execution")
    # GenSQL's user-facing answer remains separate from the native output's CSV.
    # The evaluator checks the entire answer independently against online rows.
    response = nodes[0].result.response
    # The pinned GenSQL parser has already extracted the envelope's output.
    answer = response
    trace.emit(
        "answer_submitted", gen_sql_response=response, answer=answer, output=nodes[2].result.model_dump(mode="json")
    )
    return {"answer": answer, "session_id": nodes[0].session_id}


async def execute_nodes(workflow: Any, nodes: list[Any], trace: ExecutionTrace) -> None:
    history = importlib.import_module("datus.schemas.action_history").ActionHistoryManager()
    for node in nodes:
        configured = node.setup_input(workflow)
        if not configured["success"]:
            raise IntegrityError("native workflow input rejected")
        # Native stream actions from execute/output are yielded but not added to
        # the manager upstream. Capture these raw lifecycle records explicitly.
        async for action in node.execute_stream(history):
            trace.emit("workflow_action", node=node.type, action=action.model_dump(mode="json"))
        trace.emit("node_result", node=node.type, result=node.result.model_dump(mode="json") if node.result else None)
        if node.result is None or not node.result.success:
            raise IntegrityError("native node failed; inspect preserved node_result")
        if not node.update_context(workflow)["success"]:
            raise IntegrityError("native workflow context update failed")
