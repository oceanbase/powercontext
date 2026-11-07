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

"""Install and diagnose the Hermes PowerContext memory provider."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from shutil import which
from typing import Any

import yaml

from powercontext.cli.guidance import HOST_GUIDANCE, SKILL_DIRECTORY, preserve_installed_guidance, refresh_skill
from powercontext.cli.installation import publish
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import setup_transport_updates
from powercontext.paths import powercontext_data_dir

HERMES_HOME_ENV = "HERMES_HOME"
HERMES_PLUGIN_RELATIVE = Path(HOST_GUIDANCE["hermes"].plugin)
HERMES_COMMAND_PLUGIN_RELATIVE = Path("integrations") / "hermes" / "plugins" / "powercontext-command"
HERMES_PLUGIN_NAME = "powercontext"
HERMES_COMMAND_PLUGIN_NAME = "powercontext-command"
HERMES_MIN_VERSION = (0, 20, 4)
_HERMES_VERSION_PATTERN = re.compile(r"Hermes Agent v?(\d+)\.(\d+)\.(\d+)")


def hermes_home() -> Path:
    """Return Hermes' user home, honoring the host's environment override."""

    configured = os.environ.get(HERMES_HOME_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".hermes").resolve()


def hermes_executable() -> str:
    """Return a subprocess-launchable Hermes CLI path."""

    executable = which("hermes")
    if executable is None:
        raise SetupError("Hermes CLI is not installed or is not on PATH.")  # noqa: TRY003
    return executable


def get_hermes_version(executable: str | None = None) -> str:
    """Return the Hermes version and reject versions below the supported minimum."""

    executable = executable or hermes_executable()
    command = [executable, "--version"]
    completed = _run_hermes_command(command)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError(f"`{' '.join(command)}` failed: {detail}")  # noqa: TRY003
    match = _HERMES_VERSION_PATTERN.search(f"{completed.stdout}\n{completed.stderr}")
    if match is None:
        raise SetupError(f"`{' '.join(command)}` returned an unrecognized Hermes version")  # noqa: TRY003
    parsed = tuple(int(part) for part in match.groups())
    if parsed < HERMES_MIN_VERSION:
        actual = ".".join(str(part) for part in parsed)
        minimum = ".".join(str(part) for part in HERMES_MIN_VERSION)
        raise SetupError(  # noqa: TRY003
            f"Hermes Agent v{actual} is unsupported; PowerContext requires Hermes Agent v{minimum} or newer."
        )
    return ".".join(str(part) for part in parsed)


def _run_plugin_doctor(executable: str, plugin: Path) -> None:
    command = [executable, "plugins", "doctor", "--ci", str(plugin)]
    completed = _run_hermes_command(command)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError(f"`{' '.join(command)}` failed: {detail}")  # noqa: TRY003


def _run_hermes_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 - the executable and arguments are controlled by this CLI.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(f"Cannot run {' '.join(command)}: {error}") from error  # noqa: TRY003


__all__ = [
    "HERMES_COMMAND_PLUGIN_NAME",
    "HERMES_COMMAND_PLUGIN_RELATIVE",
    "HERMES_PLUGIN_NAME",
    "get_hermes_version",
    "hermes_executable",
    "hermes_home",
]


def hermes_transport_updates(settings) -> dict[Path, bytes]:
    configured = os.environ.get("POWERCONTEXT_HERMES_CONFIG")
    path = Path(configured) if configured else hermes_home() / "powercontext/config.json"
    config = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if not isinstance(config, dict):
        raise ValueError("Hermes client configuration must be an object")  # noqa: TRY003, TRY004
    config.update(base_url=settings.server_url, allow_insecure_http=settings.allow_insecure_http)
    return {path: (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()}


def _hermes_config() -> dict[str, Any]:
    path = hermes_home() / "config.yaml"
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else {}
        if not isinstance(value, dict):
            raise ValueError  # noqa: TRY004, TRY301 - normalize invalid native configuration.
    except (OSError, ValueError, yaml.YAMLError) as error:
        raise SetupError("Cannot read Hermes native configuration") from error  # noqa: TRY003
    return value


class HermesInstaller:
    def install(self, source):
        executable = hermes_executable()
        get_hermes_version(executable)
        with source.package("hermes") as package:
            command = package.parent / HERMES_COMMAND_PLUGIN_NAME
            if not (command / "plugin.yaml").is_file():
                raise SetupError(f"PowerContext Hermes plugin was not found under {command}.")  # noqa: TRY003
            root = hermes_home() / "plugins"
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=root) as temporary:
                replacements = []
                for path, name in ((package, HERMES_PLUGIN_NAME), (command, HERMES_COMMAND_PLUGIN_NAME)):
                    target, candidate = root / name, Path(temporary) / name
                    shutil.copytree(path, candidate)
                    preserve_installed_guidance(target, candidate)
                    replacements.append((candidate, target))
                skill = hermes_home() / SKILL_DIRECTORY
                staged_skill = Path(temporary) / "skill"
                previous = skill if skill.exists() else root / HERMES_PLUGIN_NAME / SKILL_DIRECTORY
                if previous.exists():
                    shutil.copytree(previous, staged_skill)
                refresh_skill(package / SKILL_DIRECTORY, staged_skill, previous=previous)
                replacements.append((staged_skill, skill))
                publish(replacements)
            return {
                "plugin": HERMES_PLUGIN_NAME,
                "plugin_path": str(root / HERMES_PLUGIN_NAME),
                "hermes_home": str(hermes_home()),
                "data_dir": str(powercontext_data_dir()),
                "command_plugin_path": str(root / HERMES_COMMAND_PLUGIN_NAME),
                "skill_path": str(skill),
            }

    def configure(self, connection):
        if not (hermes_home() / "plugins/powercontext/__init__.py").is_file():
            raise SetupError("PowerContext is not installed in hermes; run powercontext setup hermes")  # noqa: TRY003
        publish(writes={**setup_transport_updates(connection), **hermes_transport_updates(connection)})
        return {}

    def enable(self) -> None:
        config = _hermes_config()
        plugins = config.get("plugins", {})
        memory = config.get("memory", {})
        commands = []
        if HERMES_COMMAND_PLUGIN_NAME not in plugins.get(
            "disabled", []
        ) and HERMES_COMMAND_PLUGIN_NAME not in plugins.get("enabled", []):
            commands.append(["plugins", "enable", HERMES_COMMAND_PLUGIN_NAME, "--no-allow-tool-override"])
        if "provider" not in memory:
            commands.append(["memory", "setup", HERMES_PLUGIN_NAME])
        for arguments in commands:
            command = [hermes_executable(), *arguments]
            completed = _run_hermes_command(command)
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
                raise SetupError(f"`{' '.join(command)}` failed: {detail}")  # noqa: TRY003
        if "provider" not in memory and _hermes_config().get("memory", {}).get("provider") != HERMES_PLUGIN_NAME:
            raise SetupError("Hermes did not select PowerContext; inspect hermes memory status")  # noqa: TRY003

    def verify(self) -> dict[str, Diagnostic]:
        """Collect diagnostics for the optional Hermes integration."""

        try:
            executable = hermes_executable()
        except SetupError:
            return {
                "hermes": Diagnostic(
                    status=DiagnosticStatus.FAILED,
                    detail="Hermes CLI is not installed or is not on PATH",
                ),
                "plugin": Diagnostic(
                    status=DiagnosticStatus.SKIPPED,
                    detail="not checked because Hermes CLI is unavailable",
                ),
            }

        try:
            hermes_version = get_hermes_version(executable)
        except SetupError as error:
            return {
                "hermes": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
                "plugin": Diagnostic(
                    status=DiagnosticStatus.SKIPPED,
                    detail="not checked because Hermes version validation failed",
                ),
            }

        config = _hermes_config()
        provider = config.get("memory", {}).get("provider")
        plugins = config.get("plugins", {})
        plugin = hermes_home() / "plugins" / HERMES_PLUGIN_NAME
        command_plugin = hermes_home() / "plugins" / HERMES_COMMAND_PLUGIN_NAME
        if not HOST_GUIDANCE["hermes"].recognizes(plugin):
            return {
                "hermes": Diagnostic(
                    status=DiagnosticStatus.OK, detail=f"{executable} (Hermes Agent v{hermes_version})"
                ),
                "plugin": Diagnostic(
                    status=DiagnosticStatus.FAILED,
                    detail="PowerContext Hermes plugin is not installed",
                ),
                "command_plugin": Diagnostic(
                    status=DiagnosticStatus.SKIPPED,
                    detail="not checked because the PowerContext memory provider is not installed",
                ),
            }

        try:
            _run_plugin_doctor(executable, plugin)
        except SetupError as error:
            plugin_diagnostic = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
        else:
            plugin_diagnostic = Diagnostic(
                status=DiagnosticStatus.OK,
                detail="powercontext passed Hermes plugin doctor",
            )

        if not HOST_GUIDANCE["hermes"].recognizes(command_plugin):
            command_diagnostic = Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="PowerContext Hermes command companion is not installed",
            )
        else:
            try:
                _run_plugin_doctor(executable, command_plugin)
            except SetupError as error:
                command_diagnostic = Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error))
            else:
                command_diagnostic = Diagnostic(
                    status=DiagnosticStatus.OK,
                    detail="powercontext-command passed Hermes plugin doctor",
                )

        return {
            "hermes": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} (Hermes Agent v{hermes_version})"),
            "plugin": plugin_diagnostic,
            "command_plugin": command_diagnostic,
            "provider": Diagnostic(
                DiagnosticStatus.OK if provider == HERMES_PLUGIN_NAME else DiagnosticStatus.FAILED,
                "PowerContext selected"
                if provider == HERMES_PLUGIN_NAME
                else "PowerContext is not the selected memory provider; use hermes memory setup",
            ),
            "command_enabled": Diagnostic(
                DiagnosticStatus.OK
                if HERMES_COMMAND_PLUGIN_NAME in plugins.get("enabled", [])
                and HERMES_COMMAND_PLUGIN_NAME not in plugins.get("disabled", [])
                else DiagnosticStatus.FAILED,
                "Inspect hermes plugins list for the PowerContext command companion",
            ),
        }
