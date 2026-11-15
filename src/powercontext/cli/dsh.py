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

import json
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
DSH_CLIENT_BUNDLE = Path("lib") / "client.js"
DSH_ASSETS = Path("lib") / "assets"


@dataclass(frozen=True, slots=True)
class PluginCapabilities:
    """Installation and browser-entry capabilities reported separately from connectivity."""

    name: str
    version: str
    host_bundle: str
    browser_client: str
    client_declaration: str
    asset_export: str

    @property
    def browser_ui(self) -> bool:
        """Return whether the package can contribute a browser entry."""

        return self.browser_client == "present" and self.client_declaration == "present"

    def checks(self) -> dict[str, str]:
        """Return stable, non-secret capability checks for ``doctor`` output."""

        return {
            "package": self.name,
            "version": self.version,
            "host_bundle": self.host_bundle,
            "browser_client": self.browser_client,
            "client_declaration": self.client_declaration,
            "asset_export": self.asset_export,
        }

    def as_json(self) -> dict[str, str]:
        """Return the install-result representation."""

        return self.checks()


def _export_target(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        target = value.get("default")
        return target if isinstance(target, str) else None
    return None


def inspect_plugin_package(path: Path) -> PluginCapabilities:
    """Read a built package without executing it or starting the host."""

    try:
        manifest = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SetupError(f"PowerContext DSH plugin at {path} has no readable package.json") from error  # noqa: TRY003
    if not isinstance(manifest, dict) or manifest.get("name") != DSH_PLUGIN_NAME:
        raise SetupError(f"PowerContext DSH plugin at {path} is not named {DSH_PLUGIN_NAME}")  # noqa: TRY003
    raw_version = manifest.get("version")
    version = raw_version.strip() if isinstance(raw_version, str) and raw_version.strip() else "unknown"
    exports = manifest.get("exports")
    client_target = _export_target(exports.get("./client")) if isinstance(exports, dict) else None
    declaration = manifest.get("dsh")
    client_declaration = declaration.get("client") if isinstance(declaration, dict) else None
    declared = (
        isinstance(client_declaration, dict)
        and client_declaration.get("platform") == "web"
        and isinstance(client_target, str)
        and client_target.endswith("client.js")
    )
    try:
        assets = (path / DSH_ASSETS).is_dir() and any((path / DSH_ASSETS).iterdir())
    except OSError:
        assets = False
    return PluginCapabilities(
        name=DSH_PLUGIN_NAME,
        version=version,
        host_bundle="present" if (path / DSH_BUNDLE).is_file() else "missing",
        browser_client="present" if (path / DSH_CLIENT_BUNDLE).is_file() else "missing",
        client_declaration="present" if declared else "missing",
        asset_export="present" if isinstance(exports, dict) and "./assets/*" in exports and assets else "missing",
    )


def installed_plugin_path(target: DshTarget) -> Path | None:
    """Locate the package DSH installed into the selected profile, if present."""

    candidate = target.directory / "node_modules" / DSH_PLUGIN_NAME
    return candidate if (candidate / "package.json").is_file() else None


def capability_checks(target: DshTarget) -> dict[str, str]:
    """Report installed browser-entry capability without claiming host execution."""

    path = installed_plugin_path(target)
    if path is None:
        return {
            "host_bundle": "not_observed",
            "browser_client": "not_observed",
            "client_declaration": "not_observed",
            "asset_export": "not_observed",
        }
    try:
        return inspect_plugin_package(path).checks()
    except SetupError:
        return {
            "host_bundle": "invalid",
            "browser_client": "invalid",
            "client_declaration": "invalid",
            "asset_export": "invalid",
        }


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
        capabilities = capability_checks(target)
        browser_ui = (
            capabilities.get("browser_client") == "present" and capabilities.get("client_declaration") == "present"
        )
        detail = (
            "powercontext-dsh is registered in the Desktop profile. Reopen Desktop and run /pc doctor "
            "inside that session to check the running host and Server. "
            + (
                "Browser client assets are installed; the running host must load the plugin entry "
                "before the sidebar status appears."
                if browser_ui
                else "No browser client is installed; headless tools remain available and the sidebar "
                "status is disabled."
            )
        )
        plugin = Diagnostic(
            status=DiagnosticStatus.OK,
            detail=detail,
            checks={
                "registration": "present",
                "running_host_configuration": "not_observed",
                "server_liveness": "not_checked",
                "server_readiness": "not_checked",
                "route_compatibility": "not_checked",
                "session_scope": "not_checked",
                **capabilities,
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


__all__ = [
    "DSH_PLUGIN_NAME",
    "PluginCapabilities",
    "capability_checks",
    "dsh_executable",
    "inspect_plugin_package",
    "installed_plugin_path",
    "plugin_id_installed",
    "require_built_plugin",
]


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
            capabilities = inspect_plugin_package(package)
            return {
                "plugin": DSH_PLUGIN_NAME,
                "plugin_path": str(package),
                "capabilities": capabilities.as_json(),
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
        resolved_target = target or DshTarget(DshProfile.WEB)
        capabilities = capability_checks(resolved_target)
        browser_ui = (
            capabilities.get("browser_client") == "present" and capabilities.get("client_declaration") == "present"
        )
        if installed:
            detail = (
                f"{DSH_PLUGIN_NAME} is registered in the Web profile. This standalone check cannot observe "
                "the running DSH process environment or overrides; run /pc doctor inside that session. "
                + (
                    "Browser client assets are installed; the running host must load the plugin entry "
                    "before the sidebar status appears."
                    if browser_ui
                    else "No browser client is installed; headless tools remain available and the sidebar "
                    "status is disabled."
                )
            )
        else:
            detail = "PowerContext DSH plugin is not registered in the Web profile; run powercontext setup dsh."
        return {
            "dsh": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
            "plugin_memory_protocol": protocol,
            "plugin": Diagnostic(
                status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
                detail=detail,
                checks={
                    "registration": "present" if installed else "missing",
                    "running_host_configuration": "not_observed",
                    "server_liveness": "not_checked",
                    "server_readiness": "not_checked",
                    "route_compatibility": "not_checked",
                    "session_scope": "not_checked",
                    **capabilities,
                },
            ),
        }
