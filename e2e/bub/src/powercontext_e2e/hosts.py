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

from typing import Any, Protocol

from harbor.models.trial.config import AgentConfig, ServiceVolumeConfig

from .catalog import E2ETask, MemoryEvaluationSpec
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION, REMOTE_CODEX_AUTH
from .settings import bub_environment, codex_auth_path, powercontext_bub_environment


class HostAdapter(Protocol):
    """Own the parts of a Harbor job that depend on the agent host rather than the workload."""

    version: str
    protocol_version: str

    def model_configured(self) -> bool:
        """Report whether the runtime selected a model for model-backed workloads."""

    def agent_model(self) -> str | None:
        """Return the runtime-selected model recorded in evidence."""

    def mounts(self, task: E2ETask) -> list[ServiceVolumeConfig]:
        """Return host-owned bind mounts for the task container."""

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        """Bind the host to one job Scope, or to one Scope per agent invocation when given."""


class BubHost:
    """Run Bub through its ACP server with the local PowerContext Bub plugin."""

    version = BUB_VERSION
    protocol_version = BUB_ACP_SERVER_VERSION

    def model_configured(self) -> bool:
        return "BUB_MODEL" in bub_environment()

    def agent_model(self) -> str | None:
        return bub_environment().get("BUB_MODEL")

    def mounts(self, task: E2ETask) -> list[ServiceVolumeConfig]:
        if not task.execution.model or not (auth_path := codex_auth_path()).is_file():
            return []
        return [
            {
                "type": "bind",
                "source": str(auth_path),
                "target": REMOTE_CODEX_AUTH,
                "read_only": True,
                "bind": {"create_host_path": False},
            }
        ]

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        evaluation = task.evaluation
        env = powercontext_bub_environment()
        if task.execution.model:
            env.update(bub_environment())
        else:
            env.update({"BUB_API_KEY": "null", "BUB_FALLBACK_MODELS": "null"})
        env.update({
            "BUB_HOME": "/installed-agent/bub-home",
            "BUB_MAX_STEPS": str(task.execution.max_steps),
            "BUB_MAX_TOKENS": str(task.execution.max_tokens),
            "CODEX_HOME": "/installed-agent/codex",
            "POWERCONTEXT_BUB_CAPTURE_CHECKPOINT_EVERY": str(
                evaluation.checkpoint_every_events if isinstance(evaluation, MemoryEvaluationSpec) else 5
            ),
            "POWERCONTEXT_BUB_CAPTURE_EVENTS": str(
                evaluation.capture_events if isinstance(evaluation, MemoryEvaluationSpec) else False
            ).lower(),
            "POWERCONTEXT_BUB_CAPTURE_LOG": "/logs/agent/powercontext-capture.jsonl",
            "POWERCONTEXT_BUB_CAPTURE_MAX_BYTES": str(
                evaluation.max_event_bytes if isinstance(evaluation, MemoryEvaluationSpec) else 8192
            ),
            "POWERCONTEXT_BUB_SCOPE_ID": scope_id,
        })
        kwargs: dict[str, Any] = {}
        if invocation_scopes is not None:
            env.pop("POWERCONTEXT_BUB_SCOPE_ID")
            kwargs["invocation_scopes"] = invocation_scopes
        return AgentConfig(
            import_path="powercontext_e2e.harbor_agent:PowerContextBubAcpAgent",
            env=env,
            kwargs=kwargs,
        )


_HOSTS: dict[str, HostAdapter] = {"bub": BubHost()}


def host_adapter(task: E2ETask) -> HostAdapter:
    """Return the adapter for the host a workload declares in its execution spec."""

    return _HOSTS[task.execution.type]
