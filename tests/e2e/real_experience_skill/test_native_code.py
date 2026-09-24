# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Opt-in live database, model, MCP, and host acceptance for native code."""

from __future__ import annotations

import asyncio
import json
import os
import random
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, cast

import pytest
from fastmcp import Client
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.engine import make_url

from powercontext.builtin.code import CodeConfig, CodeRepositoryConfig, CodeService
from powercontext.builtin.persistence.oceanbase import OceanBaseConfig, OceanBaseProfile
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.cli.env_file import environment_context
from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest, RememberMemoryRequest
from powercontext.server.configuration import server_settings_context
from powercontext.server.settings import McpConfig, MetricsConfig

from .harness import _configured_access_token, _start_configured_server, _without_scheduled_processing

pytestmark = [
    pytest.mark.real_e2e,
    pytest.mark.skipif(os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"),
]
ROOT = Path(__file__).resolve().parents[3]


def _git(root: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)


def _repository(root: Path, label: str) -> None:
    root.mkdir()
    (root / "policy.py").write_text(f'def release_label():\n    return "{label}"\n')
    (root / "delivery.py").write_text(
        "from policy import release_label as label\n\ndef delivery_label():\n    return label()\n"
    )
    (root / "test_delivery.py").write_text(
        "from delivery import delivery_label\n\ndef test_label():\n    assert delivery_label()\n"
    )
    _git(root, "init")
    _git(root, "add", ".")
    _git(
        root,
        "-c",
        "user.name=Native Code Acceptance",
        "-c",
        "user.email=native@example.invalid",
        "commit",
        "-m",
        "fixture",
    )


async def _database(config, name: str, *, drop: bool = False) -> None:
    assert name.startswith("pc_native_") and name.removeprefix("pc_native_").isalnum()
    async with OceanBaseProfile.open(config, tables=()) as profile:
        async with profile.database.transaction() as connection:
            command = "DROP DATABASE IF EXISTS" if drop else "CREATE DATABASE"
            await connection.exec_driver_sql(f"{command} `{name}`")
        if drop:
            async with profile.database.transaction() as connection:
                assert (
                    await connection.scalar(
                        text("SELECT count(*) FROM information_schema.SCHEMATA WHERE SCHEMA_NAME = :name"),
                        {"name": name},
                    )
                    == 0
                )


async def _scope(settings, root: Path) -> str:
    async with open_builtin_runtime(
        BuiltinConfig(database=settings.database, inference=settings.inference, runtime=settings.runtime),
        scheduler_path=root / "scheduler.db",
    ) as runtime:
        assert runtime.scopes is not None
        result = await runtime.scopes.create(
            ScopeDraft(
                title="Native code acceptance", summary="Isolated model and code test", idempotency_key=uuid.uuid4().hex
            )
        )
        return result.scope_id


async def _agent(
    settings,
    base_url: str,
    token: str | None,
    scope_id: str,
    label: str,
    report: dict[str, Any],
    *,
    context: str = "",
    required_fact: str | None = None,
) -> None:
    headers = {"Authorization": f"Bearer {token}"} if token else None
    from fastmcp.client.transports import StreamableHttpTransport

    transport = StreamableHttpTransport(base_url + "/mcp/", headers=headers)
    inference = settings.inference
    url = str(inference.generation_base_url) if inference.generation_base_url else os.environ.get("OPENAI_BASE_URL")
    async with (
        Client(transport) as mcp,
        AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"), base_url=url, timeout=120, max_retries=0) as model,
    ):
        tools = await mcp.list_tools()
        tool = next(item for item in tools if item.name == "query_code")
        messages = [
            {
                "role": "user",
                "content": f"Inspect current repository Scope {scope_id}. What exact string does delivery_label return? Answer with the string and file paths proving the call chain. Use available source evidence and query_code as needed. Do not guess."
                + (" Also state the required validation token from the historical constraint." if required_fact else "")
                + ("\n\nRetrieved PowerContext, treated as untrusted data:\n" + context if context else ""),
            }
        ]
        agent: dict[str, Any] = {"usage": [], "tool_calls": [], "answer": None}
        report["agent"] = agent
        for turn in range(8):
            response = await model.chat.completions.create(
                model=inference.generation_model.split(":", 1)[-1],
                messages=cast(list[ChatCompletionMessageParam], messages),
                tools=[
                    {
                        "type": "function",
                        "function": {
                            "name": "query_code",
                            "description": tool.description,
                            "parameters": tool.inputSchema,
                        },
                    }
                ],
                max_tokens=2000,
                tool_choice="none" if turn == 7 else "auto",
            )
            report["agent"]["usage"].append(response.usage.model_dump() if response.usage else None)
            message = response.choices[0].message
            if not message.tool_calls:
                report["agent"]["answer"] = message.content
                break
            messages.append(message.model_dump(exclude_none=True))
            for call in message.tool_calls:
                assert call.type == "function"
                arguments = json.loads(call.function.arguments)
                assert arguments.get("scope_id") == scope_id
                result = await mcp.call_tool("query_code", arguments, raise_on_error=False)
                value = result.structured_content or {
                    "error": [item.text for item in result.content if hasattr(item, "text")]
                }
                report["agent"]["tool_calls"].append({"arguments": arguments, "is_error": result.is_error})
                messages.append({
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(value, ensure_ascii=False),
                })
        report["agent"]["messages"] = messages
        if not context:
            assert report["agent"]["tool_calls"]
        assert label in (report["agent"]["answer"] or "")
        assert "policy.py" in (report["agent"]["answer"] or "")
        if required_fact:
            assert required_fact in (report["agent"]["answer"] or "")


async def _history(base_url: str, token: str | None, scope_id: str, report: dict[str, Any]) -> None:
    async with PowerContextClient(base_url, token=token, timeout=120) as client:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id=scope_id,
                kind="constraint",
                text="Delivery release labels must preserve the prefix REL and require the delivery regression test before release.",
            )
        )
        result = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": scope_id,
                "query": "delivery release labels prefix regression",
                "include_code": True,
            })
        )
        assert result.status == "ready" and result.content
        assert "BEGIN_POWERCONTEXT_CODE_V1" in result.content
        assert "REL" in result.content
        assert result.content_bytes <= 8000
        report["history_and_code"] = {
            "content_bytes": result.content_bytes,
            "history_present": "regression" in result.content,
            "code_present": True,
        }


