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

DSH_PLUGIN_NAME = "powercontext-dsh"
DSH_PLUGIN_RELATIVE = Path("integrations") / "dsh" / "plugins" / "powercontext"
DSH_PROFILE = "web"
DSH_BUNDLE = Path("lib") / "index.js"


@dataclass(frozen=True, slots=True)
class DshSetupResult:
    plugin: str
    plugin_path: str
    data_dir: str


def install_dsh_plugin(*, source: str, ref: str) -> DshSetupResult:
    """Install the plugin from a PowerContext checkout or Git source."""

    dsh_executable()
    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error
    plugin_dir = resolve_dsh_plugin_dir(source=source, ref=ref)
    require_built_plugin(plugin_dir)
    _run_dsh("plugin", "--profile", DSH_PROFILE, "add", str(plugin_dir))
    return DshSetupResult(
        plugin=DSH_PLUGIN_NAME,
        plugin_path=str(plugin_dir),
        data_dir=str(data_dir),
    )


def resolve_dsh_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the plugin directory for a local checkout or a materialized Git ref."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    """Accept either the plugin directory or a PowerContext repository root."""

    if _is_dsh_plugin(root):
        return root
    plugin = root / DSH_PLUGIN_RELATIVE
    if _is_dsh_plugin(plugin):
        return plugin
    raise SetupError.missing_dsh_plugin(root)


def require_built_plugin(path: Path) -> None:
    """Reject a plugin directory that cannot be loaded by DeepSeek Harness."""

    if not (path / DSH_BUNDLE).is_file():
        raise SetupError.unbuilt_dsh_plugin(path)


def run_dsh_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional DeepSeek Harness integration."""

    try:
        executable = dsh_executable()
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
    try:
        output = _run_dsh("--profile", DSH_PROFILE, "--dump-config")
    except SetupError as error:
        return {
            "dsh": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="plugin list is unavailable"),
        }
    installed = plugin_id_installed(output)
    return {
        "dsh": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
            detail=(f"{DSH_PLUGIN_NAME} is installed" if installed else "PowerContext DSH plugin is not installed"),
        ),
    }


def dsh_executable() -> str:
    """Return a subprocess-launchable DeepSeek Harness CLI path."""

    if os.name == "nt":
        cmd = which("dsh.cmd")
        if cmd is not None:
            return cmd
    executable = which("dsh")
    if executable is None:
        raise SetupError.dsh_unavailable()
    return executable


def plugin_id_installed(output: str) -> bool:
    """Return True when dump-config lists the plugin id, not just the package name."""

    expected = {
        f"id: {DSH_PLUGIN_NAME}",
        f'id: "{DSH_PLUGIN_NAME}"',
        f"id: '{DSH_PLUGIN_NAME}'",
    }
    return any(raw.strip().lstrip("-").strip() in expected for raw in output.splitlines())


def github_clone_url(source: str) -> str:
    """Accept a GitHub slug or repository URL and return a clone URL."""

    try:
        return _github_clone_url(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_dsh_source() from None


def checkout_target(ref: str) -> Path:
    """Resolve a Git ref to a directory that stays under the DSH checkout root."""

    root = (powercontext_data_dir() / "checkouts" / "dsh").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_dsh_ref(ref)
    target = (root / ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_dsh_ref(ref) from error
    if target == root:
        raise SetupError.invalid_dsh_ref(ref)
    return target


def _is_dsh_plugin(path: Path) -> bool:
    manifest = path / "package.json"
    if not manifest.is_file():
        return False
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload.get("name") == DSH_PLUGIN_NAME


def _usable_checkout(target: Path) -> bool:
    return _is_dsh_plugin(target) or _is_dsh_plugin(target / DSH_PLUGIN_RELATIVE)


def _materialize_remote_checkout(source: str, ref: str) -> Path:
    target = checkout_target(ref)
    if _usable_checkout(target):
        return target
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    _clone_github_source(source, ref, target)
    return target


def _clone_github_source(source: str, ref: str, target: Path) -> None:
    try:
        clone_github_source(source, ref, target)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_dsh_source() from None


def _run_dsh(*arguments: str) -> str:
    command = [dsh_executable(), *arguments]
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
        raise SetupError.command_unavailable(command, error) from error
    if completed.returncode != 0:
        detail = (
            (completed.stderr or "").strip() or (completed.stdout or "").strip() or f"exit code {completed.returncode}"
        )
        raise SetupError.command_failed(command, detail)
    return completed.stdout or ""


__all__ = [
    "DSH_PLUGIN_NAME",
    "DshSetupResult",
    "checkout_target",
    "dsh_executable",
    "github_clone_url",
    "install_dsh_plugin",
    "plugin_dir_from_checkout",
    "plugin_id_installed",
    "require_built_plugin",
    "resolve_dsh_plugin_dir",
    "run_dsh_diagnostics",
]
