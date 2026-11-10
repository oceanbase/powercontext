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

"""Thin Harbor ACP adapter for Bub's native plugin environment."""

from __future__ import annotations

import shlex
from importlib.metadata import version
from os import environ
from pathlib import Path
from typing import Any, override

from harbor.agents.installed import acp as harbor_acp
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

AGENT_ID = "powercontext-bub-acp"
REMOTE_BIN_DIR = "/installed-agent/bin"
REMOTE_BUB_HOME = "/installed-agent/bub-home"
# Bub keeps every session's messages as JSONL in its home, and nothing else clears them between the steps of a trial.
BUB_TAPES = '"${BUB_HOME:?}/tapes"'
REMOTE_BUB_PROJECT = "/installed-agent/bub-project"
REMOTE_CODEX_AUTH = "/run/agent-auth/codex-auth.json"
REMOTE_CODEX_HOME = "/installed-agent/codex"
REMOTE_SOURCE = "/opt/powercontext/source"
REMOTE_SOURCE_OVERRIDE = f"{REMOTE_SOURCE}/e2e/bub/source-overrides.txt"
REMOTE_TOOL_DIR = "/installed-agent/tools"
BUB_VERSION = version("bub")
POWERCONTEXT_VERSION = version("powercontext")
# Change ACP_SDK_REQUIREMENT together with this version: it repeats the server's own pin of the ACP SDK.
BUB_ACP_SERVER_VERSION = "0.1.3"
# bub-acp-server 0.1.3 pins this pre-release. uv accepts a pre-release only when a direct requirement names it, so the
# install must repeat the pin or it cannot resolve the server at all. A server release that pins another SDK version
# conflicts with this requirement and fails to install.
ACP_SDK_REQUIREMENT = "agent-client-protocol==1.0.0rc2"
# The runner venv is a separate interpreter from Bub's plugin environment, and Harbor's acp_runner.py imports the
# helper symbols (text_block, spawn_agent_process) that the reorganized 1.x SDK no longer exports; 0.12.1 is the last
# release that ships them. The runner pin therefore names that release, not the server's pre-release.
ACP_RUNNER_SDK_REQUIREMENT = "agent-client-protocol==0.12.1"
# The runner venv reports the SDK it resolves and fails the setup when a symbol Harbor's acp_runner.py imports is
# missing, so a mismatched install surfaces here instead of as an ImportError in every step's acp.txt.
_RUNNER_SDK_PROBE = (
    "import acp; from importlib.metadata import version; "
    "missing = [name for name in ('text_block', 'spawn_agent_process', 'RequestError', 'PROTOCOL_VERSION') "
    "if not hasattr(acp, name)]; "
    "print('acp-sdk', version('agent-client-protocol'), 'missing:', missing); "
    "raise SystemExit(bool(missing))"
)
STEP_FAILURE_MARKER = "/logs/agent/powercontext-step-failed"
# Harbor uploads each step's tests to this directory before running the step's verifier and leaves them there.
STEP_TESTS_DIR = "/tests"
PYPI_INDEX_URL = "https://pypi.org/simple"
PIP_INDEX_SETTING = "POWERCONTEXT_E2E_PIP_INDEX_URL"


class PowerContextBubAcpAgent(harbor_acp.AcpAgent):
    """Install Bub through its supported uv tool and plugin commands, and start every session without Bub's tapes.

    Bub does not search another session's tape, but an agent can read the files, so each session starts without them.
    """

    def __init__(self, **kwargs: Any) -> None:
        self._invocation_scopes = tuple(kwargs.pop("invocation_scopes", ()))
        self._powercontext = bool(kwargs.pop("powercontext", True))
        self._step_index = 0
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

    @override
    async def run(self, instruction: str, environment: BaseEnvironment, context: AgentContext) -> None:
        try:
            # The marker comes first: the verifier reads a missing marker as a passed step, so every failure
            # after this line, including a failed removal, must leave it in place.
            await environment.exec(command=f"touch {STEP_FAILURE_MARKER}")
            await self.exec_as_agent(environment, command=f"rm -rf {BUB_TAPES}")
            await clear_step_tests(environment)
            if not self._invocation_scopes:
                await super().run(instruction, environment, context)
            else:
                if self._step_index >= len(self._invocation_scopes):
                    invocation = self._step_index + 1
                    raise RuntimeError(  # noqa: TRY003
                        f"No E2E scope configured for agent invocation {invocation}"
                    )
                scope_id = self._invocation_scopes[self._step_index]
                with environment.scoped_exec_env({"POWERCONTEXT_BUB_SCOPE_ID": scope_id}):
                    await super().run(instruction, environment, context)
            await environment.exec(command=f"rm -f {STEP_FAILURE_MARKER}")
        finally:
            self._step_index += 1

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.exec_as_root(
            environment,
            command=self._build_dependencies_command("uvx"),
            env={"DEBIAN_FRONTEND": "noninteractive", "PIP_INDEX_URL": pip_index_url()},
        )
        await self.exec_as_root(
            environment,
            command=_pin_runner_acp_sdk_command(),
            env={"PIP_INDEX_URL": pip_index_url()},
        )
        await self.exec_as_root(environment, command=_install_bub_command(powercontext=self._powercontext))
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
            f'#!/usr/bin/env sh\nset -eu\nexec {REMOTE_BIN_DIR}/bub acp "$@"\n',
            encoding="utf-8",
        )
        await environment.upload_file(source_path=launcher_path, target_path=self._LAUNCHER_REMOTE_PATH)
        runner_path = Path(harbor_acp.__file__).with_name("acp_runner.py")
        await environment.upload_file(source_path=runner_path, target_path=self._RUNNER_REMOTE_PATH)
        await environment.exec(
            command=f"chmod a+rx {self._LAUNCHER_REMOTE_PATH} {self._RUNNER_REMOTE_PATH}",
            user="root",
        )
        self._selected_distribution_kind = "uvx"


