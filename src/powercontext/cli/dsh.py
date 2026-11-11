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

"""Install and diagnose the DeepSeek Harness PowerContext plugin."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from powercontext.cli.dsh_runtime import DshProfile, DshTarget, dsh_home
from powercontext.cli.guidance import SKILL_DIRECTORY, refresh_skill
from powercontext.cli.installation import publish
from powercontext.cli.memory_protocol import installed_memory_protocol
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import configure_connection
from powercontext.paths import powercontext_data_dir

DSH_PLUGIN_NAME = "powercontext-dsh"
DSH_PLUGIN_RELATIVE = Path("integrations") / "dsh" / "plugins" / "powercontext"
DSH_PROFILE = "web"
DSH_BUNDLE = Path("lib") / "index.js"


def require_built_plugin(path: Path) -> None:
    """Reject a plugin directory that cannot be loaded by DeepSeek Harness."""

    if not (path / DSH_BUNDLE).is_file():
        raise SetupError(f"PowerContext DSH plugin at {path} is missing lib/index.js. Build the plugin before setup.")  # noqa: TRY003


def _desktop_diagnostics(target: DshTarget) -> dict[str, Diagnostic]:
    """Compose registration with Desktop's parser without booting its reserved profile."""
    from powercontext.cli.dsh_transport import read_dsh_settings

    try:
        settings = read_dsh_settings(profile=target.profile, executable=target.command, require_installed=True)
        protocol = installed_memory_protocol(settings.get("memoryProtocol"), "dsh")
    except ValueError as error:
        plugin = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
        protocol = Diagnostic(DiagnosticStatus.SKIPPED, "installed plugin could not be inspected")
    else:
        plugin = Diagnostic(
            status=DiagnosticStatus.OK,
            detail="powercontext-dsh is registered in the Desktop profile. Reopen Desktop and run /pc doctor "
            "inside that session to check the running host and Server.",
            checks={
                "registration": "present",
                "running_host_configuration": "not_observed",
                "server_liveness": "not_checked",
                "server_readiness": "not_checked",
                "route_compatibility": "not_checked",
                "session_scope": "not_checked",
            },
        )
    return {
        "dsh": Diagnostic(status=DiagnosticStatus.OK, detail=target.command or "Desktop-installed dsh"),
        "plugin": plugin,
        "plugin_memory_protocol": protocol,
    }


def dsh_executable() -> str:
    """Return a subprocess-launchable DeepSeek Harness CLI path."""

    if os.name == "nt":
        cmd = which("dsh.cmd")
        if cmd is not None:
            return cmd
    executable = which("dsh")
    if executable is None:
        raise SetupError("DeepSeek Harness CLI is not installed or is not on PATH.")  # noqa: TRY003
    return executable


def plugin_id_installed(output: str) -> bool:
    """Return True when dump-config lists the plugin id, not just the package name."""

    expected = {
        f"id: {DSH_PLUGIN_NAME}",
        f'id: "{DSH_PLUGIN_NAME}"',
        f"id: '{DSH_PLUGIN_NAME}'",
    }
    return any(raw.strip().lstrip("-").strip() in expected for raw in output.splitlines())


