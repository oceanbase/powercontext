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

"""Host adapters that attach one agent host and its PowerContext integration to a Harbor job."""

from __future__ import annotations

from collections.abc import Iterable
from os import environ
from pathlib import Path
from typing import Any, Protocol

from harbor.models.trial.config import AgentConfig, ServiceVolumeConfig

from .catalog import ContinuationEvaluationSpec, E2ETask, MemoryEvaluationSpec
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION, REMOTE_CODEX_AUTH, REMOTE_SOURCE
from .harbor_codex import CODEX_VERSION
from .settings import bub_environment, codex_auth_path, powercontext_bub_environment, powercontext_codex_environment

# Bub installs its plugin against the local powercontext package, so its container gets that package's sources.
POWERCONTEXT_PACKAGE_PATHS = ("pyproject.toml", "README.md", "LICENSE", "src")


class HostAdapter(Protocol):
    """Own the parts of a Harbor job that depend on the agent host rather than the workload."""

    name: str
    version: str
    protocol_version: str

    def missing_settings(self) -> tuple[str, ...]:
        """Return the runtime settings that model-backed workloads need and the environment does not provide."""

    def agent_model(self) -> str | None:
        """Return the runtime-selected model recorded in evidence."""

    def agent_settings(self) -> dict[str, str]:
        """Return runtime-selected agent settings, other than the model, recorded in evidence."""

    def mounts(self, task: E2ETask, repository: Path) -> list[ServiceVolumeConfig]:
        """Return host-owned bind mounts for the task container, such as the integration sources it installs."""

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str | None,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        """Configure the host agent for one Harbor job.

        With ``scope_id``, the host runs with its PowerContext integration bound to that Scope, or to one Scope per
        agent invocation when ``invocation_scopes`` is given. Without it, the host runs with no PowerContext
        integration installed.
        """


class BubHost:
    """Run Bub through its ACP server with the local PowerContext Bub plugin."""

    name = "bub"
    version = BUB_VERSION
    protocol_version = BUB_ACP_SERVER_VERSION

    def missing_settings(self) -> tuple[str, ...]:
        return () if "BUB_MODEL" in bub_environment() else ("BUB_MODEL",)

    def agent_model(self) -> str | None:
        return bub_environment().get("BUB_MODEL")

    def agent_settings(self) -> dict[str, str]:
        return {}

    def mounts(self, task: E2ETask, repository: Path) -> list[ServiceVolumeConfig]:
        mounts = source_mounts(
            repository,
            (*POWERCONTEXT_PACKAGE_PATHS, "integrations/bub", "e2e/bub/source-overrides.txt"),
        )
        if task.execution.model and (auth_path := codex_auth_path()).is_file():
            mounts.append(read_only_bind(auth_path, REMOTE_CODEX_AUTH))
        return mounts

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str | None,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        env = powercontext_bub_environment() if scope_id is not None else {}
        if task.execution.model:
            env.update(bub_environment())
        else:
            env.update({"BUB_API_KEY": "null", "BUB_FALLBACK_MODELS": "null"})
        env.update({
            "BUB_HOME": "/installed-agent/bub-home",
            "BUB_MAX_STEPS": str(task.execution.max_steps),
            "BUB_MAX_TOKENS": str(task.execution.max_tokens),
            "CODEX_HOME": "/installed-agent/codex",
        })
        kwargs: dict[str, Any] = {}
        if scope_id is None:
            kwargs["powercontext"] = False
        else:
            capture_events, checkpoint_every, max_bytes = _capture_settings(task)
            env.update({
                "POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY": str(checkpoint_every),
                "POWERCONTEXT_BUB_CAPTURE_EVENTS": str(capture_events).lower(),
                "POWERCONTEXT_BUB_CAPTURE_LOG": "/logs/agent/powercontext-capture.jsonl",
                "POWERCONTEXT_BUB_CAPTURE_MAX_BYTES": str(max_bytes),
                "POWERCONTEXT_BUB_SCOPE_ID": scope_id,
            })
            if invocation_scopes is not None:
                env.pop("POWERCONTEXT_BUB_SCOPE_ID")
                kwargs["invocation_scopes"] = invocation_scopes
        return AgentConfig(
            import_path="powercontext_e2e.harbor_agent:PowerContextBubAcpAgent",
            env=env,
            kwargs=kwargs,
        )


