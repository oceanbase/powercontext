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

"""Protect what the harness forwards into the agent container for each run."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import NamedTuple

import pytest
from harbor.agents.installed.opencode import OpenCode
from harbor.environments.base import ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig

from powercontext_e2e.catalog import E2ETask, load_tasks
from powercontext_e2e.harbor_claude_code import PowerContextClaudeCodeAgent
from powercontext_e2e.harbor_codex import PowerContextCodexAgent
from powercontext_e2e.harbor_opencode import PowerContextOpenCodeAgent
from powercontext_e2e.hosts import host_adapter
from powercontext_e2e.runner import _job_config, prepare_runtime_task, require_runtime_models, run_tasks
from powercontext_e2e.settings import HarnessSettings, ModelNotConfiguredError

_REPOSITORY = Path(__file__).resolve().parents[3]
_TASKS = load_tasks(_REPOSITORY / "e2e" / "bub" / "tasks")
_CODEX_AUTH_TARGET = "/run/agent-auth/codex-auth.json"
_PAIRED_TASK = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "project-decision-continuation.yaml")[0]


@pytest.fixture(autouse=True)
def isolated_host_environment(monkeypatch, tmp_path: Path) -> Path:
    for name in tuple(os.environ):
        if name.startswith(("BUB_", "POWERCONTEXT_")) or name in {"CODEX_HOME", "GITHUB_SHA", "OPENAI_API_KEY"}:
            monkeypatch.delenv(name)
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    return codex_home


def _task(task_id: str) -> E2ETask:
    return next(task for task in _TASKS if task.id == task_id)


def _config(task: E2ETask, tmp_path: Path, **kwargs) -> JobConfig:
    return _job_config(task, "run-1", "scope-1", tmp_path / "out", HarnessSettings(repository=_REPOSITORY), **kwargs)


def _agent_env(config: JobConfig) -> dict[str, str]:
    (agent,) = config.agents
    return agent.env


def _mount_targets(config: JobConfig) -> list[str]:
    return [mount["target"] for mount in config.environment.mounts]


def _powercontext_mounts(config: JobConfig) -> list[str]:
    # Any path naming PowerContext is a lead for an OFF agent that searches its container.
    return [target for target in _mount_targets(config) if "powercontext" in target]


def test_non_model_task_disables_bub_model_access(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BUB_MODEL", "provider:model")
    monkeypatch.setenv("BUB_API_KEY", "host-bub-key")
    monkeypatch.setenv("BUB_OPENAI_API_KEY", "host-provider-key")
    monkeypatch.setenv("POWERCONTEXT_BUB_CUSTOM", "forwarded")
    monkeypatch.setenv("POWERCONTEXT_BUB_EMPTY", "")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "server-token")
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-key")
    task = _task("project-database-decision")
    assert not task.execution.model

    env = _agent_env(_config(task, tmp_path))

    assert env["BUB_API_KEY"] == "null"
    assert env["BUB_FALLBACK_MODELS"] == "null"
    assert "BUB_MODEL" not in env
    assert "BUB_OPENAI_API_KEY" not in env
    assert env["POWERCONTEXT_BUB_CUSTOM"] == "forwarded"
    assert "POWERCONTEXT_BUB_EMPTY" not in env
    assert "POWERCONTEXT_CLIENT_API_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert {"unrelated-key", "host-bub-key", "host-provider-key"}.isdisjoint(env.values())


def test_model_task_forwards_host_bub_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("BUB_MODEL", "provider:model")
    monkeypatch.setenv("BUB_API_KEY", "host-bub-key")
    monkeypatch.setenv("BUB_EMPTY", "")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "server-token")
    task = _task("terminal-bench-db-wal-recovery")
    assert task.execution.model

    env = _agent_env(_config(task, tmp_path))

    assert env["BUB_MODEL"] == "provider:model"
    assert env["BUB_API_KEY"] == "host-bub-key"
    assert "BUB_FALLBACK_MODELS" not in env
    assert "BUB_EMPTY" not in env
    assert "POWERCONTEXT_CLIENT_API_TOKEN" not in env


def test_manifest_budget_and_job_scope_override_host_values(monkeypatch, tmp_path: Path) -> None:
    for name in ("BUB_MAX_STEPS", "BUB_MAX_TOKENS"):
        monkeypatch.setenv(name, "host-value")
    monkeypatch.setenv("POWERCONTEXT_BUB_SCOPE_ID", "host-scope")
    task = _task("terminal-bench-db-wal-recovery")

    env = _agent_env(_config(task, tmp_path))

    assert env["BUB_MAX_STEPS"] == str(task.execution.max_steps)
    assert env["BUB_MAX_TOKENS"] == str(task.execution.max_tokens)
    assert env["POWERCONTEXT_BUB_SCOPE_ID"] == "scope-1"


def test_codex_auth_is_mounted_only_for_model_tasks(isolated_host_environment: Path, tmp_path: Path) -> None:
    auth_path = isolated_host_environment / "auth.json"
    model_task = _task("terminal-bench-db-wal-recovery")

    assert _CODEX_AUTH_TARGET not in _mount_targets(_config(model_task, tmp_path))

    auth_path.write_text("{}", encoding="utf-8")
    model_config = _config(model_task, tmp_path)
    non_model_config = _config(_task("project-database-decision"), tmp_path)

    (mount,) = (mount for mount in model_config.environment.mounts if mount["target"] == _CODEX_AUTH_TARGET)
    assert Path(mount["source"]) == auth_path
    assert mount["read_only"] is True
    assert _CODEX_AUTH_TARGET not in _mount_targets(non_model_config)


def test_agent_proxy_is_forwarded_only_when_configured(monkeypatch, tmp_path: Path) -> None:
    task = _task("project-database-decision")
    assert "HTTPS_PROXY" not in _agent_env(_config(task, tmp_path))

    monkeypatch.setenv("POWERCONTEXT_E2E_AGENT_PROXY_URL", "http://proxy.invalid:3128")
    env = _agent_env(_config(task, tmp_path))

    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert env[name] == "http://proxy.invalid:3128"
    assert "powercontext" in env["NO_PROXY"].split(",")


def test_batch_job_binds_scopes_per_invocation_instead_of_per_job(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("POWERCONTEXT_BUB_SCOPE_ID", "host-scope")
    tasks = tuple(task for task in _TASKS if "batch:acceptance" in task.categories)
    settings = HarnessSettings(repository=_REPOSITORY)
    runtime = prepare_runtime_task(tasks, output_dir=tmp_path, settings=settings, failure_policy="collect-all")
    invocation_scopes = ("scope-a1", "scope-a2", "scope-b", "scope-c")

    config = _config(tasks[0], tmp_path, runtime=runtime, invocation_scopes=invocation_scopes)

    (agent,) = config.agents
    assert "POWERCONTEXT_BUB_SCOPE_ID" not in agent.env
    assert agent.kwargs["invocation_scopes"] == invocation_scopes
    assert config.datasets == []
    assert [task.path for task in config.tasks] == [runtime.task_config.path]


def test_model_workload_requires_a_runtime_model(tmp_path: Path) -> None:
    task = _task("terminal-bench-db-wal-recovery")
    output_dir = tmp_path / "out"

    with pytest.raises(ModelNotConfiguredError, match=task.id):
        asyncio.run(run_tasks((task,), output_dir=output_dir, settings=HarnessSettings(repository=_REPOSITORY)))

    assert not output_dir.exists()


def test_off_arm_runs_the_host_without_powercontext(
    monkeypatch, isolated_host_environment: Path, tmp_path: Path
) -> None:
    # Both arms get the Codex login that authenticates Bub's model, at a path that does not name PowerContext.
    (isolated_host_environment / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("BUB_MODEL", "provider:model")
    monkeypatch.setenv("POWERCONTEXT_BUB_BASE_URL", "http://host-gateway:8000")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "server-token")
    task = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "project-decision-continuation.yaml")[0]

    off = _job_config(task, "run-1", None, tmp_path / "off", HarnessSettings(repository=_REPOSITORY))
    on = _job_config(task, "run-1", "scope-1", tmp_path / "on", HarnessSettings(repository=_REPOSITORY))

    (off_agent,) = off.agents
    (on_agent,) = on.agents
    assert not [name for name in off_agent.env if name.startswith("POWERCONTEXT_")]
    assert "server-token" not in off_agent.env.values()
    assert _mount_targets(off) == [_CODEX_AUTH_TARGET]
    assert not _powercontext_mounts(off)
    assert _powercontext_mounts(on)
    assert on_agent.env["POWERCONTEXT_BUB_API_TOKEN"] == "server-token"  # noqa: S105 - test value
    assert off_agent.kwargs == {"powercontext": False}
    assert off_agent.env["BUB_MODEL"] == on_agent.env["BUB_MODEL"] == "provider:model"
    assert on_agent.env["POWERCONTEXT_BUB_SCOPE_ID"] == "scope-1"
    assert on_agent.env["POWERCONTEXT_BUB_CAPTURE_EVENTS"] == "true"
    assert on_agent.kwargs == {}


class _PluginHost(NamedTuple):
    name: str
    model: str
    server_url: str
    insecure_http: str
    scope_id: str


_PLUGIN_HOSTS = [
    _PluginHost(
        "codex",
        "POWERCONTEXT_E2E_CODEX_MODEL",
        "POWERCONTEXT_CODEX_SERVER_URL",
        "POWERCONTEXT_CODEX_ALLOW_INSECURE_HTTP",
        "POWERCONTEXT_CODEX_SCOPE_ID",
    ),
    _PluginHost(
        "claude-code",
        "POWERCONTEXT_E2E_CLAUDE_CODE_MODEL",
        "POWERCONTEXT_CLAUDE_SERVER_URL",
        "POWERCONTEXT_CLAUDE_ALLOW_INSECURE_HTTP",
        "POWERCONTEXT_CLAUDE_SCOPE_ID",
    ),
    _PluginHost(
        "opencode",
        "POWERCONTEXT_E2E_OPENCODE_MODEL",
        "POWERCONTEXT_OPENCODE_SERVER_URL",
        "POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP",
        "POWERCONTEXT_OPENCODE_SCOPE_ID",
    ),
]


def _paired_config(tmp_path: Path, host: str, scope_id: str | None) -> JobConfig:
    settings = HarnessSettings(repository=_REPOSITORY)
    output_dir = tmp_path / (scope_id or "off")
    return _job_config(_PAIRED_TASK, "run-1", scope_id, output_dir, settings, host=host_adapter(host))


@pytest.mark.parametrize("host", _PLUGIN_HOSTS, ids=lambda host: host.name)
def test_plugin_host_off_arm_has_nothing_of_powercontext(monkeypatch, tmp_path: Path, host: _PluginHost) -> None:
    # An OFF agent that can find PowerContext files or credentials searches for PowerContext instead of working
    # like a host without it.
    monkeypatch.setenv(host.model, "model-test")
    monkeypatch.setenv(host.server_url, "http://host-gateway:8000")
    monkeypatch.setenv(host.insecure_http, "true")
    monkeypatch.setenv(host.scope_id, "host-scope")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", "server-token")

    off, on = _paired_config(tmp_path, host.name, None), _paired_config(tmp_path, host.name, "scope-1")

    (off_agent,) = off.agents
    (on_agent,) = on.agents
    assert off.environment.mounts == []
    assert _powercontext_mounts(on)
    assert off_agent.env == {}
    assert on_agent.env[host.scope_id] == "scope-1"
    assert on_agent.env[host.insecure_http] == "true"
    assert on_agent.env[host.server_url.replace("SERVER_URL", "AUTHORIZATION")] == "Bearer server-token"
    assert off_agent.import_path == on_agent.import_path
    assert off_agent.model_name == on_agent.model_name == "model-test"
    assert off_agent.kwargs == {**on_agent.kwargs, "powercontext": False}


@pytest.mark.parametrize("agent_class", [PowerContextCodexAgent, PowerContextClaudeCodeAgent])
@pytest.mark.parametrize("powercontext", [True, False])
def test_plugin_agents_install_the_plugin_only_for_on(tmp_path: Path, agent_class: type, powercontext: bool) -> None:
    agent = agent_class(
        logs_dir=tmp_path,
        model_name="model-test",
        server_url="http://host-gateway:8000",
        powercontext=powercontext,
    )

    # Both agents set the plugin up before each session, in the command Harbor runs to register MCP servers.
    setup = agent._build_register_mcp_servers_command() or ""

    assert ("plugin" in setup and "powercontext" in setup) is powercontext


def test_codex_on_arm_enables_plugins_and_runs_hooks_unattended(tmp_path: Path) -> None:
    def flags(powercontext: bool) -> str:
        return PowerContextCodexAgent(
            logs_dir=tmp_path,
            model_name="gpt-test",
            server_url="http://host-gateway:8000",
            powercontext=powercontext,
        ).build_cli_flags()

    assert "--enable plugins" in flags(True)
    assert "--dangerously-bypass-hook-trust" in flags(True)
    assert "plugins" not in flags(False)
    assert "hook-trust" not in flags(False)


class _RecordingEnvironment:
    """Record the commands an agent runs, succeeding without a container."""

    default_user = None

    def __init__(self, *, failing: str | None = None) -> None:
        self.commands: list[str] = []
        self._failing = failing

    async def exec(self, command: str, **_: object) -> ExecResult:
        self.commands.append(command)
        failed = self._failing is not None and self._failing in command
        return ExecResult(stdout="", stderr="", return_code=1 if failed else 0)


def _opencode_agent(tmp_path: Path, *, powercontext: bool) -> PowerContextOpenCodeAgent:
    return PowerContextOpenCodeAgent(
        logs_dir=tmp_path,
        model_name="openrouter/model-test",
        server_url="http://host-gateway:8000",
        powercontext=powercontext,
        reasoning_effort="medium",
    )


@pytest.mark.parametrize("powercontext", [True, False])
def test_opencode_installs_the_plugin_only_for_on(tmp_path: Path, powercontext: bool) -> None:
    environment = _RecordingEnvironment()

    asyncio.run(_opencode_agent(tmp_path, powercontext=powercontext).install(environment))

    installed = [command for command in environment.commands if "powercontext-opencode.js" in command]
    assert bool(installed) is powercontext


@pytest.mark.parametrize("powercontext", [True, False])
def test_opencode_sessions_start_without_earlier_opencode_sessions(
    monkeypatch, tmp_path: Path, powercontext: bool
) -> None:
    # Harbor leaves OpenCode's data directory in place between the steps of a trial.
    started: list[list[str]] = []

    async def run_opencode(self, instruction, environment, context) -> None:
        started.append(list(environment.commands))

    monkeypatch.setattr(OpenCode, "run", run_opencode)
    environment = _RecordingEnvironment()

    asyncio.run(_opencode_agent(tmp_path, powercontext=powercontext).run("task", environment, AgentContext()))

    (before_session,) = started
    assert any("opencode.db" in command and "rm -rf" in command for command in before_session)


def test_opencode_session_does_not_start_when_earlier_sessions_remain(monkeypatch, tmp_path: Path) -> None:
    # A session store the clear did not reach must stop the arm instead of leaving an earlier session readable.
    started: list[str] = []

    async def run_opencode(self, instruction, environment, context) -> None:
        started.append(instruction)

    monkeypatch.setattr(OpenCode, "run", run_opencode)
    environment = _RecordingEnvironment(failing="opencode session list")

    with pytest.raises(RuntimeError):
        asyncio.run(_opencode_agent(tmp_path, powercontext=False).run("task", environment, AgentContext()))

    assert started == []


def test_opencode_reasoning_effort_selects_the_model_variant(tmp_path: Path) -> None:
    assert _opencode_agent(tmp_path, powercontext=True).build_cli_flags() == "--variant medium"


@pytest.mark.parametrize("host", _PLUGIN_HOSTS, ids=lambda host: host.name)
def test_plugin_host_requires_a_model_and_the_server_url_before_any_run(monkeypatch, host: _PluginHost) -> None:
    adapter = host_adapter(host.name)

    with pytest.raises(ModelNotConfiguredError, match=f"{host.model}, {host.server_url}"):
        require_runtime_models((_PAIRED_TASK,), adapter)

    monkeypatch.setenv(host.model, "model-test")
    monkeypatch.setenv(host.server_url, "http://host-gateway:8000")
    require_runtime_models((_PAIRED_TASK,), adapter)


@pytest.mark.parametrize("host", ["bub", "codex", "claude-code", "opencode"])
@pytest.mark.parametrize(
    "manifest",
    ["paired-tasks/project-decision-continuation.yaml", "tasks/acceptance-01-project-database-decision.yaml"],
)
def test_agent_container_cannot_read_workload_answers(monkeypatch, tmp_path: Path, manifest: str, host: str) -> None:
    # The agent can search its container, so no mount may expose task files, answer keys, or benchmark data.
    for plugin_host in _PLUGIN_HOSTS:
        monkeypatch.setenv(plugin_host.server_url, "http://host-gateway:8000")
    task = load_tasks(_REPOSITORY / "e2e" / "bub" / manifest)[0]
    protected = [_REPOSITORY / "e2e" / "bub" / name for name in ("harbor-tasks", "paired-tasks", "tasks")]
    protected.append(_REPOSITORY / "benchmark")

    sources = [Path(mount["source"]) for mount in _config(task, tmp_path, host=host_adapter(host)).environment.mounts]

    assert not [
        (source, path)
        for source in sources
        for path in protected
        if path.is_relative_to(source) or source.is_relative_to(path)
    ]
