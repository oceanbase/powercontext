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

"""Install and diagnose the PowerContext WorkBuddy integration."""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import uuid
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

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

WORKBUDDY_HOME_ENV = "WORKBUDDY_HOME"
WORKBUDDY_PLUGIN_NAME = "powercontext"
WORKBUDDY_PLUGIN_RELATIVE = Path("integrations") / "workbuddy" / "plugins" / "powercontext"
WORKBUDDY_HOOKS_DIRNAME = "hooks"
WORKBUDDY_SKILLS_DIRNAME = "skills"
WORKBUDDY_SKILL_NAME = "project-context"
WORKBUDDY_SKILL_MANIFEST = ".powercontext.json"
WORKBUDDY_PYTHON_PLACEHOLDER = "${POWERCONTEXT_PYTHON}"
WORKBUDDY_SCOPE_BINDING_PLACEHOLDER = "${POWERCONTEXT_SCOPE_BINDING_SCRIPT}"
WORKBUDDY_HOOK_DRIVER = "workbuddy_powercontext_hook.py"
WORKBUDDY_SCOPE_RESOLVER = "powercontext_scope_binding.py"
WORKBUDDY_HOOK_MODULES = (
    "workbuddy_powercontext_hook.py",
    "workbuddy_settings.py",
    "prepared_context.py",
)
WORKBUDDY_SCRIPT_MODULES = ("__init__.py", "workspace_scope.py")
WORKBUDDY_SERVER_URL_ENV = "POWERCONTEXT_WORKBUDDY_SERVER_URL"
WORKBUDDY_AUTHORIZATION_ENV = "POWERCONTEXT_WORKBUDDY_AUTHORIZATION"
WORKBUDDY_MCP_URL = f"${{{WORKBUDDY_SERVER_URL_ENV}:-http://127.0.0.1:8000}}/mcp"
WORKBUDDY_MCP_AUTHORIZATION = f"${{{WORKBUDDY_AUTHORIZATION_ENV}:-}}"
WORKBUDDY_LEGACY_MCP_URL = "http://127.0.0.1:8000/mcp"
WORKBUDDY_MCP_DESCRIPTION = "PowerContext agent memory & handoff MCP server (local service on port 8000)"
WORKBUDDY_HOOK_STATUS_MESSAGE = "Syncing PowerContext"
WORKBUDDY_HOOK_TIMEOUT = 10


@dataclass(frozen=True, slots=True)
class WorkBuddySetupResult:
    plugin: str
    plugin_path: str
    workbuddy_home: str
    hooks_dir: str
    data_dir: str


def install_workbuddy_plugin(*, source: str, ref: str) -> WorkBuddySetupResult:
    """Install the PowerContext hooks, MCP server, and Skill into WorkBuddy's user directory."""

    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error

    plugin_dir = resolve_workbuddy_plugin_dir(source=source, ref=ref)
    require_workbuddy_plugin(plugin_dir)

    home = workbuddy_home()
    hooks_dir = home / WORKBUDDY_HOOKS_DIRNAME
    skills_dir = home / WORKBUDDY_SKILLS_DIRNAME
    skill_dir = skills_dir / WORKBUDDY_SKILL_NAME
    settings_file = home / "settings.json"
    mcp_file = home / "mcp.json"

    try:
        home.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.workbuddy_home_unavailable(home, error) from error

    require_replaceable_workbuddy_skill(skill_dir)
    settings_snapshot = _read_bytes_or_none(
        settings_file,
        io_error=lambda error: SetupError.workbuddy_settings_write(settings_file, error),
    )
    mcp_snapshot = _read_bytes_or_none(
        mcp_file,
        io_error=lambda error: SetupError.workbuddy_mcp_write(mcp_file, error),
    )
    hooks_backup = _snapshot_directory(hooks_dir)
    skill_backup = _snapshot_directory(skill_dir)
    try:
        _install_hook_files(plugin_dir, hooks_dir)
        _merge_workbuddy_settings(settings_file, hooks_dir)
        _merge_workbuddy_mcp(mcp_file)
        _install_workbuddy_skill(plugin_dir, skills_dir, hooks_dir)
    except BaseException:
        _restore_file(settings_file, settings_snapshot)
        _restore_file(mcp_file, mcp_snapshot)
        _restore_directory(hooks_dir, hooks_backup)
        _restore_directory(skill_dir, skill_backup)
        raise
    finally:
        _remove_path(hooks_backup)
        _remove_path(skill_backup)

    return WorkBuddySetupResult(
        plugin=WORKBUDDY_PLUGIN_NAME,
        plugin_path=str(plugin_dir),
        workbuddy_home=str(home),
        hooks_dir=str(hooks_dir),
        data_dir=str(data_dir),
    )


