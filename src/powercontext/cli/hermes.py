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

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from shutil import which

from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.paths import powercontext_data_dir

HERMES_HOME_ENV = "HERMES_HOME"
HERMES_PLUGIN_RELATIVE = Path("integrations") / "hermes" / "plugins" / "powercontext"
HERMES_PLUGIN_NAME = "powercontext"
HERMES_MIN_VERSION = (0, 20, 4)
_HERMES_VERSION_PATTERN = re.compile(r"Hermes Agent v?(\d+)\.(\d+)\.(\d+)")


@dataclass(frozen=True, slots=True)
class HermesSetupResult:
    plugin: str
    plugin_path: str
    hermes_home: str
    data_dir: str


def install_hermes_plugin(*, source: str, ref: str) -> HermesSetupResult:
    """Install the PowerContext provider into Hermes' user plugin directory."""

    executable = hermes_executable()
    get_hermes_version(executable)
    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error

    plugin_dir = resolve_hermes_plugin_dir(source=source, ref=ref)
    home = hermes_home()
    target = home / "plugins" / HERMES_PLUGIN_NAME
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = _new_staging_directory(target)
        try:
            shutil.rmtree(staging)
            shutil.copytree(plugin_dir, staging)
            _run_plugin_doctor(executable, staging)
            _replace_directory(staging, target)
        except BaseException:
            _remove_path(staging)
            raise
    except OSError as error:
        raise SetupError.hermes_plugin_write(target, error) from error

    return HermesSetupResult(
        plugin=HERMES_PLUGIN_NAME,
        plugin_path=str(target),
        hermes_home=str(home),
        data_dir=str(data_dir),
    )


def resolve_hermes_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the Hermes provider directory from a local or remote checkout."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    """Accept either the provider directory or a PowerContext repository root."""

    if _is_hermes_plugin(root):
        return root
    plugin = root / HERMES_PLUGIN_RELATIVE
    if _is_hermes_plugin(plugin):
        return plugin
    raise SetupError.missing_hermes_plugin(root)


def run_hermes_diagnostics() -> dict[str, Diagnostic]:
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

    plugin = hermes_home() / "plugins" / HERMES_PLUGIN_NAME
    if not _is_hermes_plugin(plugin):
        return {
            "hermes": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} (Hermes Agent v{hermes_version})"),
            "plugin": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="PowerContext Hermes plugin is not installed",
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

    return {
        "hermes": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} (Hermes Agent v{hermes_version})"),
        "plugin": plugin_diagnostic,
    }


def hermes_home() -> Path:
    """Return Hermes' user home, honoring the host's environment override."""

    configured = os.environ.get(HERMES_HOME_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".hermes").resolve()


def hermes_executable() -> str:
    """Return a subprocess-launchable Hermes CLI path."""

    executable = which("hermes")
    if executable is None:
        raise SetupError.hermes_unavailable()
    return executable


def checkout_target(source: str, ref: str) -> Path:
    """Resolve a source and Git ref to a directory under the Hermes cache."""

    root = (powercontext_data_dir() / "checkouts" / "hermes").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_hermes_ref(ref)
    source_key = _source_cache_key(source)
    target = (root / source_key / ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_hermes_ref(ref) from error
    if target == root:
        raise SetupError.invalid_hermes_ref(ref)
    return target


def github_clone_url(source: str) -> str:
    """Accept a GitHub slug or repository URL and return a clone URL."""

    text = source.strip()
    if text.startswith(("https://github.com/", "http://github.com/", "git@github.com:")):
        return text if text.endswith(".git") else f"{text}.git"
    if "://" in text or text.startswith("git@"):
        raise SetupError.invalid_hermes_source(source)
    if "/" in text and not text.startswith("."):
        return f"https://github.com/{text}.git"
    raise SetupError.invalid_hermes_source(source)


def _is_local_source(source: str) -> bool:
    candidate = Path(source).expanduser()
    return source.startswith((".", "/", "~")) or (len(source) >= 2 and source[1] == ":") or candidate.exists()


def _is_hermes_plugin(path: Path) -> bool:
    return (path / "__init__.py").is_file() and (path / "plugin.yaml").is_file()


def _usable_checkout(target: Path) -> bool:
    return _is_hermes_plugin(target) or _is_hermes_plugin(target / HERMES_PLUGIN_RELATIVE)


def _materialize_remote_checkout(source: str, ref: str) -> Path:
    target = checkout_target(source, ref)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = _new_staging_directory(target)
    shutil.rmtree(staging)
    try:
        _clone_github_source(source, ref, staging)
        _replace_directory(staging, target)
    except BaseException:
        _remove_path(staging)
        raise
    return target


def _clone_github_source(source: str, ref: str, target: Path) -> None:
    command = ["git", "clone", "--depth", "1", "--branch", ref, github_clone_url(source), str(target)]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to git.
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)


def get_hermes_version(executable: str | None = None) -> str:
    """Return the Hermes version and reject versions below the supported minimum."""

    executable = executable or hermes_executable()
    command = [executable, "--version"]
    completed = _run_hermes_command(command)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)
    match = _HERMES_VERSION_PATTERN.search(f"{completed.stdout}\n{completed.stderr}")
    if match is None:
        raise SetupError.invalid_command_output(command, "an unrecognized Hermes version")
    parsed = tuple(int(part) for part in match.groups())
    if parsed < HERMES_MIN_VERSION:
        actual = ".".join(str(part) for part in parsed)
        minimum = ".".join(str(part) for part in HERMES_MIN_VERSION)
        raise SetupError.unsupported_hermes_version(actual, minimum)
    return ".".join(str(part) for part in parsed)


def _run_plugin_doctor(executable: str, plugin: Path) -> None:
    command = [executable, "plugins", "doctor", "--ci", str(plugin)]
    completed = _run_hermes_command(command)
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)


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
        raise SetupError.command_unavailable(command, error) from error


def _source_cache_key(source: str) -> str:
    normalized = github_clone_url(source)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def _new_staging_directory(target: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=f".{target.name}-", dir=target.parent))


def _replace_directory(staging: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.backup-{uuid.uuid4().hex}")
    moved_old = False
    try:
        if target.exists() or target.is_symlink():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
    except BaseException:
        if moved_old and not (target.exists() or target.is_symlink()) and backup.exists():
            os.replace(backup, target)
        raise
    finally:
        _remove_path(staging)
        _remove_path(backup)


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


__all__ = [
    "HERMES_PLUGIN_NAME",
    "HermesSetupResult",
    "checkout_target",
    "get_hermes_version",
    "github_clone_url",
    "hermes_executable",
    "hermes_home",
    "install_hermes_plugin",
    "plugin_dir_from_checkout",
    "resolve_hermes_plugin_dir",
    "run_hermes_diagnostics",
]