def pip_index_url() -> str:
    """Return the index Harbor's ACP runtime install uses for pip, overriding any the task image configured.

    SWE-bench Pro images keep a pip configuration that names the index their build used, at a loopback address that
    no longer answers (confirmed in the ansible image), so pip inside them cannot install anything until the index is
    overridden. ``POWERCONTEXT_E2E_PIP_INDEX_URL`` names a mirror the containers can reach; the host's own
    ``PIP_INDEX_URL`` is not forwarded, because a host mirror is often unreachable from a container.
    """

    return environ.get(PIP_INDEX_SETTING) or PYPI_INDEX_URL


async def clear_step_tests(environment: BaseEnvironment) -> None:
    """Remove the tests Harbor uploaded for an earlier step's verifier.

    Harbor uploads a step's tests before its verifier runs and leaves them in the container, so a later session
    could read an earlier step's verifier, which can hint at a continuation workload's answer. Each verifier uploads
    its own tests again, so a later step loses nothing. Harbor's own directory reset runs as root, which owns the
    uploaded files, and leaves an empty directory whatever was at the path.
    """

    # Harbor's environments return a failed command rather than raising, and a session must not start with the
    # earlier tests still readable.
    result = await environment.empty_dirs([STEP_TESTS_DIR], chmod=False)
    if result is not None and result.return_code != 0:
        raise RuntimeError(f"Emptying {STEP_TESTS_DIR} failed with exit code {result.return_code}: {result.stderr}")  # noqa: TRY003


def _tool_environment() -> str:
    return f"UV_TOOL_BIN_DIR={shlex.quote(REMOTE_BIN_DIR)} UV_TOOL_DIR={shlex.quote(REMOTE_TOOL_DIR)}"


def _install_bub_command(*, powercontext: bool = True) -> str:
    uv = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin/uv"
    plugin = f"--overrides {REMOTE_SOURCE_OVERRIDE} --with {REMOTE_SOURCE}/integrations/bub " if powercontext else ""
    return (
        "set -eu; "
        f"mkdir -p {REMOTE_BIN_DIR} {REMOTE_BUB_HOME} {REMOTE_BUB_PROJECT} {REMOTE_CODEX_HOME}; "
        f"if [ -f {REMOTE_CODEX_AUTH} ]; then "
        f"cp {REMOTE_CODEX_AUTH} {REMOTE_CODEX_HOME}/auth.json; "
        f"chmod 600 {REMOTE_CODEX_HOME}/auth.json; "
        "fi; "
        f"SETUPTOOLS_SCM_PRETEND_VERSION={shlex.quote(POWERCONTEXT_VERSION)} {_tool_environment()} "
        f"{uv} tool install --force {plugin}"
        f"{shlex.quote(f'bub[app]=={BUB_VERSION}')}"
    )


def _install_acp_server_command() -> str:
    runner_bin = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin"
    # Bub initializes its plugin project with an unpinned requirement, so retain the harness version here.
    return (
        "set -eu; "
        f"PATH={runner_bin}:$PATH BUB_HOME={REMOTE_BUB_HOME} CODEX_HOME={REMOTE_CODEX_HOME} "
        f"BUB_PROJECT={REMOTE_BUB_PROJECT} "
        f"{_tool_environment()} {REMOTE_BIN_DIR}/bub install "
        f"{shlex.quote(f'bub-acp-server=={BUB_ACP_SERVER_VERSION}')} {shlex.quote(ACP_SDK_REQUIREMENT)} "
        f"{shlex.quote(f'bub[app]=={BUB_VERSION}')}"
    )


def _pin_runner_acp_sdk_command() -> str:
    """Name the ACP SDK in Harbor's runner venv, whose own install leaves it unpinned.

    Harbor builds that venv with a bare ``pip install agent-client-protocol`` and then runs its ``acp_runner.py``
    from it. The 1.x releases reorganize the package and no longer export the helper symbols the runner imports,
    so the unpinned install resolves to a runner that dies on ``ImportError: cannot import name 'text_block'``
    and every step ends with "No ACP summary file found" before the agent starts. Pin the venv to the last
    release that still ships those symbols, and let the probe exit non-zero when anything is still missing so the
    failure surfaces in the setup log instead of in every step.
    """

    python = f"{harbor_acp.AcpAgent._RUNNER_VENV_PATH}/bin/python"
    return (
        "set -eu; "
        f"{python} -m pip install --disable-pip-version-check --no-input {shlex.quote(ACP_RUNNER_SDK_REQUIREMENT)}; "
        f"{python} -c {shlex.quote(_RUNNER_SDK_PROBE)}"
    )
