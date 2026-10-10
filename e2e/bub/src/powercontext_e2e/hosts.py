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
from dataclasses import dataclass
from os import environ
from pathlib import Path
from typing import Any, Protocol

from harbor.models.trial.config import AgentConfig, ServiceVolumeConfig

from .catalog import E2ETask, MemoryEvaluationSpec, is_paired
from .harbor_agent import BUB_ACP_SERVER_VERSION, BUB_VERSION, REMOTE_CODEX_AUTH, REMOTE_SOURCE
from .harbor_claude_code import CLAUDE_CODE_VERSION
from .harbor_codex import CODEX_VERSION
from .harbor_opencode import OPENCODE_VERSION
from .harbor_pi import PI_VERSION
from .settings import (
    agent_secret,
    bub_environment,
    codex_auth_path,
    powercontext_bub_environment,
    prefixed_environment,
    server_api_token,
)

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

    def mounts(self, task: E2ETask, repository: Path, *, powercontext: bool) -> list[ServiceVolumeConfig]:
        """Return host-owned bind mounts for the task container.

        Only a run with PowerContext gets the integration sources it installs, so an agent without PowerContext cannot
        find PowerContext in its container.
        """

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str | None,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        """Configure the host agent for one Harbor job.

        With ``scope_id``, the host runs with its PowerContext integration bound to that Scope, or to one Scope per
        agent invocation when ``invocation_scopes`` is given, and authenticated with the harness Client's token.
        Without it, the host runs as a user without PowerContext has it: no integration and no credential.
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

    def mounts(self, task: E2ETask, repository: Path, *, powercontext: bool) -> list[ServiceVolumeConfig]:
        paths = (*POWERCONTEXT_PACKAGE_PATHS, "integrations/bub", "e2e/bub/source-overrides.txt")
        mounts = source_mounts(repository, paths) if powercontext else []
        # The Codex login authenticates Bub's model, so both arms get it.
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
            if (token := server_api_token()) is not None:
                env["POWERCONTEXT_BUB_API_TOKEN"] = agent_secret("POWERCONTEXT_BUB_API_TOKEN", token)
            if invocation_scopes is not None:
                env.pop("POWERCONTEXT_BUB_SCOPE_ID")
                kwargs["invocation_scopes"] = invocation_scopes
        return AgentConfig(
            import_path="powercontext_e2e.harbor_agent:PowerContextBubAcpAgent",
            env=env,
            kwargs=kwargs,
        )


@dataclass(frozen=True)
class PluginHost:
    """Run a host through Harbor's own agent for it, with the host's PowerContext plugin only in the ON arm.

    Only the ON arm installs the plugin and receives the plugin's native ``<plugin_prefix>*`` settings, with
    ``<plugin_prefix>SCOPE_ID`` bound to the arm's Scope and ``<plugin_prefix>AUTHORIZATION`` carrying the harness
    Client's token. ``<plugin_prefix>SERVER_URL`` names the Server as the agent container reaches it. The harness
    selects the model with ``<setting_prefix>MODEL`` and passes the reasoning effort from
    ``<setting_prefix>REASONING_EFFORT`` explicitly.
    """

    name: str
    version: str
    agent_import_path: str
    plugin_prefix: str
    setting_prefix: str
    # Only the files the plugin installation reads, so the agent cannot find workload answers in its container.
    plugin_paths: tuple[str, ...]

    @property
    def protocol_version(self) -> str:
        # The harness drives the host through its own non-interactive CLI of the same version.
        return self.version

    def missing_settings(self) -> tuple[str, ...]:
        required = {f"{self.setting_prefix}MODEL": self.agent_model(), self._server_url_setting: self._server_url()}
        return tuple(name for name, value in required.items() if value is None)

    def agent_model(self) -> str | None:
        return environ.get(f"{self.setting_prefix}MODEL") or None

    def agent_settings(self) -> dict[str, str]:
        # The published SWE-bench Pro run used medium reasoning. Passing it explicitly also keeps a Harbor fallback,
        # such as CLAUDE_CODE_EFFORT_LEVEL, from changing it unrecorded.
        return {"reasoning_effort": environ.get(f"{self.setting_prefix}REASONING_EFFORT") or "medium"}

    def mounts(self, task: E2ETask, repository: Path, *, powercontext: bool) -> list[ServiceVolumeConfig]:
        return source_mounts(repository, self.plugin_paths) if powercontext else []

    def agent_config(
        self,
        task: E2ETask,
        *,
        scope_id: str | None,
        invocation_scopes: tuple[str, ...] | None,
    ) -> AgentConfig:
        if invocation_scopes is not None:
            raise ValueError(f"The {self.name} host binds one Scope per job and cannot run batched workloads")  # noqa: TRY003
        if (server_url := self._server_url()) is None:
            raise ValueError(f"{self._server_url_setting} must name the Server as the agent container reaches it")  # noqa: TRY003
        env: dict[str, str] = {}
        if scope_id is not None:
            env = {**self._plugin_environment(), f"{self.plugin_prefix}SCOPE_ID": scope_id}
            if (token := server_api_token()) is not None:
                # Each plugin sends this value as its Authorization header.
                authorization = f"{self.plugin_prefix}AUTHORIZATION"
                env[authorization] = agent_secret(authorization, f"Bearer {token}")
        return AgentConfig(
            import_path=self.agent_import_path,
            model_name=self.agent_model(),
            env=env,
            kwargs={
                "powercontext": scope_id is not None,
                "server_url": server_url,
                **self.agent_settings(),
            },
        )

    @property
    def _server_url_setting(self) -> str:
        return f"{self.plugin_prefix}SERVER_URL"

    def _server_url(self) -> str | None:
        return self._plugin_environment().get(self._server_url_setting)

    def _plugin_environment(self) -> dict[str, str]:
        return prefixed_environment(self.plugin_prefix)


# Harbor authenticates Codex from CODEX_AUTH_JSON_PATH, CODEX_FORCE_AUTH_JSON, or OPENAI_API_KEY. The plugin's local
# marketplace is integrations/codex.
CODEX = PluginHost(
    name="codex",
    version=CODEX_VERSION,
    agent_import_path="powercontext_e2e.harbor_codex:PowerContextCodexAgent",
    plugin_prefix="POWERCONTEXT_CODEX_",
    setting_prefix="POWERCONTEXT_E2E_CODEX_",
    plugin_paths=("integrations/codex",),
)
# Harbor authenticates Claude Code from CLAUDE_CODE_OAUTH_TOKEN, ANTHROPIC_API_KEY, or ANTHROPIC_AUTH_TOKEN, and also
# forwards ANTHROPIC_BASE_URL. The plugin's marketplace is the repository root, so only its manifest is mounted.
CLAUDE_CODE = PluginHost(
    name="claude-code",
    version=CLAUDE_CODE_VERSION,
    agent_import_path="powercontext_e2e.harbor_claude_code:PowerContextClaudeCodeAgent",
    plugin_prefix="POWERCONTEXT_CLAUDE_",
    setting_prefix="POWERCONTEXT_E2E_CLAUDE_CODE_",
    plugin_paths=(".claude-plugin/marketplace.json", "integrations/claude-code"),
)


# Harbor passes the key of the model's provider, such as OPENROUTER_API_KEY. The plugin is a bundled JavaScript file
# with a Skill, and reads its Server URL from its environment.
OPENCODE = PluginHost(
    name="opencode",
    version=OPENCODE_VERSION,
    agent_import_path="powercontext_e2e.harbor_opencode:PowerContextOpenCodeAgent",
    plugin_prefix="POWERCONTEXT_OPENCODE_",
    setting_prefix="POWERCONTEXT_E2E_OPENCODE_",
    plugin_paths=(
        "integrations/opencode/plugins/powercontext/lib",
        "integrations/opencode/plugins/powercontext/skills",
    ),
)

# Harbor passes the key of the model's provider, such as OPENROUTER_API_KEY. The package's extension is TypeScript that
# Pi loads directly with the Skill, and reads its Server URL from its environment.
PI = PluginHost(
    name="pi",
    version=PI_VERSION,
    agent_import_path="powercontext_e2e.harbor_pi:PowerContextPiAgent",
    plugin_prefix="POWERCONTEXT_PI_",
    setting_prefix="POWERCONTEXT_E2E_PI_",
    plugin_paths=tuple(
        f"integrations/pi/plugins/powercontext/{name}" for name in ("package.json", "extensions", "src", "skills")
    ),
)


def _capture_settings(task: E2ETask) -> tuple[bool, int, int]:
    evaluation = task.evaluation
    if isinstance(evaluation, MemoryEvaluationSpec):
        return evaluation.capture_events, evaluation.checkpoint_every_events, evaluation.max_event_bytes
    # Bub captures nothing automatically by default. The ON arm records every turn so that, like the other hosts'
    # integrations, it captures what the user says without relying on the model to call a memory tool.
    return is_paired(task), 5, 8192


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


_HOSTS: dict[str, HostAdapter] = {host.name: host for host in (BubHost(), CODEX, CLAUDE_CODE, OPENCODE, PI)}


def host_adapter(name: str) -> HostAdapter:
    """Return the adapter for a host by name."""

    return _HOSTS[name]
