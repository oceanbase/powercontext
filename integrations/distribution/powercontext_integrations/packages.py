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

"""Shared source, installation, and detection for package and directory targets."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import tomllib
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from powercontext.cli.git_source import InvalidGitHubSourceError, clone_github_source
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.client.transport_policy import client_config_file

from .resources import install_resources
from .targets import Target


@dataclass(frozen=True)
class PackageSetupResult:
    plugin: str
    destination: str
    python: str | None = None


def saved_installation(target: Target) -> dict[str, str]:
    try:
        config = json.loads(client_config_file().read_text())
        value = config.get("hosts", {}).get(target.target, {}).get("installation", {})
        return value if isinstance(value, dict) and all(isinstance(item, str) for item in value.values()) else {}
    except (OSError, ValueError, AttributeError):
        return {}


def application_python(target: Target, python: str | None) -> str:
    selected = python or saved_installation(target).get("python")
    if selected:
        executable = Path(selected).expanduser()
    else:
        environment = Path.cwd() / ".venv"
        executable = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not executable.is_file():
        raise SetupError("Application Python is unavailable; pass --python /path/to/application/.venv/bin/python.")
    return str(executable.absolute())


@contextmanager
def package_source(target: Target, source: str, ref: str):
    local = Path(source).expanduser()
    with tempfile.TemporaryDirectory(prefix="powercontext-setup-") as temporary:
        if local.is_dir():
            root = local.resolve()
        else:
            if not ref or ref.startswith("-") or any(character.isspace() for character in ref):
                raise SetupError.invalid_ref(target.label, ref)
            root = Path(temporary) / "source"
            try:
                clone_github_source(source, ref, root)
            except InvalidGitHubSourceError as error:
                raise SetupError("Use a local directory or a credential-free GitHub repository.") from error
        package = root / target.source if (root / target.source).is_dir() else root
        marker = "pyproject.toml" if target.setup.package else target.hook_manifest or "plugin.json"
        if not (package / marker).is_file():
            raise SetupError.not_found(f"PowerContext {target.label} package", package)
        try:
            text = (package / marker).read_text()
            metadata = tomllib.loads(text).get("project", {}) if target.setup.package else json.loads(text)
        except (OSError, ValueError) as error:
            raise SetupError("Cannot read the source package metadata.") from error
        if not isinstance(metadata, dict) or metadata.get("name") != (target.setup.package or "powercontext"):
            raise SetupError("The source package does not match the selected integration.")
        yield package


def run_package_command(command: list[str], *, detail: str) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=120)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError(detail) from error
    if result.returncode:
        raise SetupError(detail)
    return result.stdout


RECEIPT = ".powercontext-install.json"


def contained_path(directory: Path, name: str) -> Path:
    relative = PurePosixPath(name)
    if not name or name == "." or relative.is_absolute() or ".." in relative.parts or "\\" in name:
        raise SetupError("Plugin resources must use contained relative paths.")
    path = directory / name
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise SetupError("Plugin resources must not traverse symlinks.")
    return path


def owned_files(directory: Path) -> set[str]:
    receipt = contained_path(directory, RECEIPT)
    if not receipt.exists():
        return set()
    try:
        names = json.loads(receipt.read_text())
        if not isinstance(names, list) or not names or not all(isinstance(name, str) for name in names):
            raise ValueError  # noqa: TRY301
        for name in names:
            contained_path(directory, name)
        return {name for name in names if isinstance(name, str)}
    except (OSError, ValueError) as error:
        raise SetupError("The plugin installation receipt is invalid.") from error


def staged_files(target: Target, package: Path, destination: Path, server_url: str | None) -> dict[str, bytes]:
    with tempfile.TemporaryDirectory(prefix="powercontext-plugin-") as temporary:
        staging = Path(temporary)
        for source in package.rglob("*"):
            relative = source.relative_to(package)
            if any(
                part in {"node_modules", "__pycache__", "tests"} or (part.startswith(".") and part != ".minimax-plugin")
                for part in relative.parts
            ):
                continue
            source = contained_path(package, relative.as_posix())
            if source.is_file():
                path = staging / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, path)
        for name in ("mcp.json", "powercontext.mcp.json"):
            existing = contained_path(destination, name)
            if existing.is_file():
                shutil.copyfile(existing, staging / name)
        install_resources(target.target, staging, server_url=server_url)
        return {
            path.relative_to(staging).as_posix(): path.read_bytes() for path in staging.rglob("*") if path.is_file()
        }


def install_directory(target: Target, package: Path, destination: Path, server_url: str | None) -> PackageSetupResult:
    """Replace owned files only; preserve private MCP settings and roll back failed writes."""
    destination = destination.expanduser().absolute()
    try:
        owned = owned_files(destination)
        files = staged_files(target, package, destination, server_url)
        manifest = contained_path(destination, target.hook_manifest or "plugin.json")
        if not owned and manifest.is_file() and json.loads(manifest.read_text()).get("name") == "powercontext":
            owned = {name for name in files if contained_path(destination, name).is_file()}
        paths = {name: contained_path(destination, name) for name in {*files, *owned, RECEIPT}}
        if any(paths[name].exists() and name not in owned for name in files):
            raise SetupError(f"Plugin destination contains files not owned by PowerContext: {destination}")
        snapshots = {name: path.read_bytes() if path.is_file() else None for name, path in paths.items()}
        files[RECEIPT] = (json.dumps(sorted(files)) + "\n").encode()
        try:
            for name, content in files.items():
                paths[name].parent.mkdir(parents=True, exist_ok=True)
                paths[name].write_bytes(content)
            for name in owned - files.keys():
                paths[name].unlink(missing_ok=True)
        except OSError:
            for name, content in snapshots.items():
                if content is None:
                    paths[name].unlink(missing_ok=True)
                else:
                    paths[name].write_bytes(content)
            raise
    except (OSError, ValueError, KeyError, AttributeError, TypeError) as error:
        raise SetupError(f"Cannot install plugin at {destination}") from error
    return PackageSetupResult(target.target, str(destination))


def install_package_plugin(
    target: Target,
    *,
    source: str,
    ref: str,
    python: str | None = None,
    destination: Path | None = None,
    server_url: str | None = None,
) -> PackageSetupResult:
    if target.setup.package:
        executable = application_python(target, python)
        with package_source(target, source, ref) as package:
            run_package_command(
                ["uvx", "--from", "uv", "uv", "pip", "install", "--python", executable, str(package)],
                detail=f"Cannot install {target.setup.package} into {executable}.",
            )
        return PackageSetupResult(target.target, str(Path(executable).parent), executable)
    selected = destination or saved_installation(target).get("destination")
    if not selected:
        raise SetupError("Pass --destination /path/to/plugin and register that directory with the loading agent.")
    with package_source(target, source, ref) as package:
        return install_directory(target, package, Path(selected), server_url)


def run_package_diagnostics(
    target: Target, *, python: str | None = None, destination: Path | None = None
) -> dict[str, Diagnostic]:
    if target.setup.package:
        try:
            executable = application_python(target, python)
        except SetupError:
            return {
                "environment": Diagnostic(
                    DiagnosticStatus.FAILED, "Application Python is not installed or is not on PATH"
                ),
                "package": Diagnostic(DiagnosticStatus.SKIPPED, "Pass --python or create the project .venv."),
            }
        try:
            version = run_package_command(
                [
                    executable,
                    "-I",
                    "-c",
                    "import importlib, importlib.metadata, sys\n"
                    "try: version = importlib.metadata.version(sys.argv[2])\n"
                    "except importlib.metadata.PackageNotFoundError: print('not_installed')\n"
                    "else: importlib.import_module(sys.argv[1]); print(version)",
                    target.setup.package.replace("-", "_"),
                    target.setup.package,
                ],
                detail=f"Cannot import {target.setup.package} in the selected application environment.",
            ).strip()
            if version == "not_installed":
                return {
                    "environment": Diagnostic(
                        DiagnosticStatus.FAILED, f"{target.label} package is not installed or is not on PATH"
                    ),
                    "package": Diagnostic(
                        DiagnosticStatus.SKIPPED, "Install this integration in the application environment."
                    ),
                }
            package = Diagnostic(DiagnosticStatus.OK, f"{target.setup.package} {version}")
        except SetupError as error:
            package = Diagnostic(DiagnosticStatus.FAILED, str(error))
        return {"environment": Diagnostic(DiagnosticStatus.OK, executable), "package": package}
    selected = destination or saved_installation(target).get("destination")
    path = Path(selected) if selected else None
    if path is None or not path.is_dir():
        return {
            "directory": Diagnostic(DiagnosticStatus.FAILED, "Plugin directory is not installed or is not on PATH"),
            "plugin": Diagnostic(DiagnosticStatus.SKIPPED, "Run setup with --destination."),
        }
    try:
        names = owned_files(path)
        manifest = target.hook_manifest or "plugin.json"
        valid = manifest in names and all(contained_path(path, name).is_file() for name in names)
    except (OSError, ValueError, SetupError):
        valid = False
    return {
        "directory": Diagnostic(DiagnosticStatus.OK, str(path)),
        "plugin": Diagnostic(
            DiagnosticStatus.OK if valid else DiagnosticStatus.FAILED,
            "Plugin resources are present."
            if valid
            else "Plugin resources are missing or the installation receipt is invalid.",
        ),
    }
