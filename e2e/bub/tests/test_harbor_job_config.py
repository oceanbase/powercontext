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
import http.server
import json
import os
import shutil
import socket
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from typing import NamedTuple
from urllib.parse import urlsplit

import pytest
from harbor.agents.installed import acp as harbor_acp
from harbor.agents.installed.base import NonZeroAgentExitCodeError
from harbor.agents.installed.claude_code import ClaudeCode
from harbor.agents.installed.codex import Codex
from harbor.agents.installed.opencode import OpenCode
from harbor.agents.installed.pi import Pi
from harbor.environments.base import BaseEnvironment, ExecResult
from harbor.models.agent.context import AgentContext
from harbor.models.job.config import JobConfig
from harbor.models.task.config import TaskOS
from harbor.utils.env import resolve_env_vars

from powercontext_e2e import harbor_agent, harbor_kimi_code
from powercontext_e2e.catalog import E2ETask, load_tasks
from powercontext_e2e.harbor_agent import PowerContextBubAcpAgent
from powercontext_e2e.harbor_claude_code import PowerContextClaudeCodeAgent
from powercontext_e2e.harbor_codex import PowerContextCodexAgent
from powercontext_e2e.harbor_kimi_code import PowerContextKimiCodeAgent
from powercontext_e2e.harbor_opencode import PowerContextOpenCodeAgent
from powercontext_e2e.harbor_pi import PowerContextPiAgent
from powercontext_e2e.hosts import host_adapter
from powercontext_e2e.runner import _job_config, prepare_runtime_task, require_runtime_models, run_tasks
from powercontext_e2e.settings import (
    HarnessSettings,
    ModelNotConfiguredError,
    powercontext_bub_environment,
    prefixed_environment,
)

_REPOSITORY = Path(__file__).resolve().parents[3]
_TASKS = load_tasks(_REPOSITORY / "e2e" / "bub" / "tasks")
_CODEX_AUTH_TARGET = "/run/agent-auth/codex-auth.json"
_PAIRED_TASK = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / "project-decision-continuation.yaml")[0]


@pytest.fixture(autouse=True)
def step_tests_dir(monkeypatch, tmp_path: Path) -> Path:
    """Keep every agent's pre-session tests reset inside the test's own directory, never the host's /tests."""

    tests = tmp_path / "tests"
    monkeypatch.setattr(harbor_agent, "STEP_TESTS_DIR", str(tests))
    return tests


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


def test_agent_proxy_is_forwarded_by_reference_only_when_configured(monkeypatch, tmp_path: Path) -> None:
    # Harbor does not treat the proxy names as sensitive and would write a literal URL, credentials included, to
    # its job files.
    task = _task("project-database-decision")
    assert "HTTPS_PROXY" not in _agent_env(_config(task, tmp_path))

    proxy_url = "http://user:pass@proxy.invalid:3128"
    monkeypatch.setenv("POWERCONTEXT_E2E_AGENT_PROXY_URL", proxy_url)
    # A developer's or CI's own proxy settings, which the harness's requests follow, stay as they are.
    host_proxy = _host_proxy_settings()
    config = _config(task, tmp_path)
    env = _agent_env(config)

    assert proxy_url not in config.model_dump_json()
    assert "proxy.invalid" not in config.model_dump_json()
    resolved = resolve_env_vars(env)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        assert resolved[name] == proxy_url
    assert "powercontext" in env["NO_PROXY"].split(",")
    assert _host_proxy_settings() == host_proxy


def _host_proxy_settings() -> dict[str, str]:
    return {name: value for name, value in os.environ.items() if name.lower() in ("http_proxy", "https_proxy")}


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
    assert resolve_env_vars(on_agent.env)["POWERCONTEXT_BUB_API_TOKEN"] == "server-token"  # noqa: S105 - test value
    assert off_agent.kwargs == {"powercontext": False}
    assert off_agent.env["BUB_MODEL"] == on_agent.env["BUB_MODEL"] == "provider:model"
    assert on_agent.env["POWERCONTEXT_BUB_SCOPE_ID"] == "scope-1"
    assert on_agent.env["POWERCONTEXT_BUB_CAPTURE_EVENTS"] == "true"
    assert on_agent.kwargs == {}


@pytest.mark.parametrize("manifest", ["project-decision-continuation.yaml", "swebench-pro"])
def test_bub_on_arm_captures_every_paired_workload(monkeypatch, isolated_host_environment: Path, manifest: str) -> None:
    # Bub captures nothing by default; the ON arm must record the session on both paired workload kinds, or the
    # Server never sees a Source and every run is an integration failure.
    monkeypatch.setenv("BUB_MODEL", "provider:model")
    monkeypatch.setenv("POWERCONTEXT_BUB_BASE_URL", "http://host-gateway:8000")
    task = load_tasks(_REPOSITORY / "e2e" / "bub" / "paired-tasks" / manifest)[0]

    (on_agent,) = _job_config(
        task, "run-1", "scope-1", isolated_host_environment / "on", HarnessSettings(repository=_REPOSITORY)
    ).agents

    assert on_agent.env["POWERCONTEXT_BUB_CAPTURE_EVENTS"] == "true"