class CodexHost:
    """Run Codex CLI through Harbor's Codex agent with the local PowerContext Codex plugin.

    Both arms install the plugin; only the ON arm enables Codex plugins and binds a Scope. Harbor authenticates Codex
    from ``CODEX_AUTH_JSON_PATH``, ``CODEX_FORCE_AUTH_JSON``, or ``OPENAI_API_KEY``.
    """

    name = "codex"
    version = CODEX_VERSION
    # The harness drives Codex through `codex exec --json` of the same CLI.
    protocol_version = CODEX_VERSION

    def missing_settings(self) -> tuple[str, ...]:
        required = {
            "POWERCONTEXT_E2E_CODEX_MODEL": self.agent_model(),
            "POWERCONTEXT_CODEX_SERVER_URL": _codex_server_url(),
        }
        return tuple(name for name, value in required.items() if value is None)

    def agent_model(self) -> str | None:
        return environ.get("POWERCONTEXT_E2E_CODEX_MODEL") or None

    def agent_settings(self) -> dict[str, str]:
        # The published SWE-bench Pro run used medium reasoning; Harbor's own default is high.
        return {"reasoning_effort": environ.get("POWERCONTEXT_E2E_CODEX_REASONING_EFFORT") or "medium"}

    def mounts(self, task: E2ETask, repository: Path) -> list[ServiceVolumeConfig]:
        # The directory is the plugin's local marketplace; Codex copies the plugin into CODEX_HOME from here.
        return source_mounts(repository, ("integrations/codex",))

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str | None,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        if invocation_scopes is not None:
            raise ValueError("The Codex host binds one Scope per job and cannot run batched acceptance workloads")  # noqa: TRY003
        # The plugin reads its Server URL only from the installed .mcp.json, so the harness writes it there.
        if (server_url := _codex_server_url()) is None:
            raise ValueError("POWERCONTEXT_CODEX_SERVER_URL must name the Server as the agent container reaches it")  # noqa: TRY003
        env = {} if scope_id is None else {**powercontext_codex_environment(), "POWERCONTEXT_CODEX_SCOPE_ID": scope_id}
        return AgentConfig(
            import_path="powercontext_e2e.harbor_codex:PowerContextCodexAgent",
            model_name=self.agent_model(),
            env=env,
            kwargs={
                "powercontext": scope_id is not None,
                "server_url": server_url,
                **self.agent_settings(),
            },
        )


def _codex_server_url() -> str | None:
    return powercontext_codex_environment().get("POWERCONTEXT_CODEX_SERVER_URL")


def _capture_settings(task: E2ETask) -> tuple[bool, int, int]:
    evaluation = task.evaluation
    if isinstance(evaluation, MemoryEvaluationSpec):
        return evaluation.capture_events, evaluation.checkpoint_every_events, evaluation.max_event_bytes
    # Bub captures nothing automatically by default. The ON arm records every turn so that, like the other hosts'
    # integrations, it captures what the user says without relying on the model to call a memory tool.
    return isinstance(evaluation, ContinuationEvaluationSpec), 5, 8192


def source_mounts(repository: Path, paths: Iterable[str]) -> list[ServiceVolumeConfig]:
    """Mount selected repository paths read-only at the same relative place under the agent's source directory.

    Only what an installation needs is mounted: the whole repository would also expose workload answer keys and
    benchmark data to the agent.
    """

    return [read_only_bind(repository / path, f"{REMOTE_SOURCE}/{path}") for path in paths]


def read_only_bind(source: Path, target: str) -> ServiceVolumeConfig:
    return {
        "type": "bind",
        "source": str(source),
        "target": target,
        "read_only": True,
        "bind": {"create_host_path": False},
    }


_HOSTS: dict[str, HostAdapter] = {host.name: host for host in (BubHost(), CodexHost())}


def host_adapter(name: str) -> HostAdapter:
    """Return the adapter for a host by name."""

    return _HOSTS[name]
