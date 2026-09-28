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

import pytest
from harbor.models.job.config import JobConfig

from powercontext_e2e.catalog import E2ETask, load_tasks
from powercontext_e2e.runner import _job_config, prepare_runtime_task, run_tasks
from powercontext_e2e.settings import HarnessSettings, ModelNotConfiguredError

_REPOSITORY = Path(__file__).resolve().parents[3]
_TASKS = load_tasks(_REPOSITORY / "e2e" / "bub" / "tasks")
_CODEX_AUTH_TARGET = "/run/powercontext/codex-auth.json"


@pytest.fixture(autouse=True)
def isolated_host_environment(monkeypatch, tmp_path: Path) -> Path:
    for name in tuple(os.environ):
        if name.startswith(("BUB_", "POWERCONTEXT_")) or name in {"CODEX_HOME", "GITHUB_SHA"}:
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
    assert {"server-token", "unrelated-key", "host-bub-key", "host-provider-key"}.isdisjoint(env.values())


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