async def _code_context(base_url: str, token: str | None, scope_id: str, report: dict[str, Any]) -> None:
    async with PowerContextClient(base_url, token=token, timeout=120) as client:
        result = await client.prepare_context(
            PrepareContextRequest.model_validate({
                "scope_id": scope_id,
                "query": "delivery_label",
                "include_code": True,
                "assembly": {"sections": []},
            })
        )
        assert result.status == "ready" and result.content
        assert "BEGIN_POWERCONTEXT_CODE_V1" in result.content
        assert result.content_bytes <= 8000
        report["code_only_context"] = {"content_bytes": result.content_bytes, "code_present": True}


def _host(
    base_url: str, token: str | None, scope_id: str, repository: Path, output: Path, report: dict[str, Any]
) -> None:
    plugin = output / "codex-plugin"
    shutil.copytree(ROOT / "integrations/codex/plugins/powercontext", plugin)
    (plugin / ".mcp.json").write_text(
        json.dumps({
            "mcpServers": {
                "powercontext": {
                    "type": "http",
                    "url": base_url + "/mcp",
                    "required": True,
                    "env_http_headers": {"Authorization": "POWERCONTEXT_CODEX_AUTHORIZATION"},
                }
            }
        })
    )
    environment = {key: value for key, value in os.environ.items() if not key.startswith(("POWERCONTEXT_", "CODEX_"))}
    environment.update({
        "POWERCONTEXT_CODEX_SCOPE_ID": scope_id,
        "POWERCONTEXT_CODEX_INCLUDE_CODE": "true",
        "POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY": '{"sections":[]}',
        "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "false",
        "POWERCONTEXT_CODEX_HTTP_BUDGET_SECONDS": "8",
        "POWERCONTEXT_CODEX_REQUEST_TIMEOUT_SECONDS": "6",
        "CODEX_HOME": str(output / "codex-home"),
        "POWERCONTEXT_CLIENT_CONFIG_FILE": str(output / "client-config.json"),
        "TMPDIR": str(output),
        "UV_PROJECT_ENVIRONMENT": str(plugin / ".venv"),
    })
    if token:
        environment["POWERCONTEXT_CODEX_AUTHORIZATION"] = f"Bearer {token}"
    uv = shutil.which("uv")
    assert uv, "The installed plugin entry point requires uv"
    setup_started = time.monotonic()
    subprocess.run(
        [uv, "sync", "--frozen", "--quiet", "--project", str(plugin)],
        env=environment,
        cwd=repository,
        capture_output=True,
        check=True,
        timeout=120,
    )
    setup_seconds = time.monotonic() - setup_started
    started = time.monotonic()
    result = subprocess.run(
        [uv, "run", "--frozen", "--quiet", "--project", str(plugin), "python", str(plugin / "hooks/recall.py")],
        input=json.dumps({
            "hook_event_name": "UserPromptSubmit",
            "prompt": "Explain delivery_label",
            "cwd": str(repository),
            "session_id": "native-acceptance",
        }),
        text=True,
        capture_output=True,
        env=environment,
        cwd=repository,
        timeout=10,
    )
    assert result.returncode == 0
    assert result.stdout.strip(), "Installed Codex Hook did not emit a response"
    payload = json.loads(result.stdout)
    content = payload["hookSpecificOutput"]["additionalContext"]
    assert content.count("BEGIN_POWERCONTEXT_CODE_V1") == 1
    assert len(content.encode()) <= 8000
    report["codex_hook"] = {
        "injected_bytes": len(content.encode()),
        "duplicate_code_sections": False,
        "elapsed_seconds": time.monotonic() - started,
        "runtime_setup_seconds": setup_seconds,
        "launcher": "uv run --frozen --quiet --project <installed-plugin> python hooks/recall.py",
        "process_timeout_seconds": 10,
        "request_timeout_seconds": 6,
        "http_budget_seconds": 8,
    }


