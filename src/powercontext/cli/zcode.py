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
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlsplit

from powercontext.cli.authorization import normalize_authorization
from powercontext.cli.git_source import InvalidGitHubSourceError, clone_github_source, is_local_source
from powercontext.cli.guidance import HOST_GUIDANCE, preserve_installed_guidance
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import is_remote_http, setup_environment
from powercontext.client.transport_policy import normalize_client_url

PLUGIN_RELATIVE = Path(HOST_GUIDANCE["zcode"].plugin)
OWNER_MARKER = ".powercontext-owned"
DEFAULT_SERVER_URL = "http://127.0.0.1:8000"


@dataclass(frozen=True, slots=True)
class ZCodeSetupResult:
    plugin: str
    plugin_path: str
    config_file: str
    server_url: str


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


def _plugin_from_checkout(root: Path) -> Path:
    for candidate in (root, root / PLUGIN_RELATIVE):
        try:
            manifest = json.loads((candidate / ".zcode-plugin" / "plugin.json").read_text(encoding="utf-8"))
            if manifest.get("name") == "powercontext" and (candidate / "hooks" / "user_prompt_submit.mjs").is_file():
                return candidate
        except (OSError, ValueError, AttributeError):
            continue
    raise SetupError(f"PowerContext ZCode plugin was not found under {root}")  # noqa: TRY003


def _source_plugin(source: str, ref: str, checkout: Path) -> Path:
    if is_local_source(source):
        return _plugin_from_checkout(Path(source).expanduser().resolve())
    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError("invalid ZCode Git ref")  # noqa: TRY003
    try:
        clone_github_source(source, ref, checkout)
    except InvalidGitHubSourceError:
        raise SetupError("invalid ZCode source; use a local path or HTTPS/SSH GitHub repository") from None  # noqa: TRY003
    return _plugin_from_checkout(checkout)


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


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


@contextmanager
def preserve_zcode_installation() -> Iterator[None]:
    """Restore managed ZCode state if later setup steps, including shared settings, fail."""

    config_path = zcode_config_file()
    destination = zcode_plugin_dir()
    old_config = config_path.read_bytes() if config_path.exists() else None
    existed = destination.exists()
    owned = (destination / OWNER_MARKER).is_file()
    with tempfile.TemporaryDirectory(prefix="powercontext-zcode-rollback-") as temporary:
        backup = Path(temporary) / "plugin"
        if owned:
            shutil.copytree(destination, backup)
        try:
            yield
        except BaseException:
            try:
                if owned or not existed:
                    if destination.exists():
                        shutil.rmtree(destination)
                    if owned:
                        shutil.copytree(backup, destination)
                if old_config is None:
                    config_path.unlink(missing_ok=True)
                else:
                    config_path.parent.mkdir(parents=True, exist_ok=True)
                    config_path.write_bytes(old_config)
            except OSError as error:
                raise SetupError("ZCode setup failed and could not restore its previous configuration") from error  # noqa: TRY003
            raise


def saved_zcode_capture_prompts() -> bool:
    """Keep an installed capture preference when setup has no explicit override."""

    return _read_config(zcode_plugin_dir() / "powercontext.json").get("capture_prompts") is not False


