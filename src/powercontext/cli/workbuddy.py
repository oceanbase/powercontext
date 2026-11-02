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

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from powercontext.cli.guidance import (
    ENTRY_NAME,
    HOST_GUIDANCE,
    SKILL_DIRECTORY,
    GuidanceError,
    refresh_skill,
    write_if_changed,
)
from powercontext.cli.installation import publish, write_json
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import configure_connection
from powercontext.paths import powercontext_data_dir

WORKBUDDY_HOME_ENV = "WORKBUDDY_HOME"
WORKBUDDY_PLUGIN_NAME = "powercontext"
WORKBUDDY_PLUGIN_RELATIVE = Path(HOST_GUIDANCE["workbuddy"].plugin)
WORKBUDDY_HOOKS_DIRNAME = "hooks"
WORKBUDDY_SKILLS_DIRNAME = SKILL_DIRECTORY.parent.name
WORKBUDDY_SKILL_NAME = ENTRY_NAME
WORKBUDDY_SKILL_MANIFEST = ".powercontext.json"
WORKBUDDY_PYTHON_PLACEHOLDER = "${POWERCONTEXT_PYTHON}"
WORKBUDDY_SCOPE_BINDING_PLACEHOLDER = "${POWERCONTEXT_SCOPE_BINDING_SCRIPT}"
WORKBUDDY_HOOK_DRIVER = "workbuddy_powercontext_hook.py"
WORKBUDDY_SCOPE_RESOLVER = "powercontext_scope_binding.py"
WORKBUDDY_HOOK_MODULES = (
    "workbuddy_powercontext_hook.py",
    "workbuddy_settings.py",
    "powercontext_client_config.py",
    "prepared_context.py",
    "prompt_privacy.py",
)
WORKBUDDY_SCRIPT_MODULES = ("__init__.py", "workspace_scope.py")
WORKBUDDY_SERVER_URL_ENV = "POWERCONTEXT_WORKBUDDY_SERVER_URL"
WORKBUDDY_AUTHORIZATION_ENV = "POWERCONTEXT_WORKBUDDY_AUTHORIZATION"
WORKBUDDY_MCP_URL = f"${{{WORKBUDDY_SERVER_URL_ENV}:-http://127.0.0.1:8000}}/mcp"
WORKBUDDY_MCP_AUTHORIZATION = f"${{{WORKBUDDY_AUTHORIZATION_ENV}:-}}"
WORKBUDDY_MCP_DESCRIPTION = "PowerContext agent memory & handoff MCP server (local service on port 8000)"
WORKBUDDY_HOOK_STATUS_MESSAGE = "Syncing PowerContext"
WORKBUDDY_HOOK_TIMEOUT = 30


def require_workbuddy_plugin(path: Path) -> None:
    """Reject a directory that cannot provide the WorkBuddy hooks, MCP, and Skill."""

    if not all((path / "hooks" / name).is_file() for name in WORKBUDDY_HOOK_MODULES) or not all(
        (path / "scripts" / name).is_file() for name in WORKBUDDY_SCRIPT_MODULES
    ):
        raise SetupError(f"PowerContext WorkBuddy plugin was not found under {path}.")  # noqa: TRY003