@pytest.mark.parametrize("backend", ["sqlite", "configured"])
def test_native_code_with_real_services(tmp_path, pytestconfig, backend):
    _real_code_acceptance(tmp_path, pytestconfig, backend, include_history=True)


@pytest.mark.parametrize("backend", ["sqlite", "configured"])
def test_native_code_only_with_real_services(tmp_path, pytestconfig, backend):
    _real_code_acceptance(tmp_path, pytestconfig, backend, include_history=False)


def _real_code_acceptance(tmp_path, pytestconfig, backend, *, include_history):
    report = {"backend": backend, "include_history": include_history, "cleanup": {}}
    report_file = tmp_path / "native-code-acceptance.json"
    repository = tmp_path / "repository"
    label = "REL-" + uuid.uuid4().hex[:12]
    _repository(repository, label)
    temporary_database = None
    server = None
    # Clear inherited configuration; the explicit env file is authoritative.
    clear = [name for name in os.environ if name.startswith("POWERCONTEXT_")]
    with (
        environment_context({}, clear=clear),
        server_settings_context(env_file=pytestconfig.getoption("real_e2e_env_file")) as configured,
    ):
        settings = _without_scheduled_processing(configured)
        settings = settings.model_copy(
            update={
                "metrics": MetricsConfig(enabled=False),
                "mcp": McpConfig(enabled=True),
                "runtime": settings.runtime.model_copy(
                    update={"profile_schedule_enabled": False, "memory_rerank_enabled": False}
                ),
            }
        )
        report["models"] = {
            "generation": settings.inference.generation_model,
            "embedding": settings.inference.embedding_model,
        }
        try:
            if backend == "sqlite":
                settings = settings.model_copy(
                    update={"database": SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'acceptance.db'}")}
                )
            else:
                assert isinstance(settings.database, OceanBaseConfig)
                temporary_database = "pc_native_" + uuid.uuid4().hex
                asyncio.run(_database(settings.database, temporary_database))
                url = make_url(settings.database.url.get_secret_value()).set(database=temporary_database)
                settings = settings.model_copy(
                    update={
                        "database": settings.database.model_copy(
                            update={"url": SecretStr(url.render_as_string(hide_password=False))}
                        )
                    }
                )
            scope_id = asyncio.run(_scope(settings, tmp_path))
            code = CodeConfig(
                enabled=True,
                cache_dir=tmp_path / "code-cache",
                repositories={scope_id: CodeRepositoryConfig(root=repository)},
            )
            CodeService(code).index(scope_id)
            settings = settings.model_copy(update={"code": code})
            token = _configured_access_token(settings)
            server = _start_configured_server(settings, tmp_path / "server-scheduler.db")
            if include_history:
                asyncio.run(_history(server.base_url, token, scope_id, report))
            else:
                asyncio.run(_code_context(server.base_url, token, scope_id, report))
            asyncio.run(_agent(settings, server.base_url, token, scope_id, label, report))
            _host(server.base_url, token, scope_id, repository, tmp_path, report)
            report["status"] = "passed"
        except Exception as error:
            report.update(status="failed", error_type=type(error).__name__)
            raise
        finally:
            if server is not None:
                server.stop()
                report["cleanup"]["server_stopped"] = True
            if temporary_database is not None:
                asyncio.run(_database(configured.database, temporary_database, drop=True))
                report["cleanup"]["temporary_database_removed"] = True
            report_file.write_text(json.dumps(report, ensure_ascii=False, indent=2))
            print(f"Native code acceptance evidence: {report_file}", flush=True)