def resolve_workbuddy_plugin_dir(*, source: str, ref: str) -> Path:
    """Return the WorkBuddy plugin directory from a local or remote checkout."""

    if _is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(_materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    """Accept either the plugin directory or a PowerContext repository root."""

    if _is_workbuddy_plugin(root):
        return root
    plugin = root / WORKBUDDY_PLUGIN_RELATIVE
    if _is_workbuddy_plugin(plugin):
        return plugin
    raise SetupError.missing_workbuddy_plugin(root)


def require_workbuddy_plugin(path: Path) -> None:
    """Reject a directory that cannot provide the WorkBuddy hooks, MCP, and Skill."""

    if not _is_workbuddy_plugin(path):
        raise SetupError.missing_workbuddy_plugin(path)


def run_workbuddy_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional WorkBuddy integration."""

    home = workbuddy_home()
    hooks_dir = home / WORKBUDDY_HOOKS_DIRNAME
    skill_file = home / WORKBUDDY_SKILLS_DIRNAME / WORKBUDDY_SKILL_NAME / "SKILL.md"
    return {
        "hooks": _hooks_diagnostic(hooks_dir),
        "settings": _settings_diagnostic(home / "settings.json"),
        "mcp": _mcp_diagnostic(home / "mcp.json"),
        "skill": _skill_diagnostic(skill_file),
    }


def workbuddy_home() -> Path:
    """Return WorkBuddy's user home, honoring the host's environment override."""

    configured = os.environ.get(WORKBUDDY_HOME_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".workbuddy").resolve()


def github_clone_url(source: str) -> str:
    """Accept a GitHub slug or repository URL and return a clone URL."""

    try:
        return _github_clone_url(source)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_workbuddy_source(source) from None


def checkout_target(source: str, ref: str) -> Path:
    """Resolve a source and Git ref to a directory under the WorkBuddy checkout cache."""

    root = (powercontext_data_dir() / "checkouts" / "workbuddy").resolve()
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_workbuddy_ref(ref)
    source_key = hashlib.sha256(github_clone_url(source).encode("utf-8")).hexdigest()[:16]
    target = (root / source_key / ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_workbuddy_ref(ref) from error
    if target == root:
        raise SetupError.invalid_workbuddy_ref(ref)
    return target


def _install_hook_files(plugin_dir: Path, hooks_dir: Path) -> None:
    """Copy the hook driver, its modules, and the scope resolver into the hooks directory."""

    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        source_hooks = plugin_dir / WORKBUDDY_HOOKS_DIRNAME
        for name in WORKBUDDY_HOOK_MODULES:
            shutil.copy2(source_hooks / name, hooks_dir / name)
        shutil.copy2(plugin_dir / "scripts" / "workspace_scope.py", hooks_dir / WORKBUDDY_SCOPE_RESOLVER)
    except OSError as error:
        raise SetupError.workbuddy_hooks_write(hooks_dir, error) from error


def _merge_workbuddy_settings(settings_file: Path, hooks_dir: Path) -> None:
    """Register the PowerContext UserPromptSubmit hook without dropping existing settings."""

    settings = _load_json_object(
        settings_file,
        invalid=lambda: SetupError.invalid_workbuddy_settings(settings_file),
        io_error=lambda error: SetupError.workbuddy_settings_write(settings_file, error),
    )
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SetupError.invalid_workbuddy_settings(settings_file)
    hooks_dict = cast(dict[str, Any], hooks)
    matchers = hooks_dict.setdefault("UserPromptSubmit", [])
    if not isinstance(matchers, list):
        raise SetupError.invalid_workbuddy_settings(settings_file)

    _upsert_powercontext_hook(cast(list[Any], matchers), hooks_dir, settings_file)

    try:
        _write_json_atomically(settings_file, settings)
    except OSError as error:
        raise SetupError.workbuddy_settings_write(settings_file, error) from error


def _merge_workbuddy_mcp(mcp_file: Path) -> None:
    """Register the PowerContext MCP server without dropping existing servers."""

    config = _load_json_object(
        mcp_file,
        invalid=lambda: SetupError.invalid_workbuddy_mcp(mcp_file),
        io_error=lambda error: SetupError.workbuddy_mcp_write(mcp_file, error),
    )
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError.invalid_workbuddy_mcp(mcp_file)
    servers_dict = cast(dict[str, Any], servers)

    existing = servers_dict.get(WORKBUDDY_PLUGIN_NAME)
    entry = _workbuddy_mcp_entry(existing)
    if isinstance(existing, dict):
        servers_dict[WORKBUDDY_PLUGIN_NAME] = {**cast(dict[str, Any], existing), **entry}
    else:
        servers_dict[WORKBUDDY_PLUGIN_NAME] = entry

    try:
        _write_json_atomically(mcp_file, config)
    except OSError as error:
        raise SetupError.workbuddy_mcp_write(mcp_file, error) from error


def _workbuddy_mcp_entry(existing: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "http",
        "url": WORKBUDDY_MCP_URL,
        "headers": {"Authorization": WORKBUDDY_MCP_AUTHORIZATION},
        "description": WORKBUDDY_MCP_DESCRIPTION,
        "disabled": False,
    }
    if isinstance(existing, dict) and not _is_legacy_workbuddy_mcp_entry(existing):
        existing_url = existing.get("url")
        if isinstance(existing_url, str) and existing_url.strip():
            entry["url"] = existing_url
        existing_headers = existing.get("headers")
        if isinstance(existing_headers, dict) and existing_headers:
            entry["headers"] = cast(dict[str, Any], existing_headers)
    return entry


def _is_legacy_workbuddy_mcp_entry(existing: dict[str, Any]) -> bool:
    return existing == {
        "type": "http",
        "url": WORKBUDDY_LEGACY_MCP_URL,
        "headers": {},
        "description": WORKBUDDY_MCP_DESCRIPTION,
        "disabled": False,
    }


def require_replaceable_workbuddy_skill(target: Path) -> None:
    if target.exists() and not _owned_workbuddy_skill(target):
        raise SetupError.workbuddy_skill_conflict(target)


def _owned_workbuddy_skill(path: Path) -> bool:
    try:
        payload = json.loads((path / WORKBUDDY_SKILL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload == {"schema": 1, "owner": "powercontext", "integration": "workbuddy"}


def _install_workbuddy_skill(plugin_dir: Path, skills_dir: Path, hooks_dir: Path) -> None:
    """Copy the project-context Skill and resolve its hooks directory placeholder."""

    source = plugin_dir / WORKBUDDY_SKILLS_DIRNAME / WORKBUDDY_SKILL_NAME
    target = skills_dir / WORKBUDDY_SKILL_NAME
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        skill_markdown = target / "SKILL.md"
        content = skill_markdown.read_text(encoding="utf-8")
        content = content.replace(WORKBUDDY_PYTHON_PLACEHOLDER, _shell_argument(_python_executable()))
        content = content.replace(
            WORKBUDDY_SCOPE_BINDING_PLACEHOLDER,
            _shell_argument((hooks_dir / WORKBUDDY_SCOPE_RESOLVER).as_posix()),
        )
        skill_markdown.write_text(content, encoding="utf-8")
        (target / WORKBUDDY_SKILL_MANIFEST).write_text(
            json.dumps({"schema": 1, "owner": "powercontext", "integration": "workbuddy"}, indent=2) + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        raise SetupError.workbuddy_skill_write(target, error) from error


def _upsert_powercontext_hook(matchers: list[Any], hooks_dir: Path, settings_file: Path) -> None:
    """Insert or update the PowerContext command hook inside the UserPromptSubmit matchers."""

    entry: dict[str, Any] = {
        "type": "command",
        "command": _workbuddy_hook_command(hooks_dir),
        "timeout": WORKBUDDY_HOOK_TIMEOUT,
        "statusMessage": WORKBUDDY_HOOK_STATUS_MESSAGE,
    }
    for matcher in matchers:
        if not isinstance(matcher, dict):
            raise SetupError.invalid_workbuddy_settings(settings_file)
        matcher_dict = cast(dict[str, Any], matcher)
        group = matcher_dict.get("hooks")
        if group is None:
            continue
        if not isinstance(group, list):
            raise SetupError.invalid_workbuddy_settings(settings_file)
        group_list = cast(list[Any], group)
        for index, existing in enumerate(group_list):
            if isinstance(existing, dict) and _is_powercontext_hook(cast(dict[str, Any], existing)):
                group_list[index] = {**cast(dict[str, Any], existing), **entry}
                return
    matchers.append({"hooks": [entry]})


def _workbuddy_hook_command(hooks_dir: Path) -> str:
    script = hooks_dir / WORKBUDDY_HOOK_DRIVER
    return f"{_shell_argument(_python_executable())} {_shell_argument(script.as_posix())}"


def _python_executable() -> str:
    return Path(sys.executable).as_posix()


def _shell_argument(value: str | Path) -> str:
    text = str(value)
    if os.name == "nt":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def _is_powercontext_hook(entry: dict[str, Any]) -> bool:
    command = entry.get("command")
    return isinstance(command, str) and WORKBUDDY_HOOK_DRIVER in command


def _hooks_diagnostic(hooks_dir: Path) -> Diagnostic:
    missing_modules = [name for name in WORKBUDDY_HOOK_MODULES if not (hooks_dir / name).is_file()]
    if missing_modules or not (hooks_dir / WORKBUDDY_SCOPE_RESOLVER).is_file():
        return Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail="PowerContext WorkBuddy hooks are not installed",
        )
    return Diagnostic(status=DiagnosticStatus.OK, detail=f"hooks installed in {hooks_dir}")


def _settings_diagnostic(settings_file: Path) -> Diagnostic:
    try:
        settings = json.loads(settings_file.read_text(encoding="utf-8")) if settings_file.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"cannot read {settings_file}")
    if not isinstance(settings, dict):
        return Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail="PowerContext WorkBuddy hook is not registered in settings.json",
        )
    if not _settings_have_powercontext_hook(cast(dict[str, Any], settings)):
        return Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail="PowerContext WorkBuddy hook is not registered in settings.json",
        )
    return Diagnostic(status=DiagnosticStatus.OK, detail=str(settings_file))


def _mcp_diagnostic(mcp_file: Path) -> Diagnostic:
    try:
        config = json.loads(mcp_file.read_text(encoding="utf-8")) if mcp_file.exists() else {}
    except (OSError, UnicodeError, json.JSONDecodeError):
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"cannot read {mcp_file}")
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(servers, dict) or not isinstance(servers.get(WORKBUDDY_PLUGIN_NAME), dict):
        return Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail="PowerContext WorkBuddy MCP server is not registered in mcp.json",
        )
    return Diagnostic(status=DiagnosticStatus.OK, detail=str(mcp_file))


def _skill_diagnostic(skill_file: Path) -> Diagnostic:
    if not skill_file.is_file() or not _owned_workbuddy_skill(skill_file.parent):
        return Diagnostic(status=DiagnosticStatus.FAILED, detail="PowerContext WorkBuddy skill is not installed")
    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError:
        return Diagnostic(status=DiagnosticStatus.FAILED, detail=f"cannot read {skill_file}")
    if WORKBUDDY_SCOPE_BINDING_PLACEHOLDER in content or WORKBUDDY_PYTHON_PLACEHOLDER in content:
        return Diagnostic(
            status=DiagnosticStatus.FAILED,
            detail="PowerContext WorkBuddy skill still contains an unresolved command placeholder",
        )
    return Diagnostic(status=DiagnosticStatus.OK, detail=str(skill_file.parent))


def _settings_have_powercontext_hook(settings: dict[str, Any]) -> bool:
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return False
    hooks_dict = cast(dict[str, Any], hooks)
    matchers = hooks_dict.get("UserPromptSubmit")
    if not isinstance(matchers, list):
        return False
    for matcher in cast(list[Any], matchers):
        if not isinstance(matcher, dict):
            continue
        group = cast(dict[str, Any], matcher).get("hooks")
        if not isinstance(group, list):
            continue
        if any(isinstance(entry, dict) and _is_powercontext_hook(cast(dict[str, Any], entry)) for entry in group):
            return True
    return False


def _is_workbuddy_plugin(path: Path) -> bool:
    hooks = path / WORKBUDDY_HOOKS_DIRNAME
    scripts = path / "scripts"
    skill_markdown = path / WORKBUDDY_SKILLS_DIRNAME / WORKBUDDY_SKILL_NAME / "SKILL.md"
    return (
        all((hooks / name).is_file() for name in WORKBUDDY_HOOK_MODULES)
        and all((scripts / name).is_file() for name in WORKBUDDY_SCRIPT_MODULES)
        and skill_markdown.is_file()
    )


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
    try:
        clone_github_source(source, ref, target)
    except InvalidGitHubSourceError:
        raise SetupError.invalid_workbuddy_source(source) from None


def _load_json_object(
    path: Path,
    *,
    invalid: Callable[[], SetupError],
    io_error: Callable[[OSError], SetupError],
) -> dict[str, Any]:
    """Load a JSON object from disk, treating a missing file as an empty object."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except OSError as error:
        raise io_error(error) from error
    except (UnicodeError, json.JSONDecodeError) as error:
        raise invalid() from error
    if not isinstance(payload, dict):
        raise invalid()
    return cast(dict[str, Any], payload)


def _write_json_atomically(path: Path, payload: dict[str, Any]) -> None:
    _write_bytes_atomically(path, (json.dumps(payload, indent=2) + "\n").encode("utf-8"))


def _write_bytes_atomically(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.write(descriptor, content)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        with suppress(FileNotFoundError):
            temporary.unlink()


def _read_bytes_or_none(path: Path, *, io_error: Callable[[OSError], SetupError]) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise io_error(error) from error


def _snapshot_directory(path: Path) -> Path | None:
    if not path.is_dir():
        return None
    backup = Path(tempfile.mkdtemp(prefix=f".{path.name}-rollback-"))
    shutil.rmtree(backup)
    shutil.copytree(path, backup)
    return backup


def _restore_file(path: Path, original: bytes | None) -> None:
    if original is None:
        path.unlink(missing_ok=True)
        return
    _write_bytes_atomically(path, original)


def _restore_directory(path: Path, backup: Path | None) -> None:
    _remove_path(path)
    if backup is not None and backup.is_dir():
        shutil.copytree(backup, path)


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


def _remove_path(path: Path | None) -> None:
    if path is None:
        return
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


__all__ = [
    "WORKBUDDY_PLUGIN_NAME",
    "WorkBuddySetupResult",
    "checkout_target",
    "github_clone_url",
    "install_workbuddy_plugin",
    "plugin_dir_from_checkout",
    "require_workbuddy_plugin",
    "resolve_workbuddy_plugin_dir",
    "run_workbuddy_diagnostics",
    "workbuddy_home",
]