class _PluginHost(NamedTuple):
    name: str
    model: str
    server_url: str
    insecure_http: str | None
    scope_id: str
    # The setting, named like the Server URL's, that carries the Server token, and whether it has the Bearer scheme.
    credential: str = "AUTHORIZATION"
    bearer: bool = True


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
    _PluginHost(
        "pi",
        "POWERCONTEXT_E2E_PI_MODEL",
        "POWERCONTEXT_PI_SERVER_URL",
        "POWERCONTEXT_PI_ALLOW_INSECURE_HTTP",
        "POWERCONTEXT_PI_SCOPE_ID",
    ),
    # Kimi Code's plugin has no plain-HTTP consent: it accepts plain HTTP on loopback only, where the harness relays.
    _PluginHost(
        "kimi-code",
        "POWERCONTEXT_E2E_KIMI_CODE_MODEL",
        "POWERCONTEXT_KIMI_SERVER_URL",
        None,
        "POWERCONTEXT_KIMI_SCOPE_ID",
        credential="TOKEN",
        bearer=False,
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
    if host.insecure_http is not None:
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
    if host.insecure_http is not None:
        assert on_agent.env[host.insecure_http] == "true"
    credential = host.server_url.replace("SERVER_URL", host.credential)
    assert resolve_env_vars(on_agent.env)[credential] == ("Bearer server-token" if host.bearer else "server-token")
    assert off_agent.import_path == on_agent.import_path
    assert off_agent.model_name == on_agent.model_name == "model-test"
    assert off_agent.kwargs == {**on_agent.kwargs, "powercontext": False}


@pytest.mark.parametrize("host", ["bub", "codex", "claude-code", "opencode", "pi", "kimi-code"])
def test_job_files_hold_no_part_of_a_short_server_token(monkeypatch, tmp_path: Path, host: str) -> None:
    # Harbor writes the job configuration to its job files and keeps the first four and last three characters of a
    # sensitive value, which is most of a token this short.
    token = "QzJ9QzJ9Qz"  # noqa: S105 - test value
    monkeypatch.setenv("BUB_MODEL", "provider:model")
    for plugin_host in _PLUGIN_HOSTS:
        monkeypatch.setenv(plugin_host.model, "model-test")
        monkeypatch.setenv(plugin_host.server_url, "http://host-gateway:8000")
    monkeypatch.setenv("POWERCONTEXT_CLIENT_API_TOKEN", token)

    config = _paired_config(tmp_path, host, "scope-1")

    written = config.model_dump_json()
    assert not [part for part in (token, token[:4], token[-3:]) if part in written]
    (agent,) = config.agents
    assert [value for value in resolve_env_vars(agent.env).values() if value in (token, f"Bearer {token}")]
    # The harness holds the value under its own name: an integration's native environment, which a later job reads
    # again, never returns it.
    adapter = host_adapter(host)
    native = powercontext_bub_environment() if host == "bub" else prefixed_environment(adapter.plugin_prefix)
    assert not [name for name, value in native.items() if token in value]


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
        self.envs: list[dict[str, str]] = []
        self._failing = failing

    async def exec(self, command: str, **kwargs: object) -> ExecResult:
        self.commands.append(command)
        self.envs.append(kwargs.get("env") or {})  # type: ignore[arg-type]
        failed = self._failing is not None and self._failing in command
        return ExecResult(stdout="", stderr="", return_code=1 if failed else 0)

    async def empty_dirs(self, dirs, *, chmod: bool = True) -> ExecResult:
        return await self.exec(f"empty_dirs {' '.join(str(path) for path in dirs)}")

    async def upload_file(self, *, source_path: Path, target_path: str) -> None:
        self.commands.append(f"upload {source_path.name} {target_path}")
        self.envs.append({})


@pytest.mark.parametrize(
    ("setting", "expected"),
    [(None, "https://pypi.org/simple"), ("https://mirror.test/simple", "https://mirror.test/simple")],
)
def test_bub_runtime_install_does_not_use_the_task_images_pip_index(
    monkeypatch, tmp_path: Path, setting: str | None, expected: str
) -> None:
    # SWE-bench Pro images configure pip for the index their build used, which no longer answers. The host's own
    # PIP_INDEX_URL is not forwarded: a host mirror is often unreachable from a container.
    monkeypatch.setenv("PIP_INDEX_URL", "http://127.0.0.1:3141/simple")
    if setting is None:
        monkeypatch.delenv("POWERCONTEXT_E2E_PIP_INDEX_URL", raising=False)
    else:
        monkeypatch.setenv("POWERCONTEXT_E2E_PIP_INDEX_URL", setting)
    environment = _RecordingEnvironment()

    asyncio.run(PowerContextBubAcpAgent(logs_dir=tmp_path, powercontext=False).install(environment))

    (install_env,) = [
        env for command, env in zip(environment.commands, environment.envs, strict=True) if "pip install" in command
    ]
    assert install_env["PIP_INDEX_URL"] == expected


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


class _ShellEnvironment:
    """Run an agent's commands in a local shell with its own home and temporary directories."""

    default_user = None

    def __init__(self, root: Path, *, sessions: str = "", env: dict[str, str] | None = None) -> None:
        self.home = root / "home"
        self.tmp = root / "tmp"
        bin_dir = root / "bin"
        for directory in (self.home, self.tmp, bin_dir):
            directory.mkdir(parents=True)
        # Stands in for the OpenCode CLI, which answers `session list` with these sessions.
        opencode = bin_dir / "opencode"
        opencode.write_text(f"#!/bin/sh\nprintf '%s' '{sessions}'\n")
        opencode.chmod(0o755)
        self._env = {"HOME": str(self.home), "TMPDIR": str(self.tmp), "PATH": f"{bin_dir}:/usr/bin:/bin", **(env or {})}
        self.commands: list[tuple[str, object]] = []

    async def empty_dirs(self, dirs, *, chmod: bool = True) -> ExecResult:
        # Harbor's own reset command for a Linux container, run here as the test user.
        linux = SimpleNamespace(os=TaskOS.LINUX)
        return await self.exec(BaseEnvironment._empty_dirs_command(linux, dirs, chmod=chmod))

    async def exec(self, command: str, **kwargs: object) -> ExecResult:
        self.commands.append((command, kwargs.get("user")))
        # Harbor runs agent commands with bash, and prefixes them with `set -o pipefail`, which dash rejects.
        process = await asyncio.create_subprocess_exec(
            "bash", "-c", command, env=self._env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        return ExecResult(stdout=stdout.decode(), stderr=stderr.decode(), return_code=process.returncode)


def _leave_an_earlier_session(environment: _ShellEnvironment) -> Path:
    data = environment.home / ".local" / "share" / "opencode"
    for relative in (
        "opencode.db",
        "opencode.db-wal",
        "tool-output/tool_01",
        "plans/plan.md",
        "storage/session/ses_01.json",
        "log/opencode.log",
        "auth.json",
    ):
        path = data / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("The team chose OceanBase with 12 shards.")
    (environment.tmp / "opencode").mkdir()
    (environment.tmp / "opencode" / "notes.txt").write_text("The team chose OceanBase with 12 shards.")
    return data


@pytest.mark.parametrize("powercontext", [True, False])
def test_opencode_sessions_start_without_anything_an_earlier_session_left(
    monkeypatch, tmp_path: Path, powercontext: bool
) -> None:
    # Harbor leaves OpenCode's data directory in place between the steps of a trial. Besides the session database,
    # OpenCode keeps oversized tool results there, which an OFF session could otherwise read.
    started: list[set[str]] = []
    environment = _ShellEnvironment(tmp_path)
    data = _leave_an_earlier_session(environment)

    async def run_opencode(self, instruction, environment, context) -> None:
        started.append({path.relative_to(data).as_posix() for path in data.rglob("*") if path.is_file()})

    monkeypatch.setattr(OpenCode, "run", run_opencode)

    asyncio.run(_opencode_agent(tmp_path, powercontext=powercontext).run("task", environment, AgentContext()))

    assert started == [{"auth.json"}]
    assert not (environment.tmp / "opencode").exists()


def test_opencode_session_does_not_start_when_earlier_sessions_remain(monkeypatch, tmp_path: Path) -> None:
    # A session database the clear did not reach must stop the arm instead of leaving an earlier session readable.
    started: list[str] = []

    async def run_opencode(self, instruction, environment, context) -> None:
        started.append(instruction)

    monkeypatch.setattr(OpenCode, "run", run_opencode)
    environment = _ShellEnvironment(tmp_path, sessions='[{"id": "ses_01"}]')

    with pytest.raises(RuntimeError):
        asyncio.run(_opencode_agent(tmp_path, powercontext=False).run("task", environment, AgentContext()))

    assert started == []


def test_opencode_reasoning_effort_selects_the_model_variant(tmp_path: Path) -> None:
    assert _opencode_agent(tmp_path, powercontext=True).build_cli_flags() == "--variant medium"


def _pi_agent(tmp_path: Path, *, powercontext: bool) -> PowerContextPiAgent:
    return PowerContextPiAgent(
        logs_dir=tmp_path,
        model_name="openrouter/model-test",
        server_url="http://host-gateway:8000",
        powercontext=powercontext,
    )


@pytest.mark.parametrize("powercontext", [True, False])
def test_pi_installs_the_package_only_for_on(tmp_path: Path, powercontext: bool) -> None:
    environment = _RecordingEnvironment()

    asyncio.run(_pi_agent(tmp_path, powercontext=powercontext).install(environment))

    installed = [command for command in environment.commands if "pi install" in command]
    assert bool(installed) is powercontext


@pytest.mark.parametrize("powercontext", [True, False])
def test_pi_sessions_start_without_tool_output_an_earlier_session_left(
    monkeypatch, tmp_path: Path, powercontext: bool
) -> None:
    # Harbor leaves the container's temporary directory in place between the steps of a trial. Pi's bash tool keeps
    # the full output of a truncated command there, which a later session could otherwise read.
    started: list[set[str]] = []
    environment = _ShellEnvironment(tmp_path)
    (environment.tmp / "pi-bash-0123456789abcdef.log").write_text("The team chose OceanBase with 12 shards.")
    (environment.tmp / "notes.txt").write_text("Not Pi's file.")

    async def run_pi(self, instruction, environment, context) -> None:
        started.append({path.name for path in environment.tmp.iterdir()})
        _leave_pi_output(self)

    monkeypatch.setattr(Pi, "run", run_pi)

    asyncio.run(_pi_agent(tmp_path, powercontext=powercontext).run("task", environment, AgentContext()))

    assert started == [{"notes.txt"}]


def test_pi_sessions_start_when_no_earlier_tool_output_exists(monkeypatch, tmp_path: Path) -> None:
    started: list[str] = []

    async def run_pi(self, instruction, environment, context) -> None:
        started.append(instruction)
        _leave_pi_output(self)

    monkeypatch.setattr(Pi, "run", run_pi)

    asyncio.run(_pi_agent(tmp_path, powercontext=False).run("task", _ShellEnvironment(tmp_path), AgentContext()))

    assert started == ["task"]


def test_pi_runs_without_a_saved_session(tmp_path: Path) -> None:
    # Pi saves every session unless told not to, so the harness relies on Harbor passing `--no-session`.
    environment = _RecordingEnvironment()
    agent = _pi_agent(tmp_path, powercontext=False)
    _leave_pi_output(agent)

    asyncio.run(agent.run("task", environment, AgentContext()))

    assert any("pi --print" in command and "--no-session" in command for command in environment.commands)


def _leave_pi_output(agent: Pi, *lines: str) -> None:
    # Harbor's Pi agent tees Pi's events to this file in the container, which Harbor's Docker environment mounts.
    (agent.logs_dir / "pi.txt").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def _pi_message(stop_reason: str, **fields: str) -> str:
    # Pi serializes with JSON.stringify, which leaves U+2028 and other non-ASCII characters unescaped.
    return json.dumps(
        {"type": "message_end", "message": {"role": "assistant", "stopReason": stop_reason, **fields}},
        ensure_ascii=False,
    )


def _run_pi_with_output(monkeypatch, tmp_path: Path, *lines: str) -> None:
    async def run_pi(self, instruction, environment, context) -> None:
        _leave_pi_output(self, *lines)

    monkeypatch.setattr(Pi, "run", run_pi)
    asyncio.run(_pi_agent(tmp_path, powercontext=False).run("task", _RecordingEnvironment(), AgentContext()))


def test_pi_session_whose_model_request_failed_is_an_error(monkeypatch, tmp_path: Path) -> None:
    # In the JSON mode Harbor uses, Pi exits 0 after a failed model request, which would otherwise be graded as an
    # attempt that did not answer.
    with pytest.raises(NonZeroAgentExitCodeError, match="401: invalid key"):
        _run_pi_with_output(monkeypatch, tmp_path, _pi_message("error", errorMessage="401: invalid key"))


def test_pi_session_whose_output_is_not_on_the_host_is_an_error(monkeypatch, tmp_path: Path) -> None:
    # The failure check reads Pi's output before Harbor downloads the agent's logs, so it depends on Harbor's Docker
    # environment mounting them. Without the file, every failed session would otherwise count as an attempt.
    async def run_pi(self, instruction, environment, context) -> None:
        pass

    monkeypatch.setattr(Pi, "run", run_pi)

    with pytest.raises(RuntimeError, match="mounts /logs"):
        asyncio.run(_pi_agent(tmp_path, powercontext=False).run("task", _RecordingEnvironment(), AgentContext()))


def test_pi_session_that_recovered_from_a_failed_model_request_is_an_attempt(monkeypatch, tmp_path: Path) -> None:
    _run_pi_with_output(
        monkeypatch,
        tmp_path,
        "Warning: not an event",
        _pi_message("error", errorMessage="429: rate limited"),
        _pi_message("stop"),
    )


# JSON leaves these unescaped inside a string, and str.splitlines would split a record at each of them.
_LINE_SEPARATORS = ["\u2028", "\u2029", "\u0085"]


@pytest.mark.parametrize("separator", _LINE_SEPARATORS, ids=lambda s: f"U+{ord(s):04X}")
def test_pi_message_text_cannot_change_the_run_classification(monkeypatch, tmp_path: Path, separator: str) -> None:
    # A retry answered with this text would otherwise lose its message_end, and the stale 429 would exclude the arm.
    _run_pi_with_output(
        monkeypatch,
        tmp_path,
        _pi_message("error", errorMessage="429: rate limited"),
        _pi_message("stop", content=f"The team chose OceanBase.{separator}It runs 12 shards."),
    )

    with pytest.raises(NonZeroAgentExitCodeError, match="invalid key"):
        _run_pi_with_output(
            monkeypatch,
            tmp_path,
            _pi_message("stop", content="An earlier turn."),
            _pi_message("error", errorMessage=f"401: invalid key{separator}request id 7"),
        )


def _kimi_code_agent(
    tmp_path: Path, *, powercontext: bool, server_url: str = "http://host-gateway:8000"
) -> PowerContextKimiCodeAgent:
    return PowerContextKimiCodeAgent(
        logs_dir=tmp_path / "agent-logs",
        model_name="openrouter/model-test",
        server_url=server_url,
        powercontext=powercontext,
        reasoning_effort="medium",
    )


def test_kimi_code_installs_the_plugin_only_for_on(tmp_path: Path) -> None:
    def install(powercontext: bool) -> list[str]:
        environment = _RecordingEnvironment()
        asyncio.run(_kimi_code_agent(tmp_path, powercontext=powercontext).install(environment))
        return environment.commands

    off, on = install(False), install(True)

    # Both arms install the same Kimi Code with the same model configuration, and ON then adds the plugin.
    assert not [command for command in off if "powercontext" in command]
    assert on[: len(off)] == off
    assert on[len(off) :]
    assert all("plugins/managed/powercontext" in command for command in on[len(off) :])


@pytest.mark.parametrize(
    "server_url", ["http://host-gateway:8000/pc", "https://powercontext.test/mcp", "ftp://host-gateway:8000"]
)
def test_kimi_code_job_rejects_a_server_url_the_plugin_cannot_use(monkeypatch, tmp_path: Path, server_url: str) -> None:
    # The plugin takes the Server only as an origin and otherwise stays inactive, which each ON run would report as an
    # integration failure after spending its trial, so the job fails before its container starts.
    monkeypatch.setenv("POWERCONTEXT_E2E_KIMI_CODE_MODEL", "openrouter/model-test")
    monkeypatch.setenv("POWERCONTEXT_KIMI_SERVER_URL", server_url)

    with pytest.raises(ValueError, match="origin"):
        _paired_config(tmp_path, "kimi-code", "scope-1")


def _node_executable() -> str | None:
    """Return the Node binary itself, which runs under a test's own HOME and PATH, unlike a version manager's shim."""

    if (node := shutil.which("node")) is None:
        return None
    try:
        located = subprocess.run(  # noqa: S603 - the Node found on PATH, with fixed arguments
            [node, "-p", "process.execPath"], capture_output=True, text=True, check=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return located.stdout.strip() or None


_NODE = _node_executable()
_needs_node = pytest.mark.skipif(_NODE is None, reason="the plugin installation and the relay run Node, as Kimi Code")


@_needs_node
def test_kimi_code_relay_forwards_requests_to_the_server(tmp_path: Path) -> None:
    # The plugin reaches a plain-HTTP Server only through the relay, so a relay that does not forward a request whole
    # leaves every ON run without PowerContext.
    body = os.urandom(300_000)

    class Echo(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            received = self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(200)
            self.send_header("Content-Length", str(len(received)))
            self.end_headers()
            self.wfile.write(received)

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Echo)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    with socket.socket() as unused:
        unused.bind(("127.0.0.1", 0))
        listen = unused.getsockname()[1]
    relay = subprocess.Popen(  # noqa: S603 - fixed interpreter and arguments
        [_NODE or "node", "-e", harbor_kimi_code.RELAY, str(listen), "127.0.0.1", str(server.server_port)]
    )
    try:
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", listen), timeout=1).close()
                break
            except OSError:
                time.sleep(0.05)
        request = urllib.request.Request(f"http://127.0.0.1:{listen}/v1/context/prepare", data=body, method="POST")
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - the test's own server
            assert response.read() == body
    finally:
        relay.terminate()
        relay.wait(timeout=10)
        server.shutdown()


def _mounted_kimi_plugin(monkeypatch, tmp_path: Path) -> Path:
    """Copy what the ON container mounts of the plugin, and point the agent's installation at the copy."""

    source = tmp_path / "source"
    for path in host_adapter("kimi-code").plugin_paths:
        if (_REPOSITORY / path).is_dir():
            shutil.copytree(_REPOSITORY / path, source / path)
        else:
            (source / path).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_REPOSITORY / path, source / path)
    plugin = source / "integrations" / "kimi-code" / "plugins" / "powercontext"
    monkeypatch.setattr(harbor_kimi_code, "REMOTE_PLUGIN", str(plugin))
    return plugin


def _install_kimi_plugin(tmp_path: Path, plugin_url: str, *, token: str | None) -> Path:
    env = {harbor_kimi_code.KIMI_SCOPE: "scope-1", "PATH": f"{Path(_NODE or '').parent}:/usr/bin:/bin"}
    if token is not None:
        env[harbor_kimi_code.KIMI_TOKEN] = token
    environment = _ShellEnvironment(tmp_path / "harness", env=env)
    result = asyncio.run(environment.exec(harbor_kimi_code.install_plugin_command(plugin_url)))
    assert result.return_code == 0, result.stderr
    return environment.home / ".kimi-code"


def _kimi_installation(home: Path) -> dict[str, object]:
    files: dict[str, object] = {}
    for path in sorted((home / "plugins").rglob("*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8")
            if (
                path.relative_to(home).as_posix()
                == "plugins/managed/powercontext/skills/powercontext-project-context/SKILL.md"
            ):
                # Each installation must point at its own installed script before paths are normalized for comparison.
                script = home / "plugins/managed/powercontext/scripts/scope.mjs"
                encoded_script = json.dumps(str(script), ensure_ascii=False)
                assert script.is_file()
                assert encoded_script in text
                assert "__POWERCONTEXT_SCOPE_SCRIPT_JSON__" not in text
                text = text.replace(encoded_script, '"<installed-scope-script>"')
            if path.suffix == ".json":
                files[path.relative_to(home).as_posix()] = json.loads(text)
            else:
                if path.name == "SKILL.md":
                    assert "__POWERCONTEXT_SCOPE_SCRIPT_JSON__" not in text
                # The guidance names the installed Scope script by its path inside this test's directories.
                text = text.replace(str(home.resolve()), "<home>").replace(str(home), "<home>")
                files[path.relative_to(home).as_posix()] = text
    for record in files["plugins/installed.json"]["plugins"]:  # type: ignore[index]
        # Each installation records its own time, and its source and root inside this test's directories.
        for name in ("installedAt", "updatedAt", "originalSource"):
            del record[name]
        record["root"] = Path(record["root"]).resolve().relative_to(home.resolve()).as_posix()
    return files


@_needs_node
@pytest.mark.parametrize("token", ["server-token", None], ids=["authenticated", "unauthenticated"])
def test_kimi_code_on_arm_installs_the_plugin_as_setup_does(monkeypatch, tmp_path: Path, token: str | None) -> None:
    # Kimi Code installs plugins only from its interactive interface, so the ON arm writes what `powercontext setup
    # kimi-code` writes, from the files the container mounts. The harness itself depends on PowerContext's client only.
    from powercontext.cli import kimi_code as kimi_setup
    from powercontext.cli.installation import setup as install_plugin

    plugin = _mounted_kimi_plugin(monkeypatch, tmp_path)
    plugin_url = f"http://127.0.0.1:{harbor_kimi_code.RELAY_PORT}"
    harness = _install_kimi_plugin(tmp_path, plugin_url, token=token)

    monkeypatch.setenv("KIMI_CODE_HOME", str(tmp_path / "setup-home"))
    monkeypatch.setenv("POWERCONTEXT_CLIENT_CONFIG_FILE", str(tmp_path / "clients.json"))
    if token is not None:
        monkeypatch.setenv(harbor_kimi_code.KIMI_TOKEN, token)
    monkeypatch.setattr(kimi_setup, "kimi_executable", lambda: _NODE)
    install_plugin("kimi-code", source=str(plugin), server_url=plugin_url, scope_id="scope-1")

    assert _kimi_installation(harness) == _kimi_installation(tmp_path / "setup-home")


@_needs_node
@pytest.mark.parametrize("server_url", ["http://host-gateway:8000", "https://powercontext.test"])
def test_kimi_code_plugin_accepts_the_settings_the_harness_writes(monkeypatch, tmp_path: Path, server_url: str) -> None:
    # The plugin accepts plain HTTP only on loopback and otherwise stays inactive, so a container that reaches the
    # Server at host-gateway must give it the relay's address.
    _mounted_kimi_plugin(monkeypatch, tmp_path)
    plugin_url = harbor_kimi_code.plugin_endpoint(server_url)[0]
    home = _install_kimi_plugin(tmp_path, plugin_url, token="server-token")  # noqa: S106 - test value
    runtime = home / "plugins" / "managed" / "powercontext" / "shared" / "runtime.mjs"

    loaded = subprocess.run(  # noqa: S603 - fixed interpreter and arguments
        [
            _NODE or "node",
            "--input-type=module",
            "-e",
            "const runtime = await import(process.argv[1]); "
            "console.log(JSON.stringify(runtime.loadSettings({ credentials: false })))",
            str(runtime),
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    settings = json.loads(loaded.stdout)
    assert settings["serverUrl"] == plugin_url
    assert settings["scopeId"] == "scope-1"
    assert settings["useToken"] is True
    assert (urlsplit(plugin_url).hostname == "127.0.0.1") is server_url.startswith("http:")


def _kimi_environment(
    monkeypatch,
    tmp_path: Path,
    *,
    sessions: str = "[]",
    logs: dict[str, list[dict[str, object]]] | None = None,
    status: int = 0,
) -> tuple[_ShellEnvironment, Path]:
    """Run the agent's commands with a stand-in Kimi Code.

    The stand-in records what a session could read when it started, leaves ``logs`` as the session's event logs, one
    per agent, and exits with ``status``.
    """

    environment = _ShellEnvironment(tmp_path)
    record = tmp_path / "session-start.txt"
    session = tmp_path / "session"
    for agent_id, events in (logs or {}).items():
        log = session / "wd_workspace_1" / "session_1" / "agents" / agent_id / "wire.jsonl"
        log.parent.mkdir(parents=True)
        log.write_text("".join(json.dumps(event) + "\n" for event in events))
    bin_dir = tmp_path / "bin"
    (bin_dir / "kimi").write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        f"  session) printf '%s' '{sessions}' ;;\n"
        f'  -p) {{ find "$HOME/.kimi-code" -type f; find "{tmp_path / "tests"}" -type f; }} > "{record}"\n'
        f'      if [ -d "{session}" ]; then\n'
        f'        mkdir -p "$HOME/.kimi-code/sessions" && cp -R "{session}/." "$HOME/.kimi-code/sessions/"\n'
        "      fi\n"
        f"      exit {status} ;;\n"
        "  *) exit 2 ;;\n"
        "esac\n"
    )
    (bin_dir / "stdbuf").write_text('#!/bin/sh\nshift\nexec "$@"\n')
    for name in ("kimi", "stdbuf"):
        (bin_dir / name).chmod(0o755)
    (environment.home / ".nvm").mkdir()
    (environment.home / ".nvm" / "nvm.sh").write_text(":\n")
    # Harbor's Docker environment mounts the agent's logs directory where the container writes its agent logs.
    agent_logs = tmp_path / "agent-logs"
    agent_logs.mkdir()
    monkeypatch.setattr(harbor_kimi_code, "AGENT_LOGS", str(agent_logs))
    monkeypatch.setenv("OPENROUTER_API_KEY", "provider-key")
    return environment, record


@pytest.mark.parametrize("powercontext", [True, False])
def test_kimi_code_sessions_start_without_anything_an_earlier_session_left(
    monkeypatch, tmp_path: Path, powercontext: bool
) -> None:
    # Harbor leaves Kimi Code's home in place between the steps of a trial. Besides the sessions, Kimi Code keeps the
    # files a session edited, a search cache, and logs there, which a later session could otherwise read.
    environment, record = _kimi_environment(monkeypatch, tmp_path)
    home = environment.home / ".kimi-code"
    kept = ["config.toml", "plugins/installed.json", "plugins/managed/powercontext/kimi.plugin.json"]
    earlier = [
        "sessions/wd_workspace_1/session_1/agents/main/wire.jsonl",
        "session_index.jsonl",
        "file-history/wd_workspace_1/1@v2",
        "cache/query-store/shard-00/0.bin",
        "logs/kimi-code.log",
        "powercontext/runtime/attempt-1.json",
    ]
    for relative in kept + earlier:
        (home / relative).parent.mkdir(parents=True, exist_ok=True)
        (home / relative).write_text("The team chose OceanBase with 12 shards.")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test.sh").write_text("EXPECTED = 12")
    agent = _kimi_code_agent(tmp_path, powercontext=powercontext, server_url="https://powercontext.test")

    asyncio.run(agent.run("task", environment, AgentContext()))

    visible = {Path(line).relative_to(home).as_posix() for line in record.read_text().splitlines()}
    assert visible == set(kept)


@pytest.mark.skipif(os.geteuid() == 0, reason="root can remove files from a read-only directory")
def test_kimi_code_session_does_not_start_when_the_earlier_tests_cannot_be_removed(monkeypatch, tmp_path: Path) -> None:
    environment, record = _kimi_environment(monkeypatch, tmp_path)
    (tmp_path / "tests" / "locked").mkdir(parents=True)
    (tmp_path / "tests" / "locked" / "test.sh").write_text("The team chose OceanBase with 12 shards.")
    (tmp_path / "tests" / "locked").chmod(0o555)  # the test user cannot remove files from it
    try:
        with pytest.raises(RuntimeError, match=r"Emptying .* failed"):
            asyncio.run(_kimi_code_agent(tmp_path, powercontext=False).run("task", environment, AgentContext()))
    finally:
        (tmp_path / "tests" / "locked").chmod(0o755)

    assert not record.exists()


def test_kimi_code_session_does_not_start_when_earlier_sessions_remain(monkeypatch, tmp_path: Path) -> None:
    # Sessions the clear did not reach must stop the arm instead of leaving an earlier session readable.
    environment, record = _kimi_environment(monkeypatch, tmp_path, sessions='[{"id": "session_1"}]')

    with pytest.raises(RuntimeError, match="still lists an earlier session"):
        asyncio.run(_kimi_code_agent(tmp_path, powercontext=False).run("task", environment, AgentContext()))

    assert not record.exists()


def _kimi_usage(agent_id: str, other: int, cached: int, output: int) -> dict[str, object]:
    usage = {"inputOther": other, "output": output, "inputCacheRead": cached, "inputCacheCreation": 0}
    return {"type": "usage.record", "agentId": agent_id, "usage": usage, "usageScope": "turn"}


def test_kimi_code_step_usage_counts_each_request_of_every_agent_once(monkeypatch, tmp_path: Path) -> None:
    # Kimi Code writes no usage to its output. It records each request in the event log of the agent that made it, a
    # subagent's in the subagent's own log, and repeats the usage on the message the request produced.
    first = _kimi_usage("main", 1_000, 9_000, 50)
    repeated = {"type": "agent.message.appended", "message": {"meta": {"usage": first["usage"]}}}
    logs = {
        "main": [first, repeated, _kimi_usage("main", 300, 10_000, 20)],
        "agent-0": [_kimi_usage("agent-0", 2_000, 0, 70)],
    }
    environment, _ = _kimi_environment(monkeypatch, tmp_path, logs=logs)
    agent = _kimi_code_agent(tmp_path, powercontext=False)
    context = AgentContext()

    asyncio.run(agent.run("task", environment, context))
    agent.populate_context_post_run(context)

    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (22_300, 19_000, 140)


def test_kimi_code_step_that_failed_keeps_the_usage_of_its_requests(monkeypatch, tmp_path: Path) -> None:
    # A session that ends with an error, or is stopped by a timeout, has still spent tokens on the requests it made.
    logs = {"main": [_kimi_usage("main", 500, 1_500, 40)]}
    environment, _ = _kimi_environment(monkeypatch, tmp_path, logs=logs, status=1)
    agent = _kimi_code_agent(tmp_path, powercontext=False)
    context = AgentContext()

    with pytest.raises(NonZeroAgentExitCodeError):
        asyncio.run(agent.run("task", environment, context))
    agent.populate_context_post_run(context)

    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (2_000, 1_500, 40)


def test_kimi_code_step_without_recorded_requests_reports_no_usage(tmp_path: Path) -> None:
    context = AgentContext()

    _kimi_code_agent(tmp_path, powercontext=False).populate_context_post_run(context)

    assert (context.n_input_tokens, context.n_cache_tokens, context.n_output_tokens) == (None, None, None)


def test_kimi_code_takes_only_a_model_the_harness_can_configure(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="provider/model"):
        PowerContextKimiCodeAgent(
            logs_dir=tmp_path, model_name="moonshot/kimi-k2", server_url="http://host-gateway:8000", powercontext=False
        )


@pytest.mark.parametrize("powercontext", [True, False])
def test_bub_sessions_start_without_the_tapes_an_earlier_session_left(
    monkeypatch, tmp_path: Path, powercontext: bool
) -> None:
    # Bub keeps every session's messages in its home, which Harbor leaves in place between the steps of a trial, so
    # a later session could otherwise read what the user said in an earlier one.
    started: list[bool] = []
    bub_home = tmp_path / "bub-home"
    tape = bub_home / "tapes" / "session.jsonl"
    tape.parent.mkdir(parents=True)
    tape.write_text("The team chose OceanBase with 12 shards.")
    environment = _ShellEnvironment(tmp_path, env={"BUB_HOME": str(bub_home)})

    async def run_bub(self, instruction, environment, context) -> None:
        started.append(tape.exists())

    monkeypatch.setattr(harbor_acp.AcpAgent, "run", run_bub)
    monkeypatch.setattr(harbor_agent, "STEP_FAILURE_MARKER", str(tmp_path / "step-failed"))

    agent = PowerContextBubAcpAgent(logs_dir=tmp_path, powercontext=powercontext)
    asyncio.run(agent.run("task", environment, AgentContext()))

    assert started == [False]


def _agent(agent_class: type, tmp_path: Path, *, powercontext: bool):
    if agent_class is PowerContextBubAcpAgent:
        return PowerContextBubAcpAgent(logs_dir=tmp_path, powercontext=powercontext)
    kwargs = {"reasoning_effort": "medium"} if agent_class in (PowerContextOpenCodeAgent, PowerContextPiAgent) else {}
    return agent_class(
        logs_dir=tmp_path,
        model_name="openrouter/model-test",
        server_url="http://host-gateway:8000",
        powercontext=powercontext,
        **kwargs,
    )


@pytest.mark.parametrize("left", ["directory", "symlink"])
@pytest.mark.parametrize("powercontext", [True, False])
@pytest.mark.parametrize(
    ("agent_class", "harbor_class"),
    [
        (PowerContextBubAcpAgent, harbor_acp.AcpAgent),
        (PowerContextCodexAgent, Codex),
        (PowerContextClaudeCodeAgent, ClaudeCode),
        (PowerContextOpenCodeAgent, OpenCode),
        (PowerContextPiAgent, Pi),
    ],
)
def test_sessions_start_without_the_tests_an_earlier_step_left(
    monkeypatch, tmp_path: Path, agent_class: type, harbor_class: type, powercontext: bool, left: str
) -> None:
    # Harbor uploads each step's tests before its verifier and leaves them in the container, so a later session
    # could read an earlier step's verifier, which hints at a continuation workload's answer.
    started: list[list[str]] = []
    tests = tmp_path / "tests"
    earlier = tmp_path / "earlier-tests"
    (earlier / "nested").mkdir(parents=True)
    (earlier / "test.sh").write_text("# Copyright OceanBase: the team chose OceanBase with 12 shards.")
    (earlier / "nested" / "grade.py").write_text("EXPECTED = 12")
    if left == "symlink":
        tests.symlink_to(earlier)
    else:
        shutil.copytree(earlier, tests)
    environment = _ShellEnvironment(tmp_path, env={"BUB_HOME": str(tmp_path / "bub-home")})

    async def run_host(self, instruction, environment, context) -> None:
        started.append(sorted(path.name for path in tests.rglob("*")))
        if harbor_class is Pi:
            _leave_pi_output(self)

    monkeypatch.setattr(harbor_class, "run", run_host)
    monkeypatch.setattr(harbor_agent, "STEP_FAILURE_MARKER", str(tmp_path / "step-failed"))
    monkeypatch.setattr(harbor_agent, "STEP_TESTS_DIR", str(tests))

    asyncio.run(_agent(agent_class, tmp_path, powercontext=powercontext).run("task", environment, AgentContext()))

    assert started == [[]]
    assert tests.is_dir()
    assert not tests.is_symlink()


@pytest.mark.parametrize(
    ("agent_class", "harbor_class"),
    [
        (PowerContextBubAcpAgent, harbor_acp.AcpAgent),
        (PowerContextCodexAgent, Codex),
        (PowerContextClaudeCodeAgent, ClaudeCode),
        (PowerContextOpenCodeAgent, OpenCode),
        (PowerContextPiAgent, Pi),
    ],
)
@pytest.mark.skipif(os.geteuid() == 0, reason="root can remove files from a read-only directory")
def test_a_session_does_not_start_when_the_earlier_tests_cannot_be_removed(
    monkeypatch, tmp_path: Path, agent_class: type, harbor_class: type
) -> None:
    # Harbor's environments report a failed command instead of raising; the session must still not start.
    started: list[bool] = []
    tests = tmp_path / "tests"
    (tests / "locked").mkdir(parents=True)
    (tests / "locked" / "test.sh").write_text("The team chose OceanBase with 12 shards.")
    (tests / "locked").chmod(0o555)  # the test user cannot remove files from it
    environment = _ShellEnvironment(tmp_path, env={"BUB_HOME": str(tmp_path / "bub-home")})
    marker = tmp_path / "step-failed"

    async def run_host(self, instruction, environment, context) -> None:
        started.append(True)

    monkeypatch.setattr(harbor_class, "run", run_host)
    monkeypatch.setattr(harbor_agent, "STEP_FAILURE_MARKER", str(marker))
    monkeypatch.setattr(harbor_agent, "STEP_TESTS_DIR", str(tests))
    try:
        with pytest.raises(RuntimeError, match=r"Emptying .* failed"):
            asyncio.run(_agent(agent_class, tmp_path, powercontext=True).run("task", environment, AgentContext()))
    finally:
        (tests / "locked").chmod(0o755)

    assert started == []
    assert (tests / "locked" / "test.sh").exists()
    if agent_class is PowerContextBubAcpAgent:
        assert marker.exists()


def test_bub_step_whose_tapes_could_not_be_removed_is_marked_failed(monkeypatch, tmp_path: Path) -> None:
    # The verifier reads a missing marker as a passed step, so a step that fails before Bub starts must still leave
    # the marker, or Harbor would score it 1 and a fail-fast batch would run on.
    started: list[bool] = []
    marker = tmp_path / "step-failed"
    environment = _ShellEnvironment(tmp_path, env={})  # no BUB_HOME: the removal refuses to run

    async def run_bub(self, instruction, environment, context) -> None:
        started.append(True)

    monkeypatch.setattr(harbor_acp.AcpAgent, "run", run_bub)
    monkeypatch.setattr(harbor_agent, "STEP_FAILURE_MARKER", str(marker))

    agent = PowerContextBubAcpAgent(logs_dir=tmp_path, powercontext=True)
    with pytest.raises(RuntimeError, match="BUB_HOME: parameter null or not set"):
        asyncio.run(agent.run("task", environment, AgentContext()))

    assert marker.exists()
    assert started == []


@pytest.mark.parametrize("host", _PLUGIN_HOSTS, ids=lambda host: host.name)
def test_plugin_host_requires_a_model_and_the_server_url_before_any_run(monkeypatch, host: _PluginHost) -> None:
    adapter = host_adapter(host.name)

    with pytest.raises(ModelNotConfiguredError, match=f"{host.model}, {host.server_url}"):
        require_runtime_models((_PAIRED_TASK,), adapter)

    monkeypatch.setenv(host.model, "model-test")
    monkeypatch.setenv(host.server_url, "http://host-gateway:8000")
    require_runtime_models((_PAIRED_TASK,), adapter)


@pytest.mark.parametrize("host", ["bub", "codex", "claude-code", "opencode", "pi", "kimi-code"])
@pytest.mark.parametrize(
    "manifest",
    ["paired-tasks/project-decision-continuation.yaml", "tasks/acceptance-01-project-database-decision.yaml"],
)
def test_agent_container_cannot_read_workload_answers(monkeypatch, tmp_path: Path, manifest: str, host: str) -> None:
    # The agent can search its container, so no mount may expose task files, answer keys, or benchmark data.
    for plugin_host in _PLUGIN_HOSTS:
        monkeypatch.setenv(plugin_host.server_url, "http://host-gateway:8000")
    task = load_tasks(_REPOSITORY / "e2e" / "bub" / manifest)[0]
    protected = [
        _REPOSITORY / "e2e" / "bub" / name for name in ("harbor-tasks", "memorycode-tasks", "paired-tasks", "tasks")
    ]
    protected.append(_REPOSITORY / "evaluation")

    sources = [Path(mount["source"]) for mount in _config(task, tmp_path, host=host_adapter(host)).environment.mounts]

    assert not [
        (source, path)
        for source in sources
        for path in protected
        if path.is_relative_to(source) or source.is_relative_to(path)
    ]
