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

"""Install and diagnose the Pi PowerContext package."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from shutil import which

from powercontext.cli.guidance import HOST_GUIDANCE, SKILL_FILE, preserve_installed_guidance
from powercontext.cli.installation import SetupError, publish
from powercontext.cli.system import Diagnostic, DiagnosticStatus
from powercontext.cli.transport import configure_connection
from powercontext.paths import powercontext_data_dir

PI_PACKAGE_NAME = "powercontext-pi"
PI_PLUGIN_RELATIVE = Path(HOST_GUIDANCE["pi"].plugin)
PI_EXTENSION = Path("extensions") / "powercontext.ts"
PI_SKILL = SKILL_FILE


def require_pi_package(path: Path) -> None:
    """Reject a package that cannot expose PowerContext to Pi."""

    if not (path / PI_EXTENSION).is_file() or not (path / PI_SKILL).is_file():
        raise SetupError(  # noqa: TRY003
            f"PowerContext Pi package at {path} is missing its extension or powercontext-project-context skill."
        )


def pi_executable() -> str:
    """Return a subprocess-launchable Pi CLI path."""

    if os.name == "nt":
        cmd = which("pi.cmd")
        if cmd is not None:
            return cmd
    executable = which("pi")
    if executable is None:
        raise SetupError("Pi CLI is not installed or is not on PATH.")  # noqa: TRY003
    return executable


def pi_package_installed(output: str) -> bool:
    """Return True when Pi lists an installed package with PowerContext's manifest."""

    return any(
        HOST_GUIDANCE["pi"].recognizes(Path(line.strip()))
        for line in output.splitlines()
        if line.startswith("    ") and line.strip()
    )


def _run_pi(*arguments: str) -> str:
    command = [pi_executable(), *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed pi executable.
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
        raise SetupError(f"`{' '.join(command)}` failed: {detail}")  # noqa: TRY003
    return completed.stdout or ""


__all__ = ["PI_PACKAGE_NAME", "pi_executable", "pi_package_installed", "require_pi_package"]


class PiInstaller:
    def install(self, source):
        pi_executable()
        with source.package("pi") as package:
            require_pi_package(package)
            destination = powercontext_data_dir() / "plugins" / "pi"
            marker = ".powercontext-owned"
            if destination.exists() and not (destination / marker).is_file():
                raise SetupError(f"Pi package path {destination} is not owned by PowerContext")  # noqa: TRY003
            superseded = []
            user_packages = False
            registered_source = None
            for line in _run_pi("list").splitlines():
                if line and not line.startswith(" "):
                    user_packages = line == "User packages:"
                    registered_source = None
                elif user_packages and line.startswith("    "):
                    resolved = Path(line.strip())
                    if (
                        registered_source is not None
                        and resolved.resolve() != destination.resolve()
                        and HOST_GUIDANCE["pi"].recognizes(resolved)
                    ):
                        if registered_source.endswith(" (filtered)"):
                            raise SetupError(  # noqa: TRY003
                                f"Pi package {resolved} has resource filters. Migrate its package entry and filters to {destination} in Pi settings before retrying setup."
                            )
                        superseded.append(registered_source)
                elif user_packages and line.startswith("  "):
                    registered_source = line.strip()
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=destination.parent) as temporary:
                candidate = Path(temporary) / "package"
                shutil.copytree(package, candidate, ignore=shutil.ignore_patterns("node_modules", ".git"))
                preserve_installed_guidance(destination, candidate)
                (candidate / marker).write_text("powercontext-pi\n", encoding="utf-8")
                publish([(candidate, destination)], action=lambda: _run_pi("install", str(destination)))
            for registered_source in superseded:
                _run_pi("remove", registered_source)
            return {
                "package": PI_PACKAGE_NAME,
                "package_path": str(destination),
                "data_dir": str(powercontext_data_dir()),
            }

    def configure(self, connection):
        if not (powercontext_data_dir() / "plugins/pi/.powercontext-owned").is_file():
            raise SetupError("PowerContext is not installed in pi; run powercontext setup pi")  # noqa: TRY003
        return {"authorization_state": configure_connection(connection)}

    def verify(self) -> dict[str, Diagnostic]:
        """Collect diagnostics for the optional Pi integration."""

        try:
            executable = pi_executable()
        except SetupError:
            return {
                "pi": Diagnostic(status=DiagnosticStatus.FAILED, detail="Pi CLI is not installed or is not on PATH"),
                "package": Diagnostic(
                    status=DiagnosticStatus.SKIPPED, detail="not checked because Pi CLI is unavailable"
                ),
            }
        try:
            output = _run_pi("list")
        except SetupError as error:
            return {
                "pi": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
                "package": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="package list is unavailable"),
            }
        installed = pi_package_installed(output)
        from powercontext.cli.memory_protocol import installed_memory_protocol, read_plugin_memory_protocol

        paths = [
            Path(line.strip())
            for line in output.splitlines()
            if line.startswith("    ") and HOST_GUIDANCE["pi"].recognizes(Path(line.strip()))
        ]
        protocol = read_plugin_memory_protocol(paths[0]) if len(paths) == 1 else None
        return {
            "pi": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
            "plugin_memory_protocol": installed_memory_protocol(protocol, "pi"),
            "package": Diagnostic(
                status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
                detail=(f"{PI_PACKAGE_NAME} is installed" if installed else "PowerContext Pi package is not installed"),
            ),
        }
