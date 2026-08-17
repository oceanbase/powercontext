"""Thin Harbor ACP adapter for Bub's native plugin environment."""

from __future__ import annotations

import json
import shlex
from importlib.metadata import version
from pathlib import Path
from typing import Any

from harbor.agents.installed import acp as harbor_acp
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext
from harbor.models.trial.paths import EnvironmentPaths

AGENT_ID = "powercontext-bub-acp"
REMOTE_BIN_DIR = "/installed-agent/bin"
REMOTE_BUB_HOME = "/installed-agent/bub-home"
REMOTE_BUB_PROJECT = "/installed-agent/bub-project"
REMOTE_CODEX_AUTH = "/run/powercontext/codex-auth.json"
REMOTE_CODEX_HOME = "/installed-agent/codex"
REMOTE_SOURCE = "/opt/powercontext/source"
REMOTE_SCENARIO_SUPPORT = "/installed-agent/scenario-support.py"
REMOTE_TOOL_DIR = "/installed-agent/tools"
BUB_VERSION = version("bub")
BUB_ACP_SERVER_VERSION = "0.0.2"


class PowerContextBubAcpAgent(harbor_acp.AcpAgent):
    """Install Bub through its supported uv tool and plugin commands."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(
            registry_entry={
                "id": AGENT_ID,
                "name": "PowerContext Bub ACP",
                "version": f"bub-{BUB_VERSION}",
                "description": "Bub with the local PowerContext integration",
                "distribution": {"uvx": {"package": f"bub-acp-server=={BUB_ACP_SERVER_VERSION}"}},
            },
            distribution_preference=["uvx"],
            auth_policy="disabled",
            permission_mode="allow",
            **kwargs,
        )

    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=self._build_dependencies_command("uvx"),
            env={"DEBIAN_FRONTEND": "noninteractive"},
        )
        await self.exec_as_root(environment, command=_install_bub_command())
        await self.exec_as_root(environment, command=_install_acp_server_command())
        agent_user = shlex.quote(str(environment.default_user or "root"))
        await self.exec_as_root(
            environment,
            command=(
                f"chown -R {agent_user} {REMOTE_BIN_DIR} {REMOTE_BUB_HOME} {REMOTE_BUB_PROJECT} "
                f"{REMOTE_CODEX_HOME} {REMOTE_TOOL_DIR}"
            ),
        )

        launcher_path = self.logs_dir / "acp-launch.sh"
        launcher_path.write_text(
            "#!/usr/bin/env sh\n"
            "set -eu\n"
            f'bub_bin=$(dirname "$(readlink -f {REMOTE_BIN_DIR}/bub)")\n'
            'exec "$bub_bin/bub-acp-server" "$@"\n',
            encoding="utf-8",
        )
        await environment.upload_file(source_path=launcher_path, target_path=self._LAUNCHER_REMOTE_PATH)
        runner_path = Path(harbor_acp.__file__).with_name("acp_runner.py")
        await environment.upload_file(source_path=runner_path, target_path=self._RUNNER_REMOTE_PATH)
        scenario_support_path = Path(__file__).with_name("scenario_support.py")
        await environment.upload_file(source_path=scenario_support_path, target_path=REMOTE_SCENARIO_SUPPORT)
        await environment.exec(
            command=f"chmod a+rx {self._LAUNCHER_REMOTE_PATH} {self._RUNNER_REMOTE_PATH} {REMOTE_SCENARIO_SUPPORT}",
            user="root",
        )
        self._selected_distribution_kind = "uvx"

    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        if not _has_scenario_configuration(self.extra_env):
            await super().run(instruction, environment, context)
            return

        del context
        rendered_instruction = self.render_instruction(instruction)
        workspace_archive = self.extra_env.get("POWERCONTEXT_E2E_WORKSPACE_ARCHIVE")
        if self.extra_env.get("POWERCONTEXT_E2E_RESTORE_WORKSPACE") == "true":
            if not workspace_archive:
                raise ValueError("Workspace restore requires POWERCONTEXT_E2E_WORKSPACE_ARCHIVE")  # noqa: TRY003
            await self._workspace_command(environment, "restore", workspace_archive)

        segment_plan = _segment_plan(rendered_instruction, self.extra_env)
        runner_environment = self._runner_environment()
        for index, (prompt, max_steps) in enumerate(segment_plan):
            await self._run_segment(
                environment,
                index=index,
                prompt=prompt,
                max_steps=max_steps,
                runner_environment=runner_environment,
            )

        if self.extra_env.get("POWERCONTEXT_E2E_SAVE_WORKSPACE") == "true":
            if not workspace_archive:
                raise ValueError("Workspace snapshot requires POWERCONTEXT_E2E_WORKSPACE_ARCHIVE")  # noqa: TRY003
            await self._workspace_command(environment, "snapshot", workspace_archive)

        aggregate_command = (
            f"{self._RUNNER_VENV_PATH}/bin/python {REMOTE_SCENARIO_SUPPORT} aggregate "
            f"--logs-dir={shlex.quote(EnvironmentPaths.agent_dir.as_posix())} "
            f"--instruction={shlex.quote(rendered_instruction)}"
        )
        await self.exec_as_agent(environment, command=aggregate_command)

    def _runner_environment(self) -> dict[str, str]:
        registry_entry = self._require_registry_entry()
        environment = {
            "HARBOR_ACP_MCP_SERVERS_JSON": json.dumps(self._build_mcp_servers_payload()),
            "HARBOR_ACP_PERMISSION_MODE": self._permission_mode,
            "HARBOR_ACP_AUTH_POLICY": self._auth_policy,
            "HARBOR_ACP_AGENT_ID": registry_entry.id,
            "HARBOR_ACP_AGENT_VERSION": registry_entry.version,
        }
        if self._authenticate_method_id:
            environment["HARBOR_ACP_AUTHENTICATE_METHOD_ID"] = self._authenticate_method_id
        if self.model_name:
            environment["HARBOR_ACP_REQUESTED_MODEL"] = self.model_name
        return environment

    async def _run_segment(
        self,
        environment: BaseEnvironment,
        *,
        index: int,
        prompt: str,
        max_steps: int,
        runner_environment: dict[str, str],
    ) -> None:
        segment_dir = EnvironmentPaths.agent_dir / "segments" / f"{index:03d}"
        command = (
            f"mkdir -p {shlex.quote(segment_dir.as_posix())}; "
            f"{self._RUNNER_VENV_PATH}/bin/python {self._RUNNER_REMOTE_PATH} "
            f"--instruction={shlex.quote(prompt)} "
            f"--logs-dir={shlex.quote(segment_dir.as_posix())} "
            f"--launcher={self._LAUNCHER_REMOTE_PATH} "
            f"2>&1 | stdbuf -oL tee {shlex.quote((segment_dir / self._OUTPUT_FILENAME).as_posix())} "
            f"| stdbuf -oL tee -a {shlex.quote((EnvironmentPaths.agent_dir / self._OUTPUT_FILENAME).as_posix())}"
        )
        await self.exec_as_agent(
            environment,
            command=command,
            env={**runner_environment, "BUB_MAX_STEPS": str(max_steps)},
        )

    async def _workspace_command(self, environment: BaseEnvironment, command: str, archive: str) -> None:
        support_command = (
            f"{self._RUNNER_VENV_PATH}/bin/python {REMOTE_SCENARIO_SUPPORT} {command} "
            f"--workspace=. --archive={shlex.quote(archive)}"
        )
        await self.exec_as_agent(environment, command=support_command)


def _has_scenario_configuration(environment: dict[str, str]) -> bool:
    return any(
        name.startswith("POWERCONTEXT_E2E_SCENARIO_")
        or name in {"POWERCONTEXT_E2E_RESTORE_WORKSPACE", "POWERCONTEXT_E2E_SAVE_WORKSPACE"}
        for name in environment
    )


def _segment_plan(instruction: str, environment: dict[str, str]) -> tuple[tuple[str, int], ...]:
    max_steps = _positive_int(environment.get("BUB_MAX_STEPS"), name="BUB_MAX_STEPS")
    handoff_value = environment.get("POWERCONTEXT_E2E_SCENARIO_HANDOFF_AFTER_STEPS")
    if handoff_value is not None:
        handoff_steps = _positive_int(handoff_value, name="POWERCONTEXT_E2E_SCENARIO_HANDOFF_AFTER_STEPS")
        if handoff_steps >= max_steps:
            raise ValueError("Handoff step must be smaller than BUB_MAX_STEPS")  # noqa: TRY003
        return ((instruction, handoff_steps), ("continue", max_steps - handoff_steps))

    prompt = "continue" if environment.get("POWERCONTEXT_E2E_SCENARIO_PROMPT") == "continue" else instruction
    segment_steps = _positive_int(
        environment.get("POWERCONTEXT_E2E_SCENARIO_MAX_STEPS", str(max_steps)),
        name="POWERCONTEXT_E2E_SCENARIO_MAX_STEPS",
    )
    return ((prompt, segment_steps),)


def _positive_int(value: str | None, *, name: str) -> int:
    try:
        parsed = int(value or "")
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc  # noqa: TRY003
    if parsed < 1:
        raise ValueError(f"{name} must be positive")  # noqa: TRY003
    return parsed


def _tool_environment() -> str:
    return f"UV_TOOL_BIN_DIR={shlex.quote(REMOTE_BIN_DIR)} UV_TOOL_DIR={shlex.quote(REMOTE_TOOL_DIR)}"


def _install_bub_command() -> str:
    uv = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin/uv"
    return (
        "set -eu; "
        f"mkdir -p {REMOTE_BIN_DIR} {REMOTE_BUB_HOME} {REMOTE_BUB_PROJECT} {REMOTE_CODEX_HOME}; "
        f"if [ -f {REMOTE_CODEX_AUTH} ]; then "
        f"cp {REMOTE_CODEX_AUTH} {REMOTE_CODEX_HOME}/auth.json; "
        f"chmod 600 {REMOTE_CODEX_HOME}/auth.json; "
        "fi; "
        f"{_tool_environment()} {uv} tool install --force "
        f"--with {REMOTE_SOURCE} --with {REMOTE_SOURCE}/integrations/bub "
        f"{shlex.quote(f'bub=={BUB_VERSION}')}"
    )


def _install_acp_server_command() -> str:
    runner_bin = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin"
    return (
        "set -eu; "
        f"PATH={runner_bin}:$PATH BUB_HOME={REMOTE_BUB_HOME} CODEX_HOME={REMOTE_CODEX_HOME} "
        f"BUB_PROJECT={REMOTE_BUB_PROJECT} "
        f"{_tool_environment()} {REMOTE_BIN_DIR}/bub install "
        f"{shlex.quote(f'bub-acp-server=={BUB_ACP_SERVER_VERSION}')}"
    )
