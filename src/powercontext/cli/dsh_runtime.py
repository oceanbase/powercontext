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

"""Select a DSH profile and its installation-owned command/runtime."""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class DshProfile(StrEnum):
    WEB = "web"
    DESKTOP = "desktop"


def dsh_home() -> Path:
    """Use the same home for profile configuration and URL-bound credentials."""
    configured = os.environ.get("DSH_HOME", "")
    return Path(configured if configured.strip() else Path.home() / ".dsh").expanduser()


@dataclass(frozen=True)
class DesktopRuntime:
    """The signed application supplies both Electron and the ASAR package tree."""

    electron: Path
    anchor: Path


def desktop_runtime(command: str) -> DesktopRuntime | None:
    """Recognize installed Windows/macOS launchers, including command symlinks.

    Do not execute launcher contents or substitute an npm runtime for Desktop.
    Python cannot stat files inside ASAR; the Electron inspection helper validates
    the manifest before importing the installed configuration APIs.
    """
    path = Path(command).expanduser().resolve()
    if not path.is_file() or path.name not in {"dsh", "dsh.cmd"}:
        return None
    if tuple(part.lower() for part in path.parts[-5:-1]) != ("resources", "runtime", "cli", "bin"):
        return None
    resources = path.parents[3]
    electron = (
        resources.parent / "DeepSeek Harness.exe"
        if path.suffix == ".cmd"
        else resources.parent / "MacOS" / "DeepSeek Harness"
    )
    archive = resources / "app.asar"
    if not electron.is_file() or not archive.is_file():
        return None
    return DesktopRuntime(electron, archive / "dsh/node_modules/@deepseek-ai/dsh/package.json")


@dataclass(frozen=True)
class DshTarget:
    profile: DshProfile
    command: str | None = None

    @property
    def directory(self) -> Path:
        return dsh_home() / "profiles" / self.profile


def resolve_dsh_target(profile: DshProfile, command: Path | None = None) -> DshTarget:
    """Resolve an explicit command or find the selected carrier on PATH."""
    from powercontext.cli.system import SetupError

    if command is not None:
        selected = str(command.expanduser().resolve())
        if not Path(selected).is_file():
            raise SetupError(  # noqa: TRY003 - actionable host-specific setup failures.
                f"DSH command does not exist: {selected}"
            )
    elif profile == DshProfile.WEB:
        # Preserve Web's transport preflight before requiring a local CLI.
        selected = None
    else:
        filename = "dsh.cmd" if os.name == "nt" else "dsh"
        candidates = [str(Path(directory) / filename) for directory in os.get_exec_path() if directory]
        selected = next((candidate for candidate in candidates if desktop_runtime(candidate)), "")
    if profile == DshProfile.DESKTOP and not desktop_runtime(selected or ""):
        raise SetupError(  # noqa: TRY003 - actionable host-specific setup failures.
            "Desktop setup requires the Desktop-installed dsh command; npm/pnpm dsh cannot manage its profile. "
            "Use Desktop's Manage dsh Command menu, open a new terminal, or pass "
            "--dsh-command /path/to/Desktop/resources/runtime/cli/bin/dsh (dsh.cmd on Windows)."
        )
    target = DshTarget(profile, selected)
    if profile == DshProfile.DESKTOP and not (target.directory / "package.json").is_file():
        raise SetupError(  # noqa: TRY003 - actionable host-specific setup failures.
            f"Desktop profile is not initialized: {target.directory}. "
            "Open Desktop once, then fully quit it before rerunning setup."
        )
    return target
