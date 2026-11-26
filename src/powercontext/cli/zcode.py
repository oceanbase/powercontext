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

"""Install and inspect the ZCode plugin shared by the CLI and Windows desktop app."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlsplit

from powercontext.cli.authorization import normalize_authorization
from powercontext.cli.guidance import HOST_GUIDANCE, preserve_installed_guidance
from powercontext.cli.installation import publish
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import setup_environment, setup_transport_updates
from powercontext.client.transport_policy import normalize_client_url

PLUGIN_RELATIVE = Path(HOST_GUIDANCE["zcode"].plugin)
OWNER_MARKER = ".powercontext-owned"
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


def _endpoint(value: str) -> str:
    endpoint = normalize_client_url(value).removesuffix("/mcp").rstrip("/")
    if urlsplit(endpoint).path:
        raise SetupError("ZCode Server URL must be an HTTP(S) origin without path, credentials, query, or fragment")  # noqa: TRY003
    return endpoint


def zcode_config_file() -> Path:
    """ZCode's shared user-level plugin configuration file."""

    return Path.home() / ".zcode" / "cli" / "config.json"


def zcode_plugin_dir() -> Path:
    """PowerContext-owned copy under the ZCode user directory."""

    return zcode_config_file().parent / "plugins" / "powercontext"


def zcode_executable() -> str | None:
    """Locate a ZCode CLI build or the installed Windows desktop app."""

    explicit = os.environ.get("ZCODE_CLI_BIN")
    if explicit:
        path = Path(explicit).expanduser()
        return str(path) if path.is_file() else None
    command = which("zcode.cmd" if os.name == "nt" else "zcode") or which("zcode")
    if command:
        return command
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            desktop = Path(local_app_data) / "Programs" / "ZCode" / "ZCode.exe"
            if desktop.is_file():
                return str(desktop)
    return None