def install_zcode_plugin(  # noqa: C901 - stages and rolls back two user-owned destinations.
    *,
    source: str,
    ref: str,
    server_url: str = DEFAULT_SERVER_URL,
    allow_insecure_http: bool = False,
    capture_prompts: bool | None = None,
    boundary_flush: bool | None = None,
) -> ZCodeSetupResult:
    """Install a managed plugin copy and register it without replacing other ZCode settings."""

    if zcode_executable() is None:
        raise SetupError("ZCode CLI or Windows desktop app was not found; set ZCODE_CLI_BIN for a built checkout")  # noqa: TRY003
    endpoint = normalize_client_url(server_url).removesuffix("/mcp").rstrip("/")
    parsed = urlsplit(endpoint)
    if parsed.path or parsed.query or parsed.fragment or parsed.username or parsed.password:
        raise SetupError("ZCode Server URL must be an HTTP(S) origin without path, credentials, query, or fragment")  # noqa: TRY003
    if is_remote_http(endpoint) and not allow_insecure_http:
        raise SetupError("Remote ZCode HTTP requires explicit --allow-insecure-http consent")  # noqa: TRY003
    use_authorization_env = bool((setup_environment() | dict(os.environ)).get("POWERCONTEXT_ZCODE_AUTHORIZATION"))
    config_path = zcode_config_file()
    destination = zcode_plugin_dir()
    config = _read_config(config_path)
    if destination.exists() and not (destination / OWNER_MARKER).is_file():
        raise SetupError(f"ZCode plugin path {destination} already exists and is not owned by PowerContext")  # noqa: TRY003
    previous = _read_config(destination / "powercontext.json")
    if capture_prompts is None:
        capture_prompts = previous.get("capture_prompts") is not False
    if boundary_flush is None:
        boundary = (setup_environment() | dict(os.environ)).get("POWERCONTEXT_ZCODE_BOUNDARY_FLUSH")
        boundary_flush = (
            boundary.lower() not in {"0", "false", "no", "off"}
            if boundary is not None
            else previous.get("boundary_flush") is True
        )
    plugins = config.setdefault("plugins", {})
    dirs = plugins.setdefault("dirs", [])
    if str(destination) not in dirs:
        dirs.append(str(destination))
    if plugins.get("enabled") is False:
        raise SetupError("ZCode plugins are disabled in user config; enable them before setup")  # noqa: TRY003

    with tempfile.TemporaryDirectory(prefix="powercontext-zcode-source-") as temporary:
        plugin = _source_plugin(source, ref, Path(temporary) / "checkout")
        destination.parent.mkdir(parents=True, exist_ok=True)
        staged = Path(tempfile.mkdtemp(prefix=".powercontext-stage-", dir=destination.parent))
        backup = destination.with_name(".powercontext-backup")
        if backup.exists():
            shutil.rmtree(staged)
            raise SetupError(f"Cannot install ZCode plugin while {backup} exists")  # noqa: TRY003
        old_config = config_path.read_bytes() if config_path.exists() else None
        moved_old = False
        installed = False
        try:
            shutil.copytree(plugin, staged, dirs_exist_ok=True, ignore=shutil.ignore_patterns("tests", "node_modules"))
            preserve_installed_guidance(destination, staged)
            (staged / OWNER_MARKER).write_text("powercontext-zcode-v1\n", encoding="utf-8")
            _write_json(
                staged / "powercontext.json",
                {
                    "server_url": endpoint,
                    "allow_insecure_http": allow_insecure_http,
                    "capture_prompts": capture_prompts,
                    "boundary_flush": boundary_flush,
                },
            )
            mcp_path = staged / ".mcp.json"
            mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
            mcp["mcpServers"]["powercontext"]["url"] = endpoint + "/mcp"
            if use_authorization_env:
                mcp["mcpServers"]["powercontext"]["headers"] = {
                    "Authorization": "${POWERCONTEXT_ZCODE_AUTHORIZATION}",
                }
            _write_json(mcp_path, mcp)
            if destination.exists():
                destination.rename(backup)
                moved_old = True
            staged.rename(destination)
            installed = True
            _write_json(config_path, config)
        except (OSError, ValueError, KeyError) as error:
            if installed:
                shutil.rmtree(destination)
            if moved_old:
                backup.rename(destination)
            if old_config is None:
                config_path.unlink(missing_ok=True)
            else:
                config_path.write_bytes(old_config)
            raise SetupError("Could not install ZCode plugin; previous config and plugin were restored") from error  # noqa: TRY003
        finally:
            if staged.exists():
                shutil.rmtree(staged)
        if moved_old:
            shutil.rmtree(backup)
    return ZCodeSetupResult("powercontext", str(destination), str(config_path), endpoint)


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
            for name in ("settings.mjs", "transport.mjs", "scope.mjs", "observations.mjs", "context.mjs", "pending.mjs")
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
    if result.get("schema") != "powercontext.zcode.diagnostics.v1":
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
        diagnostics[name] = Diagnostic(status, message, checks=stages or None)
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
        return {"probe": Diagnostic(DiagnosticStatus.FAILED, "diagnostic probe unavailable or invalid")}

    return diagnostics


def run_zcode_diagnostics(*, runtime_data_dir: Path | None = None, prepare: bool = False) -> dict[str, Diagnostic]:
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