def _run_dsh(*arguments: str, executable: str | None = None) -> str:
    command = [executable or dsh_executable(), *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed dsh executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Cannot run {' '.join(command)}: {error}") from error  # noqa: TRY003
    if completed.returncode != 0:
        detail = (
            (completed.stderr or "").strip() or (completed.stdout or "").strip() or f"exit code {completed.returncode}"
        )
        raise SetupError(f"`{' '.join(command)}` failed: {detail}") from subprocess.CalledProcessError(  # noqa: TRY003
            completed.returncode, command
        )
    return completed.stdout or ""


__all__ = ["DSH_PLUGIN_NAME", "dsh_executable", "plugin_id_installed", "require_built_plugin"]


@dataclass
class DshInstaller:
    dsh_target: DshTarget | None = None

    def install(self, source):
        target = self.dsh_target
        profile = target.profile if target else DshProfile.WEB
        executable = target.command if target and target.command else dsh_executable()
        with source.package("dsh", linked=True) as package:
            require_built_plugin(package)
            skill = dsh_home() / SKILL_DIRECTORY
            skill.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=skill.parent) as temporary:
                staged = Path(temporary) / "skill"
                if skill.exists():
                    shutil.copytree(skill, staged)
                refresh_skill(package / SKILL_DIRECTORY, staged, previous=skill)
                publish(
                    [(staged, skill)],
                    action=lambda: _run_dsh("plugin", "--profile", profile, "add", str(package), executable=executable),
                )
            return {
                "plugin": DSH_PLUGIN_NAME,
                "plugin_path": str(package),
                "data_dir": str(powercontext_data_dir()),
                "profile": profile,
                "profile_dir": str(dsh_home() / "profiles" / profile),
                "skill_path": str(skill),
            }

    def configure(self, connection):
        from powercontext.cli.dsh_transport import read_dsh_settings, validate_dsh_setup_transport

        settings = read_dsh_settings(
            profile=self.dsh_target.profile if self.dsh_target else DshProfile.WEB,
            executable=self.dsh_target.command if self.dsh_target else None,
            require_installed=True,
        )
        validate_dsh_setup_transport(settings, connection.server_url, connection.allow_insecure_http)
        return {
            "authorization_state": configure_connection(
                connection, dsh_target=self.dsh_target or DshTarget(DshProfile.WEB)
            )
        }

    def verify(self) -> dict[str, Diagnostic]:
        """Collect diagnostics for the optional DeepSeek Harness integration."""
        target = self.dsh_target

        try:
            executable = target.command if target and target.command else dsh_executable()
        except SetupError:
            return {
                "dsh": Diagnostic(
                    status=DiagnosticStatus.FAILED,
                    detail="DeepSeek Harness CLI is not installed or is not on PATH",
                ),
                "plugin": Diagnostic(
                    status=DiagnosticStatus.SKIPPED,
                    detail="not checked because DeepSeek Harness CLI is unavailable",
                ),
            }
        if target and target.profile == DshProfile.DESKTOP:
            return _desktop_diagnostics(target)
        try:
            output = _run_dsh(
                "--profile", DSH_PROFILE, "--dump-config", **({"executable": executable} if target else {})
            )
        except SetupError as error:
            cause = error.__cause__
            checks = {"dump_config": "failed"}
            detail = "dsh --profile web --dump-config failed"
            if isinstance(cause, subprocess.TimeoutExpired):
                checks["dump_config"] = "timeout"
                detail = "dsh --profile web --dump-config exceeded the 120-second deadline"
            elif isinstance(cause, subprocess.CalledProcessError):
                checks["exit_code"] = str(cause.returncode)
                detail = f"dsh --profile web --dump-config exited with code {cause.returncode}"
            elif isinstance(cause, OSError):
                checks["dump_config"] = "launch_failed"
                detail = "could not launch dsh --profile web --dump-config; verify the executable and its permissions"
            return {
                "dsh": Diagnostic(
                    status=DiagnosticStatus.FAILED,
                    detail=(
                        f"{detail}; inspect the Web profile locally for configuration "
                        "or plugin-load errors. Raw host output is withheld because it can contain credentials."
                    ),
                    checks=checks,
                ),
                "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
            }
        installed = plugin_id_installed(output)
        from powercontext.cli.dsh_transport import read_dsh_settings

        try:
            settings = read_dsh_settings(profile=DSH_PROFILE, executable=executable, require_installed=True)
            protocol = installed_memory_protocol(settings.get("memoryProtocol"), "dsh")
        except ValueError:
            protocol = installed_memory_protocol(None, "dsh")
        return {
            "dsh": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
            "plugin_memory_protocol": protocol,
            "plugin": Diagnostic(
                status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
                detail=(
                    f"{DSH_PLUGIN_NAME} is registered in the Web profile. This standalone check cannot observe "
                    "the running DSH process environment or overrides; run /pc doctor inside that session."
                    if installed
                    else "PowerContext DSH plugin is not registered in the Web profile; run powercontext setup dsh."
                ),
                checks={
                    "registration": "present" if installed else "missing",
                    "running_host_configuration": "not_observed",
                    "server_liveness": "not_checked",
                    "server_readiness": "not_checked",
                    "route_compatibility": "not_checked",
                    "session_scope": "not_checked",
                },
            ),
        }