def workbuddy_home() -> Path:
    """Return WorkBuddy's user home, honoring the host's environment override."""

    configured = os.environ.get(WORKBUDDY_HOME_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else (Path.home() / ".workbuddy").resolve()


def _install_hook_files(plugin_dir: Path, hooks_dir: Path) -> None:
    """Copy the hook driver, its modules, and the scope resolver into the hooks directory."""

    try:
        hooks_dir.mkdir(parents=True, exist_ok=True)
        source_hooks = plugin_dir / WORKBUDDY_HOOKS_DIRNAME
        for name in WORKBUDDY_HOOK_MODULES:
            shutil.copy2(source_hooks / name, hooks_dir / name)
        shutil.copy2(plugin_dir / "scripts" / "workspace_scope.py", hooks_dir / WORKBUDDY_SCOPE_RESOLVER)
    except OSError as error:
        raise SetupError(f"Cannot install PowerContext WorkBuddy hooks at {hooks_dir}: {error}") from error  # noqa: TRY003


def _merge_workbuddy_settings(settings_file: Path, hooks_dir: Path) -> None:
    """Register the PowerContext UserPromptSubmit hook without dropping existing settings."""

    settings = _load_json_object(
        settings_file,
        invalid=lambda: SetupError(
            f"WorkBuddy settings at {settings_file} must contain a JSON object with a hooks mapping."
        ),
        io_error=lambda error: SetupError(f"Cannot update WorkBuddy settings at {settings_file}: {error}"),
    )
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise SetupError(f"WorkBuddy settings at {settings_file} must contain a JSON object with a hooks mapping.")  # noqa: TRY003
    hooks_dict = cast(dict[str, Any], hooks)
    matchers = hooks_dict.setdefault("UserPromptSubmit", [])
    if not isinstance(matchers, list):
        raise SetupError(f"WorkBuddy settings at {settings_file} must contain a JSON object with a hooks mapping.")  # noqa: TRY003

    _upsert_powercontext_hook(cast(list[Any], matchers), hooks_dir, settings_file)

    try:
        write_json(settings_file, settings)
    except OSError as error:
        raise SetupError(f"Cannot update WorkBuddy settings at {settings_file}: {error}") from error  # noqa: TRY003


def _merge_workbuddy_mcp(mcp_file: Path, *, server_url: str | None = None) -> None:
    """Register the PowerContext MCP server without dropping existing servers."""

    config = _load_json_object(
        mcp_file,
        invalid=lambda: SetupError(
            f"WorkBuddy MCP configuration at {mcp_file} must contain a JSON object with an mcpServers mapping."
        ),
        io_error=lambda error: SetupError(f"Cannot update WorkBuddy MCP configuration at {mcp_file}: {error}"),
    )
    servers = config.setdefault("mcpServers", {})
    if not isinstance(servers, dict):
        raise SetupError(  # noqa: TRY003
            f"WorkBuddy MCP configuration at {mcp_file} must contain a JSON object with an mcpServers mapping."
        )
    servers_dict = cast(dict[str, Any], servers)

    existing = servers_dict.get(WORKBUDDY_PLUGIN_NAME)
    entry = _workbuddy_mcp_entry(existing)
    if server_url is not None and entry.get("url") != server_url.rstrip("/") + "/mcp":
        entry["url"] = f"${{{WORKBUDDY_SERVER_URL_ENV}:-{server_url}}}/mcp"
    if isinstance(existing, dict):
        servers_dict[WORKBUDDY_PLUGIN_NAME] = {**cast(dict[str, Any], existing), **entry}
    else:
        servers_dict[WORKBUDDY_PLUGIN_NAME] = entry

    try:
        write_json(mcp_file, config)
    except OSError as error:
        raise SetupError(f"Cannot update WorkBuddy MCP configuration at {mcp_file}: {error}") from error  # noqa: TRY003


def _workbuddy_mcp_entry(existing: Any) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "type": "http",
        "url": WORKBUDDY_MCP_URL,
        "headers": {"Authorization": WORKBUDDY_MCP_AUTHORIZATION},
        "description": WORKBUDDY_MCP_DESCRIPTION,
        "disabled": False,
    }
    if isinstance(existing, dict):
        entry["disabled"] = existing.get("disabled", False)
        existing_url = existing.get("url")
        if isinstance(existing_url, str) and existing_url.strip():
            entry["url"] = existing_url
        existing_headers = existing.get("headers")
        if isinstance(existing_headers, dict) and existing_headers:
            entry["headers"] = cast(dict[str, Any], existing_headers)
    return entry


def require_replaceable_workbuddy_skill(target: Path) -> None:
    if target.exists() and not _owned_workbuddy_skill(target):
        raise SetupError(f"WorkBuddy Skill path {target} already exists and is not owned by PowerContext.")  # noqa: TRY003