def _read_config(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise SetupError(f"ZCode config at {path} is not valid UTF-8 JSON") from error  # noqa: TRY003
    if not isinstance(config, dict):
        raise SetupError(f"ZCode config at {path} must be a JSON object")  # noqa: TRY003
    plugins = config.get("plugins", {})
    if (
        not isinstance(plugins, dict)
        or not isinstance(plugins.get("dirs", []), list)
        or any(not isinstance(item, str) for item in plugins.get("dirs", []))
    ):
        raise SetupError(f"ZCode plugins.dirs at {path} must be a string array")  # noqa: TRY003
    return config


def _mcp_authorization_matches(entry: dict[str, Any]) -> bool:
    authorization = os.environ.get("POWERCONTEXT_ZCODE_AUTHORIZATION")
    if entry.get("headers", {}).get("Authorization") != "${POWERCONTEXT_ZCODE_AUTHORIZATION}":
        return not authorization
    try:
        return authorization is not None and normalize_authorization(authorization) == authorization
    except ValueError:
        return False


def _check_zcode_modules(destination: Path) -> Diagnostic:
    ok = DiagnosticStatus.OK
    failed = DiagnosticStatus.FAILED
    hook = destination / "hooks" / "user_prompt_submit.mjs"
    modules = [
        hook,
        *(destination / "hooks" / name for name in ("session_start.mjs", "stop.mjs")),
        *(
            destination / "shared" / name
            for name in (
                "settings.mjs",
                "transport.mjs",
                "git-worktree.mjs",
                "scope.mjs",
                "observations.mjs",
                "context.mjs",
                "pending.mjs",
            )
        ),
        *(destination / "scripts" / name for name in ("scope.mjs", "status.mjs", "doctor.mjs", "pending.mjs")),
    ]
    hooks_file = destination / "hooks" / "hooks.json"
    try:
        declared = json.loads(hooks_file.read_text(encoding="utf-8"))
        hook_ok = all(module.is_file() for module in modules) and all(
            declared["hooks"][name] for name in ("UserPromptSubmit", "SessionStart", "Stop")
        )
    except (OSError, ValueError, KeyError, TypeError):
        hook_ok = False
    node = which("node")
    if hook_ok and node:
        try:
            hook_ok = all(
                subprocess.run(  # noqa: S603 - fixed Node executable and managed plugin path.
                    [node, "--check", str(module)], capture_output=True, check=False, timeout=5
                ).returncode
                == 0
                for module in modules
            )
        except (OSError, subprocess.SubprocessError):
            hook_ok = False
    else:
        hook_ok = False
    return Diagnostic(ok if hook_ok else failed, "valid" if hook_ok else "missing, invalid, or Node unavailable")


def _runtime_observation_detail(item: dict[str, Any]) -> str:
    detail = ""
    observed_at = item.get("observed_at")
    selection = item.get("selection")
    scope_id = item.get("scope_id")
    if observed_at is not None:
        if (
            not isinstance(observed_at, int)
            or observed_at < 0
            or selection not in {"session_exact", "workspace_latest"}
        ):
            raise ValueError
        detail += f" observed_at={observed_at} selection={selection}"
        if scope_id is not None:
            if (
                not isinstance(scope_id, str)
                or len(scope_id) > 256
                or not all(
                    character.isascii() and (character.isalnum() or character in ":_-") for character in scope_id
                )
            ):
                raise ValueError
            detail += f" scope_id={scope_id}"
    return detail


def _decode_zcode_probe(output: str, prepare: bool) -> dict[str, Diagnostic]:
    result = json.loads(output)
    if result.get("schema") != "powercontext.zcode.diagnostics.v2":
        raise ValueError
    checks = result["checks"]
    expected = {"server", "protected_api", "scope_probe", "runtime", "mcp_session"}
    if not expected.issubset(checks):
        raise ValueError
    diagnostics = {}
    for name in expected | ({"prepare_probe"} if prepare else set()):
        item = checks.get(name, {"status": "skipped", "code": "not_observed"})
        status = DiagnosticStatus(item["status"])
        code = item["code"]
        if not isinstance(code, str) or not code.replace("_", "").isalnum() or len(code) > 64:
            raise ValueError
        message = code + (" (probe process)" if name.endswith("probe") or name == "protected_api" else "")
        if name == "runtime":
            message += " (historical Hook observation; not current-session MCP discovery)"
            message += _runtime_observation_detail(item)
        stages = item.get("stages", {}) if name == "runtime" else {}
        if not isinstance(stages, dict) or not all(
            key in {"scope", "prepare", "capture", "context_output", "flush"}
            and isinstance(value, str)
            and len(value) <= 200
            and all(part.replace("_", "").isalnum() for part in value.split(":"))
            for key, value in stages.items()
        ):
            raise ValueError
        diagnostics[name] = Diagnostic(
            status, message, checks=item.get("checks") if name == "server" else stages or None
        )
    return diagnostics


def _probe_zcode(destination: Path, runtime_data_dir: Path | None, prepare: bool) -> dict[str, Diagnostic]:
    node = which("node")
    script = destination / "scripts" / "doctor.mjs"
    if not node or not script.is_file():
        return {"probe": Diagnostic(DiagnosticStatus.FAILED, "diagnostic script or Node unavailable")}
    args = [node, str(script)]
    if runtime_data_dir is not None:
        args.extend(["--data-dir", str(runtime_data_dir.resolve())])
    if prepare:
        args.append("--prepare")
    try:
        process = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", check=False, timeout=6)  # noqa: S603
        diagnostics = _decode_zcode_probe(process.stdout, prepare)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        return {
            "probe": Diagnostic(
                DiagnosticStatus.FAILED, "diagnostic probe unavailable or unsupported; run powercontext setup zcode"
            )
        }

    return diagnostics


@dataclass
class ZCodeInstaller:
    capture_prompts: bool | None = None
    boundary_flush: bool | None = None

    def install(self, source):
        if zcode_executable() is None:
            raise SetupError("ZCode CLI or Windows desktop app was not found; set ZCODE_CLI_BIN for a built checkout")  # noqa: TRY003
        with source.package("zcode") as plugin:
            config_path, destination = zcode_config_file(), zcode_plugin_dir()
            config = _read_config(config_path)
            if destination.exists() and not (destination / OWNER_MARKER).is_file():
                raise SetupError(f"ZCode plugin path {destination} already exists and is not owned by PowerContext")  # noqa: TRY003
            plugins = config.setdefault("plugins", {})
            plugins.setdefault("enabled", True)
            dirs = plugins.setdefault("dirs", [])
            if str(destination) not in dirs:
                dirs.append(str(destination))
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "plugin"
                shutil.copytree(plugin, staged, ignore=shutil.ignore_patterns("tests", "node_modules"))
                preserve_installed_guidance(destination, staged)
                for name in ("powercontext.json", ".mcp.json"):
                    if (destination / name).is_file():
                        shutil.copy2(destination / name, staged / name)
                (staged / OWNER_MARKER).write_text("powercontext-zcode-v1\n", encoding="utf-8")
                publish(
                    [(staged, destination)],
                    {config_path: (json.dumps(config, ensure_ascii=False, indent=2) + "\n").encode()},
                )
            previous = _read_config(destination / "powercontext.json")
            return {
                "plugin": "powercontext",
                "plugin_path": str(destination),
                "config_file": str(config_path),
                "server_url": previous.get("server_url", DEFAULT_SERVER_URL),
            }

    def configure(self, connection):
        if not (zcode_plugin_dir() / OWNER_MARKER).is_file():
            raise SetupError("PowerContext is not installed in zcode; run powercontext setup zcode")  # noqa: TRY003
        endpoint = _endpoint(connection.server_url)
        destination = zcode_plugin_dir()
        previous = _read_config(destination / "powercontext.json")
        capture = self.capture_prompts if self.capture_prompts is not None else previous.get("capture_prompts", True)
        boundary = self.boundary_flush
        if boundary is None:
            value = (setup_environment() | dict(os.environ)).get("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH")
            boundary = (
                value.lower() not in {"0", "false", "no", "off"}
                if value is not None
                else previous.get("boundary_flush", False)
            )
        mcp = _read_config(destination / ".mcp.json")
        server = mcp["mcpServers"]["powercontext"]
        headers = server.setdefault("headers", {})
        authorization = (setup_environment() | dict(os.environ)).get("POWERCONTEXT_ZCODE_AUTHORIZATION")
        if authorization or endpoint != _endpoint(server["url"]):
            headers = {name: value for name, value in headers.items() if name.lower() != "authorization"}
            server["headers"] = headers
        if authorization:
            headers["Authorization"] = "${POWERCONTEXT_ZCODE_AUTHORIZATION}"
        server["url"] = endpoint + "/mcp"
        settings = {
            "server_url": endpoint,
            "allow_insecure_http": connection.allow_insecure_http,
            "capture_prompts": capture,
            "boundary_flush": boundary,
        }
        publish(
            writes={
                **setup_transport_updates(connection),
                destination / "powercontext.json": (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode(),
                destination / ".mcp.json": (json.dumps(mcp, ensure_ascii=False, indent=2) + "\n").encode(),
            }
        )
        return {"server_url": endpoint}

    def verify(self, *, runtime_data_dir: Path | None = None, prepare: bool = False) -> dict[str, Diagnostic]:
        """Check CLI, registration, Hook, MCP, and Server independently."""

        ok = DiagnosticStatus.OK
        failed = DiagnosticStatus.FAILED
        executable = zcode_executable()
        diagnostics = {
            "zcode": Diagnostic(
                ok if executable else failed, executable or "ZCode CLI or Windows desktop app was not found"
            ),
        }
        config_path = zcode_config_file()
        destination = zcode_plugin_dir()
        try:
            config = _read_config(config_path)
            plugins = config.get("plugins", {})
            registered = str(destination) in plugins.get("dirs", []) and plugins.get("enabled") is not False
            diagnostics["plugin"] = Diagnostic(
                ok if registered else failed, "registered" if registered else "not registered"
            )
        except SetupError:
            diagnostics["plugin"] = Diagnostic(failed, "ZCode user config is invalid or unreadable")
        diagnostics["hooks"] = _check_zcode_modules(destination)
        try:
            mcp = json.loads((destination / ".mcp.json").read_text(encoding="utf-8"))
            endpoint = json.loads((destination / "powercontext.json").read_text(encoding="utf-8"))["server_url"]
            entry = mcp["mcpServers"]["powercontext"]
            mcp_ok = entry["url"] == endpoint + "/mcp" and _mcp_authorization_matches(entry)
            if not isinstance(endpoint, str) or not endpoint:
                mcp_ok = False
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            endpoint = None
            mcp_ok = False
        diagnostics["mcp"] = Diagnostic(ok if mcp_ok else failed, "configured" if mcp_ok else "missing or mismatched")
        if not endpoint:
            diagnostics["server"] = Diagnostic(DiagnosticStatus.SKIPPED, "endpoint is unavailable")
        else:
            diagnostics.update(_probe_zcode(destination, runtime_data_dir, prepare))
        return diagnostics