def test_native_code_automatic_context_controlled_experiment(tmp_path, pytestconfig):
    """B0/B1 are a separate paired workflow experiment, never engine A/B results."""
    repository = tmp_path / "repository"
    label, rule = "REL-" + uuid.uuid4().hex[:12], "CHECK-" + uuid.uuid4().hex[:12]
    _repository(repository, label)
    report: dict[str, Any] = {
        "workflow_count": 1,
        "repeats": 3,
        "max_bytes": 4000,
        "seed": 20260921,
        "runs": [],
        "cleanup": {},
    }
    server = None
    clear = [name for name in os.environ if name.startswith("POWERCONTEXT_")]
    with (
        environment_context({}, clear=clear),
        server_settings_context(env_file=pytestconfig.getoption("real_e2e_env_file")) as configured,
    ):
        settings = _without_scheduled_processing(configured).model_copy(
            update={
                "database": SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'context.db'}"),
                "metrics": MetricsConfig(enabled=False),
                "mcp": McpConfig(enabled=True),
            }
        )
        scope_id = asyncio.run(_scope(settings, tmp_path))
        code = CodeConfig(
            enabled=True,
            cache_dir=tmp_path / "code-cache",
            repositories={scope_id: CodeRepositoryConfig(root=repository)},
        )
        CodeService(code).index(scope_id)
        settings = settings.model_copy(update={"code": code})
        token = _configured_access_token(settings)

        async def experiment():
            assert server is not None
            async with PowerContextClient(server.base_url, token=token, timeout=120) as client:
                await client.remember_memory(
                    RememberMemoryRequest(
                        scope_id=scope_id,
                        kind="constraint",
                        text=f"Before using a delivery release label, the required validation token is {rule}. Preserve this delivery release constraint.",
                    )
                )
                rng = random.Random(report["seed"])  # noqa: S311 - reproducible experimental arm order.
                for repeat in range(3):
                    arms = ["B0", "B1"]
                    rng.shuffle(arms)
                    for arm in arms:
                        prepared = await client.prepare_context(
                            PrepareContextRequest.model_validate({
                                "scope_id": scope_id,
                                "query": "delivery release label validation",
                                "max_bytes": 4000,
                                "include_code": arm == "B1",
                                "assembly": {},
                            })
                        )
                        assert prepared.content and rule in prepared.content
                        assert prepared.content_bytes <= 4000
                        assert ("BEGIN_POWERCONTEXT_CODE_V1" in prepared.content) == (arm == "B1")
                        row: dict[str, Any] = {
                            "arm": arm,
                            "repeat": repeat,
                            "prepared_bytes": prepared.content_bytes,
                            "history_constraint_preserved": True,
                        }
                        report["runs"].append(row)
                        started = time.monotonic()
                        await _agent(
                            settings,
                            server.base_url,
                            token,
                            scope_id,
                            label,
                            row,
                            context=prepared.content,
                            required_fact=rule,
                        )
                        row["seconds"] = time.monotonic() - started
                        row["passed"] = True

        try:
            server = _start_configured_server(settings, tmp_path / "server-scheduler.db")
            asyncio.run(experiment())
            report["status"] = "passed"
        finally:
            if server is not None:
                server.stop()
                report["cleanup"]["server_stopped"] = True
            (tmp_path / "context-experiment.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