def _owned_workbuddy_skill(path: Path) -> bool:
    try:
        payload = json.loads((path / WORKBUDDY_SKILL_MANIFEST).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return payload == {"schema": 1, "owner": "powercontext", "integration": "workbuddy"}


def _install_workbuddy_skill(plugin_dir: Path, skills_dir: Path, hooks_dir: Path) -> None:
    """Copy the powercontext-project-context Skill and resolve its hooks directory placeholder."""

    source = plugin_dir / WORKBUDDY_SKILLS_DIRNAME / WORKBUDDY_SKILL_NAME
    target = skills_dir / WORKBUDDY_SKILL_NAME
    try:
        skills_dir.mkdir(parents=True, exist_ok=True)
        refresh_skill(
            source,
            target,
            replacements={
                WORKBUDDY_PYTHON_PLACEHOLDER: _shell_argument(_python_executable()),
                WORKBUDDY_SCOPE_BINDING_PLACEHOLDER: _shell_argument((hooks_dir / WORKBUDDY_SCOPE_RESOLVER).as_posix()),
            },
        )
        write_if_changed(
            target / WORKBUDDY_SKILL_MANIFEST,
            (json.dumps({"schema": 1, "owner": "powercontext", "integration": "workbuddy"}, indent=2) + "\n").encode(
                "utf-8"
            ),
        )
    except OSError as error:
        raise SetupError(f"Cannot install PowerContext WorkBuddy skill at {target}: {error}") from error  # noqa: TRY003
    except GuidanceError as error:
        raise SetupError(str(error)) from error


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
            raise SetupError(f"WorkBuddy settings at {settings_file} must contain a JSON object with a hooks mapping.")  # noqa: TRY003
        matcher_dict = cast(dict[str, Any], matcher)
        group = matcher_dict.get("hooks")
        if group is None:
            continue
        if not isinstance(group, list):
            raise SetupError(f"WorkBuddy settings at {settings_file} must contain a JSON object with a hooks mapping.")  # noqa: TRY003
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
    if servers[WORKBUDDY_PLUGIN_NAME].get("disabled") is True:
        return Diagnostic(DiagnosticStatus.FAILED, "PowerContext MCP is disabled; enable it in WorkBuddy")
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


__all__ = ["WORKBUDDY_PLUGIN_NAME", "require_workbuddy_plugin", "workbuddy_home"]


class WorkBuddyInstaller:
    def install(self, source):
        root = workbuddy_home()
        require_replaceable_workbuddy_skill(root / WORKBUDDY_SKILLS_DIRNAME / WORKBUDDY_SKILL_NAME)
        with source.package("workbuddy") as package:
            require_workbuddy_plugin(package)
            hooks = root / WORKBUDDY_HOOKS_DIRNAME
            skill = root / WORKBUDDY_SKILLS_DIRNAME / WORKBUDDY_SKILL_NAME
            root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=root) as temporary:
                staging = Path(temporary)
                staged_hooks, staged_skills = staging / "hooks", staging / "skills"
                if skill.exists():
                    shutil.copytree(skill, staged_skills / WORKBUDDY_SKILL_NAME, symlinks=True)
                settings_file = root / "settings.json"
                _install_hook_files(package, staged_hooks)
                _install_workbuddy_skill(package, staged_skills, hooks)
                candidate = staging / "settings.json"
                if settings_file.exists():
                    shutil.copy2(settings_file, candidate)
                _merge_workbuddy_settings(candidate, hooks)
                replacements = [
                    (staged_hooks / name, hooks / name) for name in (*WORKBUDDY_HOOK_MODULES, WORKBUDDY_SCOPE_RESOLVER)
                ]
                replacements.append((staged_skills / WORKBUDDY_SKILL_NAME, skill))
                publish(replacements, {settings_file: candidate.read_bytes()})
            return {
                "plugin": WORKBUDDY_PLUGIN_NAME,
                "plugin_path": str(root),
                "workbuddy_home": str(root),
                "hooks_dir": str(hooks),
                "data_dir": str(powercontext_data_dir()),
            }

    def configure(self, connection):
        if not (workbuddy_home() / "hooks" / WORKBUDDY_HOOK_MODULES[0]).is_file():
            raise SetupError("PowerContext is not installed in workbuddy; run powercontext setup workbuddy")  # noqa: TRY003
        root = workbuddy_home()
        path = root / "mcp.json"
        with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=root) as temporary:
            candidate = Path(temporary) / "mcp.json"
            if path.exists():
                shutil.copy2(path, candidate)
            _merge_workbuddy_mcp(candidate, server_url=connection.server_url)
            state = configure_connection(connection, writes={path: candidate.read_bytes()})
        return {"authorization_state": state}

    def verify(self) -> dict[str, Diagnostic]:
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
