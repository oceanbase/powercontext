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

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from powercontext.cli.git_source import (
    InvalidGitHubSourceError,
    clone_github_source,
)
from powercontext.cli.git_source import (
    github_clone_url as _github_clone_url,
)
from powercontext.cli.git_source import (
    is_local_source as _is_local_source,
)
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.paths import powercontext_data_dir

PI_PACKAGE_NAME = "powercontext-pi"
PI_PLUGIN_RELATIVE = Path("integrations") / "pi" / "plugins" / "powercontext"
PI_EXTENSION = Path("extensions") / "powercontext.ts"
PI_SKILL = Path("skills") / "project-context" / "SKILL.md"


@dataclass(frozen=True, slots=True)
class PiSetupResult:
    package: str
    package_path: str
    data_dir: str


@dataclass(frozen=True, slots=True)
class _PiPackageListing:
    source: str
    installed_path: Path | None
    scope: str


def install_pi_plugin(*, source: str, ref: str) -> PiSetupResult:
    """Install the native Pi package from a checkout or Git source."""

    pi_executable()
    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error
    package_dir = resolve_pi_plugin_dir(source=source, ref=ref)
    require_pi_package(package_dir)
    _run_pi("install", str(package_dir))
    _remove_existing_pi_packages(keep=package_dir)
    return PiSetupResult(
        package=PI_PACKAGE_NAME,
        package_path=str(package_dir),
        data_dir=str(data_dir),
    )


def resolve_pi_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the Pi package directory for a local checkout or Git ref."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    """Accept either the Pi package directory or a PowerContext repository root."""

    if _is_pi_package(root):
        return root
    package = root / PI_PLUGIN_RELATIVE
    if _is_pi_package(package):
        return package
    raise SetupError.missing_pi_package(root)


def require_pi_package(path: Path) -> None:
    """Reject a package that cannot expose PowerContext to Pi."""

    if not (path / PI_EXTENSION).is_file() or not (path / PI_SKILL).is_file():
        raise SetupError.incomplete_pi_package(path)


def run_pi_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional Pi integration."""

    try:
        executable = pi_executable()
    except SetupError:
        return {
            "pi": Diagnostic(status=DiagnosticStatus.FAILED, detail="Pi CLI is not installed or is not on PATH"),
            "package": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because Pi CLI is unavailable"),
        }
    try:
        output = _run_pi("list")
    except SetupError as error:
        return {
            "pi": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "package": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="package list is unavailable"),
        }
    installed = pi_package_installed(output)
    return {
        "pi": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "package": Diagnostic(
            status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
            detail=(f"{PI_PACKAGE_NAME} is installed" if installed else "PowerContext Pi package is not installed"),
        ),
    }


def pi_executable() -> str:
    """Return a subprocess-launchable Pi CLI path."""

    if os.name == "nt":
        cmd = which("pi.cmd")
        if cmd is not None:
            return cmd
    executable = which("pi")
    if executable is None:
        raise SetupError.pi_unavailable()
    return executable


def pi_package_installed(output: str) -> bool:
    """Return True when Pi lists an installed package with PowerContext's manifest."""

    return any(
        listing.installed_path is not None and _is_pi_package(listing.installed_path)
        for listing in _listed_pi_packages(output)
    )


def github_clone_url(source: str) -> str:
    """Accept a GitHub slug or repository URL and return a clone URL."""

    try:
        return _github_clone_url(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_pi_source() from None


def checkout_target(ref: str) -> Path:
    """Validate a Git ref and return Pi's stable remote checkout directory."""

    root = (powercontext_data_dir() / "checkouts" / "pi").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_pi_ref(ref)
    candidate = (root / ref).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_pi_ref(ref) from error
    if candidate == root:
        raise SetupError.invalid_pi_ref(ref)
    return root / "current"


def _is_pi_package(path: Path) -> bool:
    manifest = path / "package.json"
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload.get("name") == PI_PACKAGE_NAME


def _materialize_remote_checkout(source: str, ref: str) -> Path:
    target = checkout_target(ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))
    backup: Path | None = None
    try:
        _clone_github_source(source, ref, staging)
        if target.exists():
            backup = Path(tempfile.mkdtemp(prefix=f".{target.name}-previous-", dir=target.parent))
            backup.rmdir()
            target.replace(backup)
        staging.replace(target)
    except BaseException:
        if backup is not None and backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        if backup is not None and backup.exists() and target.exists():
            shutil.rmtree(backup, ignore_errors=True)
    return target


def _listed_pi_packages(output: str) -> list[_PiPackageListing]:
    """Parse the source/path pairs emitted by ``pi list``."""

    listings: list[_PiPackageListing] = []
    source: str | None = None
    installed_path: Path | None = None
    scope = "user"

    def append_listing() -> None:
        if source is not None:
            listings.append(_PiPackageListing(source=source, installed_path=installed_path, scope=scope))

    for raw_line in output.splitlines():
        indentation = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()
        if line == "User packages:":
            append_listing()
            source = None
            installed_path = None
            scope = "user"
        elif line == "Project packages:":
            append_listing()
            source = None
            installed_path = None
            scope = "project"
        elif indentation == 2 and line:
            append_listing()
            source = line.removesuffix(" (filtered)")
            installed_path = None
        elif indentation >= 4 and source is not None and line:
            installed_path = Path(line)
    append_listing()
    return listings


def _is_powercontext_pi_listing(listing: _PiPackageListing) -> bool:
    if listing.installed_path is not None and _is_pi_package(listing.installed_path):
        return True
    source = listing.source.replace("\\", "/")
    return source == f"npm:{PI_PACKAGE_NAME}" or PI_PLUGIN_RELATIVE.as_posix() in source


def _remove_existing_pi_packages(*, keep: Path) -> None:
    """Remove superseded user-scoped PowerContext package paths after a successful install."""

    for listing in _listed_pi_packages(_run_pi("list")):
        if listing.scope != "user" or not _is_powercontext_pi_listing(listing):
            continue
        if _is_current_pi_package_listing(listing, keep):
            continue
        if listing.source.startswith(("npm:", "git:", "github:", "http:", "https:", "ssh:")):
            _run_pi("remove", listing.source)
        elif listing.installed_path is not None:
            _run_pi("remove", str(listing.installed_path))
        elif Path(listing.source).is_absolute():
            _run_pi("remove", listing.source)


def _is_current_pi_package_listing(listing: _PiPackageListing, keep: Path) -> bool:
    """Return whether a Pi listing already points at the package just installed."""

    current = keep.resolve()
    if listing.installed_path is not None and listing.installed_path.resolve() == current:
        return True
    return Path(listing.source).expanduser().resolve() == current


def _clone_github_source(source: str, ref: str, target: Path) -> None:
    try:
        clone_github_source(source, ref, target)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_pi_source() from None


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
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = (
            (completed.stderr or "").strip() or (completed.stdout or "").strip() or f"exit code {completed.returncode}"
        )
        raise SetupError.command_failed(command, detail)
    return completed.stdout or ""


__all__ = [
    "PI_PACKAGE_NAME",
    "PiSetupResult",
    "checkout_target",
    "install_pi_plugin",
    "pi_executable",
    "pi_package_installed",
    "plugin_dir_from_checkout",
    "require_pi_package",
    "resolve_pi_plugin_dir",
    "run_pi_diagnostics",
]
