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

"""Install and diagnose the native PowerContext Antigravity plugin."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from powercontext.cli.authorization import normalize_authorization, setup_authorization_value
from powercontext.cli.guidance import SKILL_FILE
from powercontext.cli.installation import PluginSource, SetupError, publish, write_json
from powercontext.cli.system import Diagnostic, DiagnosticStatus
from powercontext.cli.transport import SetupTransport, setup_transport_updates
from powercontext.client.transport_policy import normalize_client_url

_HOST = "antigravity"
_MCP = "mcp_config.json"
_SETTINGS = "powercontext.json"
_HOOKS = "hooks.json"
_SCRIPT = "scripts/lifecycle.py"
_EVENTS = ("SessionStart", "PreInvocation")


def _plugin_dir() -> Path:
    return Path.home() / ".gemini/config/plugins/powercontext"


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("expected a JSON object")  # noqa: TRY003
    return value


def _has_manifest(bundle: Path) -> bool:
    try:
        return _read(bundle / "plugin.json").get("name") == "powercontext"
    except (OSError, ValueError, TypeError):
        return False


def _check(detail: str, valid: bool) -> Diagnostic:
    return Diagnostic(DiagnosticStatus.OK if valid else DiagnosticStatus.FAILED, detail)


class AntigravityInstaller:
    """Manage the self-contained plugin in Antigravity's global plugin directory."""

    def __init__(self, *, capture_prompts: bool | None = None) -> None:
        self.capture_prompts = capture_prompts

    def install(self, source: PluginSource) -> dict[str, str]:
        target = _plugin_dir()
        if target.is_symlink() or (target.exists() and not _has_manifest(target)):
            raise SetupError(f"Antigravity plugin path is already occupied at {target}")  # noqa: TRY003
        target.parent.mkdir(parents=True, exist_ok=True)
        with source.package(_HOST) as package, tempfile.TemporaryDirectory(dir=target.parent) as temporary:
            staged = Path(temporary) / "plugin"
            shutil.copytree(package, staged, ignore=shutil.ignore_patterns("__pycache__"))
            hooks = _read(staged / _HOOKS)
            python = subprocess.list2cmdline([sys.executable]) if os.name == "nt" else shlex.quote(sys.executable)
            for event in _EVENTS:
                hooks["powercontext"][event][0]["command"] = f"{python} ./{_SCRIPT} {event}"
            write_json(staged / _HOOKS, hooks)
            for name in (_MCP, _SETTINGS):
                if (target / name).is_file():
                    shutil.copy2(target / name, staged / name)
            publish(replacements=[(staged, target)])
        return {"plugin": "powercontext", "plugin_path": str(target)}

    def configure(self, connection: SetupTransport) -> dict[str, str]:
        bundle = _plugin_dir()
        if bundle.is_symlink() or not _has_manifest(bundle):
            raise SetupError("PowerContext is not installed in Antigravity; run powercontext setup antigravity")  # noqa: TRY003
        endpoint = connection.server_url
        token = setup_authorization_value(_HOST)
        state = "not_configured"
        entry: dict[str, object] = {"url": endpoint + "/mcp"}
        if token and token.strip():
            entry["headers"] = {"Authorization": normalize_authorization(token)}
            state = "configured"
        else:
            try:
                old_endpoint, authorization = self.connection()
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
            else:
                if authorization and old_endpoint == endpoint:
                    entry["headers"] = {"Authorization": authorization}
                    state = "preserved"
        writes = {bundle / _MCP: _json_bytes({"mcpServers": {"memory": entry}})}
        if self.capture_prompts is not None:
            writes[bundle / _SETTINGS] = _json_bytes({"capture_prompts": self.capture_prompts})
        publish(writes={**writes, **setup_transport_updates(connection)})
        return {"authorization_state": state}

    @staticmethod
    def connection() -> tuple[str, str | None]:
        entry = _read(_plugin_dir() / _MCP)["mcpServers"]["memory"]
        url = entry["url"]
        if not isinstance(url, str) or not url.endswith("/mcp") or entry.get("disabled") is True:
            raise ValueError("invalid PowerContext Antigravity MCP configuration")  # noqa: TRY003
        endpoint = normalize_client_url(url).removesuffix("/mcp")
        authorization = entry.get("headers", {}).get("Authorization")
        if authorization is not None and normalize_authorization(authorization) != authorization:
            raise ValueError("invalid PowerContext Antigravity authorization header")  # noqa: TRY003
        return endpoint, authorization

    def verify(self) -> dict[str, Diagnostic]:
        bundle = _plugin_dir()
        try:
            group = _read(bundle / _HOOKS)["powercontext"]
            hooks_ok = group.get("enabled", True) is True and all(
                any(
                    hook.get("type") == "command" and hook.get("command", "").endswith(f" ./{_SCRIPT} {event}")
                    for hook in group[event]
                )
                for event in _EVENTS
            )
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            hooks_ok = False
        hooks_ok = (
            hooks_ok and (bundle / _SCRIPT).is_file() and (bundle / "powercontext_lifecycle/__init__.py").is_file()
        )
        try:
            self.connection()
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            mcp_ok = False
        else:
            mcp_ok = True
        return {
            "plugin": _check(str(bundle), _has_manifest(bundle)),
            "hooks": _check(str(bundle / _HOOKS), hooks_ok),
            "mcp": _check(str(bundle / _MCP), mcp_ok),
            "skill": _check(str(bundle / SKILL_FILE), (bundle / SKILL_FILE).is_file()),
        }


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
