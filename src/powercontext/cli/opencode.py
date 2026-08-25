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

"""Install and diagnose the native OpenCode PowerContext plugin."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from urllib.parse import unquote, urlparse
from uuid import uuid4

from powercontext.cli.git_source import InvalidGitHubSourceError, clone_github_source, github_clone_url
from powercontext.cli.git_source import is_local_source as _is_local_source
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.paths import powercontext_data_dir

OPENCODE_PLUGIN_NAME = "powercontext-opencode"
OPENCODE_PLUGIN_RELATIVE = Path("integrations") / "opencode" / "plugins" / "powercontext"
OPENCODE_BUNDLE = Path("lib") / "index.js"
OPENCODE_SKILL = Path("skills") / "project-context" / "SKILL.md"
SKILL_MANIFEST = ".powercontext.json"
MINIMUM_VERSION = (1, 18, 21)
_VERSION = re.compile(r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)")
_COMMIT = re.compile(r"^[0-9a-f]{40,64}$")
_ACTIVATION_PROBE_PATH = "POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_PATH"
_ACTIVATION_PROBE_NONCE = "POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_NONCE"


@dataclass(frozen=True, slots=True)
class OpenCodeSetupResult:
    plugin: str
    plugin_path: str
    skill_path: str
    data_dir: str


def opencode_executable() -> str:
    """Return a subprocess-launchable OpenCode CLI path."""

    executable = which("opencode")
    if executable is None:
        raise SetupError.opencode_unavailable()
    return executable


def _version() -> str:
    value = _run_opencode("--version").strip()
    match = _VERSION.match(value)
    if match is None:
        raise SetupError.invalid_command_output([opencode_executable(), "--version"], "an invalid version")
    current = tuple(int(match.group(name)) for name in ("major", "minor", "patch"))
    if current[0] != 1 or current < MINIMUM_VERSION:
        raise SetupError.unsupported_opencode_version(value)
    return value


def install_opencode_plugin(*, source: str, ref: str) -> OpenCodeSetupResult:
    """Install the plugin and its owned global Skill from one checkout."""

    opencode_executable()
    _version()
    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error
    plugin_dir = resolve_opencode_plugin_dir(source=source, ref=ref)
    require_complete_plugin(plugin_dir)
    config_dir = opencode_config_dir()
    skill_target = config_dir / "skills" / "project-context"
    require_replaceable_skill(skill_target)
    _run_opencode("plugin", str(plugin_dir), "--global", "--force")
    _install_skill(plugin_dir / OPENCODE_SKILL.parent, skill_target)
    return OpenCodeSetupResult(
        plugin=OPENCODE_PLUGIN_NAME,
        plugin_path=str(plugin_dir),
        skill_path=str(skill_target),
        data_dir=str(data_dir),
    )


def resolve_opencode_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the OpenCode package directory for a local checkout or Git ref."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    if _is_opencode_plugin(root):
        return root
    plugin = root / OPENCODE_PLUGIN_RELATIVE
    if _is_opencode_plugin(plugin):
        return plugin
    raise SetupError.missing_opencode_plugin(root)


def require_complete_plugin(path: Path) -> None:
    if not (path / OPENCODE_BUNDLE).is_file() or not (path / OPENCODE_SKILL).is_file():
        raise SetupError.incomplete_opencode_plugin(path)


def checkout_target(source: str, ref: str, resolved_commit: str) -> Path:
    root = (powercontext_data_dir() / "checkouts" / "opencode").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_opencode_ref(ref)
    try:
        normalized_source = _normalized_source_identity(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_opencode_source() from None
    if _COMMIT.fullmatch(resolved_commit) is None:
        raise SetupError.git_clone_failed()
    source_key = hashlib.sha256(normalized_source.encode()).hexdigest()[:16]
    ref_key = hashlib.sha256(ref.encode()).hexdigest()[:16]
    target = (root / source_key / ref_key / resolved_commit).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_opencode_ref(ref) from error
    if target == root:
        raise SetupError.invalid_opencode_ref(ref)
    return target


def opencode_config_dir() -> Path:
    output = _run_opencode("debug", "paths")
    for line in output.splitlines():
        key, separator, value = line.strip().partition(" ")
        if key == "config" and separator and value.strip():
            return Path(value.strip()).expanduser().resolve()
    raise SetupError.invalid_command_output([opencode_executable(), "debug", "paths"], "no config path")


def require_replaceable_skill(target: Path) -> None:
    if target.exists() and not _owned_skill(target):
        raise SetupError.opencode_skill_conflict(target)


def _install_skill(source: Path, target: Path) -> None:
    require_replaceable_skill(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent / f".{target.name}.{uuid4().hex}.tmp"
    backup: Path | None = None
    try:
        shutil.copytree(source, staging)
        (staging / SKILL_MANIFEST).write_text(
            json.dumps({"schema": 1, "owner": "powercontext", "integration": "opencode"}, indent=2) + "\n",
            encoding="utf-8",
        )
        if target.exists():
            backup = target.parent / f".{target.name}.{uuid4().hex}.bak"
            os.replace(target, backup)
        try:
            os.replace(staging, target)
        except OSError:
            if backup is not None:
                os.replace(backup, target)
                backup = None
            raise
        if backup is not None:
            with suppress(OSError):
                shutil.rmtree(backup)
    except OSError as error:
        if staging.exists():
            shutil.rmtree(staging)
        if backup is not None and backup.exists() and not target.exists():
            with suppress(OSError):
                os.replace(backup, target)
        raise SetupError.command_unavailable(["install", "OpenCode", "Skill"], error) from error


def _owned_skill(path: Path) -> bool:
    try:
        payload = json.loads((path / SKILL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload == {"schema": 1, "owner": "powercontext", "integration": "opencode"}


def _is_opencode_plugin(path: Path) -> bool:
    try:
        payload = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload.get("name") == OPENCODE_PLUGIN_NAME


def _materialize_remote_checkout(source: str, ref: str) -> Path:
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_opencode_ref(ref)
    try:
        normalized_source = _normalized_source_identity(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_opencode_source() from None
    root = (powercontext_data_dir() / "checkouts" / "opencode").resolve()
    source_key = hashlib.sha256(normalized_source.encode()).hexdigest()[:16]
    ref_key = hashlib.sha256(ref.encode()).hexdigest()[:16]
    staging_parent = root / source_key / ref_key
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".checkout-", dir=staging_parent))
    try:
        clone_github_source(source, ref, staging)
        resolved_commit = _checkout_commit(staging)
        target = checkout_target(source, ref, resolved_commit)
        if _is_opencode_plugin(target) or _is_opencode_plugin(target / OPENCODE_PLUGIN_RELATIVE):
            return target
        _replace_checkout(staging, target)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return target


def _normalized_source_identity(source: str) -> str:
    clone_url = github_clone_url(source)
    if clone_url.startswith("git@github.com:"):
        repository = clone_url.removeprefix("git@github.com:")
    else:
        repository = urlparse(clone_url).path.lstrip("/")
    return f"github.com/{repository.removesuffix('.git').casefold()}"


def _checkout_commit(path: Path) -> str:
    command = ["git", "-C", str(path), "rev-parse", "--verify", "HEAD"]
    try:
        completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=10)  # noqa: S603
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.git_clone_failed() from error
    commit = completed.stdout.strip().lower()
    if completed.returncode != 0 or _COMMIT.fullmatch(commit) is None:
        raise SetupError.git_clone_failed()
    return commit


def _replace_checkout(staging: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(f".{target.name}.{uuid4().hex}.bak")
    moved_old = False
    try:
        if target.exists():
            os.replace(target, backup)
            moved_old = True
        os.replace(staging, target)
    except BaseException:
        if moved_old and not target.exists() and backup.exists():
            os.replace(backup, target)
        raise
    finally:
        if backup.exists() and target.exists():
            shutil.rmtree(backup)


def _configured_plugin(output: str) -> bool:
    try:
        payload = json.loads(output)
    except ValueError:
        return False
    plugins = payload.get("plugin") if isinstance(payload, dict) else None
    if not isinstance(plugins, list):
        return False
    for entry in plugins:
        spec = entry[0] if isinstance(entry, list) and entry else entry
        if not isinstance(spec, str):
            continue
        parsed = urlparse(spec)
        raw = unquote(parsed.path) if parsed.scheme == "file" else spec
        path = Path(raw)
        if _is_opencode_plugin(path) or _is_opencode_plugin(path.parent):
            return True
    return False


def run_opencode_diagnostics() -> dict[str, Diagnostic]:
    """Collect model-free diagnostics for the optional OpenCode integration."""

    try:
        executable = opencode_executable()
        actual = _version()
    except SetupError as error:
        return {
            "opencode": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because OpenCode is unavailable"),
            "skill": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because OpenCode is unavailable"),
        }
    try:
        config_dir = opencode_config_dir()
        output, activated = _probe_plugin_activation()
        configured = _configured_plugin(output)
    except SetupError as error:
        return {
            "opencode": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} ({actual})"),
            "plugin": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "skill": Diagnostic(status=DiagnosticStatus.SKIPPED, detail="not checked because config is unavailable"),
        }
    skill = config_dir / "skills" / "project-context"
    skill_ok = _owned_skill(skill) and (skill / "SKILL.md").is_file()
    return {
        "opencode": Diagnostic(status=DiagnosticStatus.OK, detail=f"{executable} ({actual})"),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if configured and activated else DiagnosticStatus.FAILED,
            detail=(
                f"{OPENCODE_PLUGIN_NAME} is configured and active"
                if configured and activated
                else (
                    "PowerContext OpenCode plugin is configured but did not activate"
                    if configured
                    else "PowerContext OpenCode plugin is not configured"
                )
            ),
        ),
        "skill": Diagnostic(
            status=DiagnosticStatus.OK if skill_ok else DiagnosticStatus.FAILED,
            detail=(str(skill) if skill_ok else "PowerContext OpenCode Skill is not installed"),
        ),
    }


def _probe_plugin_activation() -> tuple[str, bool]:
    token = uuid4().hex
    with tempfile.TemporaryDirectory(prefix="powercontext-opencode-probe-") as directory:
        path = Path(directory) / "active"
        output = _run_opencode(
            "debug",
            "config",
            env={_ACTIVATION_PROBE_PATH: str(path), _ACTIVATION_PROBE_NONCE: token},
        )
        try:
            activated = path.read_text(encoding="utf-8") == token
        except OSError:
            activated = False
    return output, activated


def _run_opencode(*arguments: str, env: dict[str, str] | None = None) -> str:
    command = [opencode_executable(), *arguments]
    try:
        completed = subprocess.run(  # noqa: S603 - arguments are passed directly to the fixed OpenCode executable.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env=None if env is None else os.environ | env,
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
    "OPENCODE_PLUGIN_NAME",
    "OpenCodeSetupResult",
    "checkout_target",
    "install_opencode_plugin",
    "opencode_config_dir",
    "opencode_executable",
    "plugin_dir_from_checkout",
    "require_complete_plugin",
    "resolve_opencode_plugin_dir",
    "run_opencode_diagnostics",
]
