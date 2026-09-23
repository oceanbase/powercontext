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

"""MiniMax native discovery plugged into the shared directory installer."""

from __future__ import annotations

import json
import os
from pathlib import Path
from shutil import which

from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError

from .packages import (
    install_directory,
    package_source,
    run_package_command,
    run_package_diagnostics,
)
from .targets import load_targets


def plugin_directory(destination: Path | None = None) -> Path:
    return (
        Path(destination)
        if destination
        else Path(
            os.environ.get("MINIMAX_DATA_DIR") or os.environ.get("MAVIS_DATA_DIR") or str(Path.home() / ".minimax")
        )
        / "plugins/powercontext"
    )


def install_minimax_plugin(*, source: str, ref: str, server_url: str | None = None, destination: Path | None = None):
    if which("mcode") is None:
        raise SetupError.unavailable("MiniMax CLI")
    target = next(target for target in load_targets() if target.target == "minimax")
    with package_source(target, source, ref) as package:
        return install_directory(target, package, plugin_directory(destination), server_url)


def run_minimax_diagnostics(*, destination: Path | None = None) -> dict[str, Diagnostic]:
    executable = which("mcode")
    if executable is None:
        return {
            "minimax": Diagnostic(DiagnosticStatus.FAILED, "MiniMax CLI is not installed or is not on PATH"),
            "plugin": Diagnostic(DiagnosticStatus.SKIPPED, "MiniMax CLI is unavailable."),
        }
    target = next(target for target in load_targets() if target.target == "minimax")
    checks = run_package_diagnostics(target, destination=plugin_directory(destination))
    checks.pop("directory")
    checks["minimax"] = Diagnostic(DiagnosticStatus.OK, executable)
    try:
        listing = json.loads(
            run_package_command(
                [executable, "plugin", "list", "--marketplace", "local", "--json"],
                detail="MiniMax plugin discovery failed.",
            )
        )
        entries = listing if isinstance(listing, list) else listing.get("installed", [])
        loaded = any(entry.get("name") == "powercontext" and entry.get("enabled", True) for entry in entries)
        if not loaded:
            checks["plugin"] = Diagnostic(DiagnosticStatus.FAILED, "MiniMax did not load the PowerContext plugin.")
    except (SetupError, ValueError, AttributeError):
        checks["plugin"] = Diagnostic(DiagnosticStatus.FAILED, "MiniMax plugin discovery failed.")
    return checks


def resolve_minimax_connection():
    from powercontext.client.integration.config import mcp_connection

    directory = plugin_directory()
    try:
        connection = mcp_connection(
            directory / "powercontext.mcp.json", directory.parent.parent / "mcp.json", host="minimax"
        )
        if connection is not None:
            return connection
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise ValueError("MiniMax PowerContext MCP configuration is missing, disabled, or invalid.")
