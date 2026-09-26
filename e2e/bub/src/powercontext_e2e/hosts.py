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
from pathlib import Path
from typing import Any, Protocol

from harbor.models.trial.config import AgentConfig, ServiceVolumeConfig

from .catalog import ContinuationEvaluationSpec, E2ETask, MemoryEvaluationSpec
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION, REMOTE_CODEX_AUTH, REMOTE_SOURCE
from .settings import bub_environment, codex_auth_path, powercontext_bub_environment


class HostAdapter(Protocol):
    """Own the parts of a Harbor job that depend on the agent host rather than the workload."""

    version: str
    protocol_version: str

    def model_configured(self) -> bool:
        """Report whether the runtime selected a model for model-backed workloads."""

    def agent_model(self) -> str | None:
        """Return the runtime-selected model recorded in evidence."""

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

    version = BUB_VERSION
    protocol_version = BUB_ACP_SERVER_VERSION

    def model_configured(self) -> bool:
        return "BUB_MODEL" in bub_environment()

    def agent_model(self) -> str | None:
        return bub_environment().get("BUB_MODEL")

    def mounts(self, task: E2ETask, repository: Path) -> list[ServiceVolumeConfig]:
        mounts = source_mounts(repository, ("integrations/bub", "e2e/bub/source-overrides.txt"))
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


_HOSTS: dict[str, HostAdapter] = {"bub": BubHost()}


def host_adapter(task: E2ETask) -> HostAdapter:
    """Return the adapter for the host a workload declares in its execution spec."""

    return _HOSTS[task.execution.type]
