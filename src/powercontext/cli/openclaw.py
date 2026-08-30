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

"""Install and configure the OpenClaw PowerContext memory plugin."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from shutil import rmtree, which
from urllib.parse import urlsplit, urlunsplit

from powercontext.cli.system import Diagnostic, DiagnosticStatus, OpenClawSetupResult, SetupError
from powercontext.paths import powercontext_data_dir

OPENCLAW_PLUGIN_RELATIVE = Path("integrations") / "openclaw" / "plugins" / "memory-powercontext"
OPENCLAW_PLUGIN_NAME = "memory-powercontext"
OPENCLAW_PACKAGE_NAME = "@oceanbase/openclaw-memory-powercontext"
OPENCLAW_CHECKOUT_ROOT = "openclaw"
OPENCLAW_MIN_VERSION = (2026, 8, 1, 2)
POWERCONTEXT_TOOLS = (
    "powercontext_memory_search",
    "powercontext_memory_get",
    "powercontext_memory_store",
    "powercontext_memory_revise",
    "powercontext_memory_retire",
)
_GITHUB_REPOSITORY = re.compile(r"^[^/\s]+/[^/\s]+$")
_OPENCLAW_VERSION = re.compile(r"(?:OpenClaw\s+)?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?")


def install_openclaw_plugin(
    *,
    source: str,
    ref: str,
    server_url: str,
    scope_mode: str,
) -> OpenClawSetupResult:
    """Build, install, and configure the OpenClaw plugin from one source/ref."""

    executable = openclaw_executable()
    require_supported_openclaw(executable)
    normalized_url = normalize_server_url(server_url)
    if scope_mode not in {"agent", "project"}:
        raise SetupError.invalid_openclaw_scope()

    data_dir = powercontext_data_dir()
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise SetupError.data_directory(data_dir, error) from error

    plugin_dir = resolve_openclaw_plugin_dir(source=source, ref=ref)
    build_openclaw_plugin(plugin_dir)
    run_openclaw(executable, "plugins", "install", "--link", "--force", str(plugin_dir))
    configure_openclaw(
        executable=executable,
        server_url=normalized_url,
        scope_mode=scope_mode,
    )
    return OpenClawSetupResult(
        plugin=OPENCLAW_PLUGIN_NAME,
        plugin_path=str(plugin_dir),
        server_url=normalized_url,
        scope_mode=scope_mode,
        data_dir=str(data_dir),
    )


def openclaw_executable() -> str:
    """Return a subprocess-launchable OpenClaw executable path."""

    candidates = ("openclaw.cmd", "openclaw") if os.name == "nt" else ("openclaw",)
    for candidate in candidates:
        executable = which(candidate)
        if executable is not None:
            return executable
    raise SetupError.openclaw_unavailable()


def pnpm_executable() -> str:
    """Return the package-manager executable used to build the plugin."""

    candidates = ("pnpm.cmd", "pnpm") if os.name == "nt" else ("pnpm",)
    for candidate in candidates:
        executable = which(candidate)
        if executable is not None:
            return executable
    raise SetupError.pnpm_unavailable()


def require_supported_openclaw(executable: str) -> str:
    """Reject OpenClaw versions older than the plugin API contract."""

    completed = run_process([executable, "--version"], timeout=30, check=True)
    version_text = (completed.stdout or "").strip()
    match = _OPENCLAW_VERSION.search(version_text)
    if match is None:
        raise SetupError.unsupported_openclaw_version(version_text)
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3))
    beta = int(match.group(4)) if match.group(4) is not None else 10**9
    actual = (major, minor, patch, beta)
    if actual < OPENCLAW_MIN_VERSION:
        raise SetupError.unsupported_openclaw_version(version_text)
    return version_text


def resolve_openclaw_plugin_dir(*, source: str, ref: str) -> Path:
    """Resolve a local checkout or materialize a remote Git ref."""

    if is_local_source(source):
        return plugin_dir_from_checkout(Path(source).expanduser().resolve())
    return plugin_dir_from_checkout(materialize_remote_checkout(source, ref))


def plugin_dir_from_checkout(root: Path) -> Path:
    """Accept either the plugin directory or a PowerContext repository root."""

    if is_openclaw_plugin(root):
        return root
    plugin = root / OPENCLAW_PLUGIN_RELATIVE
    if is_openclaw_plugin(plugin):
        return plugin
    raise SetupError.missing_openclaw_plugin(root)


def is_local_source(source: str) -> bool:
    """Return whether a source names a local checkout."""

    candidate = Path(source).expanduser()
    return source.startswith((".", "/", "~")) or (len(source) >= 2 and source[1] == ":") or candidate.exists()


def is_openclaw_plugin(path: Path) -> bool:
    """Return whether a directory contains the PowerContext OpenClaw plugin."""

    package_file = path / "package.json"
    if not package_file.is_file():
        return False
    try:
        package = json.loads(package_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(package, dict) and package.get("name") == OPENCLAW_PACKAGE_NAME


def materialize_remote_checkout(source: str, ref: str) -> Path:
    """Clone a remote source into the user-owned PowerContext checkout cache."""

    target = checkout_target(ref)
    if is_openclaw_plugin(target) or is_openclaw_plugin(target / OPENCLAW_PLUGIN_RELATIVE):
        return target
    if target.exists():
        rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    clone_github_source(source, ref, target)
    return target


def checkout_target(ref: str) -> Path:
    """Resolve a ref below the OpenClaw checkout cache without path traversal."""

    if not ref or ref in {".", ".."} or "\x00" in ref:
        raise SetupError.invalid_openclaw_ref(ref)
    root = (powercontext_data_dir() / "checkouts" / OPENCLAW_CHECKOUT_ROOT).resolve()
    target = (root / ref).resolve()
    try:
        target.relative_to(root)
    except ValueError as error:
        raise SetupError.invalid_openclaw_ref(ref) from error
    if target == root:
        raise SetupError.invalid_openclaw_ref(ref)
    return target


def clone_github_source(source: str, ref: str, target: Path) -> None:
    """Clone a GitHub slug or URL at a specific ref."""

    command = ["git", "clone", "--depth", "1", "--branch", ref, github_clone_url(source), str(target)]
    run_process(command, timeout=120)


def github_clone_url(source: str) -> str:
    """Accept a GitHub slug or repository URL and return a clone URL."""

    text = source.strip()
    if text.startswith(("https://github.com/", "http://github.com/", "git@github.com:")):
        return text if text.endswith(".git") else f"{text}.git"
    if "://" in text or text.startswith("git@"):
        raise SetupError.invalid_openclaw_source(source)
    if _GITHUB_REPOSITORY.fullmatch(text):
        return f"https://github.com/{text}.git"
    raise SetupError.invalid_openclaw_source(source)


def build_openclaw_plugin(plugin_dir: Path) -> None:
    """Install plugin dependencies and produce the runtime bundle."""

    executable = pnpm_executable()
    environment = os.environ.copy()
    environment["CI"] = "true"
    run_process(
        [executable, "--dir", str(plugin_dir), "install", "--frozen-lockfile"],
        timeout=600,
        env=environment,
    )
    run_process([executable, "--dir", str(plugin_dir), "run", "build"], timeout=600, env=environment)
    if not (plugin_dir / "dist" / "index.js").is_file():
        raise SetupError.unbuilt_openclaw_plugin(plugin_dir)


def normalize_server_url(value: str) -> str:
    """Normalize the configured HTTP endpoint without accepting embedded secrets."""

    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise SetupError.openclaw_server_url_scheme()
    if parsed.username is not None or parsed.password is not None:
        raise SetupError.openclaw_server_url_credentials()
    if parsed.query or parsed.fragment:
        raise SetupError.openclaw_server_url_suffix()
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def configure_openclaw(*, executable: str, server_url: str, scope_mode: str) -> None:
    """Write plugin, memory-slot, and coding-tool allowlist configuration."""

    settings = [
        {"path": "plugins.entries.memory-powercontext.enabled", "value": True},
        {"path": "plugins.entries.memory-powercontext.config.endpoint", "value": server_url},
        {"path": "plugins.entries.memory-powercontext.config.autoRecall", "value": True},
        {"path": "plugins.entries.memory-powercontext.config.autoCapture", "value": True},
        {"path": "plugins.entries.memory-powercontext.config.scopeMode", "value": scope_mode},
        {"path": "plugins.entries.memory-powercontext.hooks.allowConversationAccess", "value": True},
        {"path": "plugins.slots.memory", "value": OPENCLAW_PLUGIN_NAME},
    ]
    if read_config_value(executable, "gateway.mode") is None:
        settings.insert(0, {"path": "gateway.mode", "value": "local"})
    run_openclaw(executable, "config", "set", "--batch-json", json.dumps(settings, separators=(",", ":")))
    current = read_tools_allowlist(executable)
    merged = list(current)
    for tool in POWERCONTEXT_TOOLS:
        if tool not in merged:
            merged.append(tool)
    run_openclaw(
        executable,
        "config",
        "set",
        "tools.alsoAllow",
        json.dumps(merged, separators=(",", ":")),
        "--strict-json",
    )
    run_openclaw(executable, "gateway", "restart")


def read_tools_allowlist(executable: str) -> list[object]:
    """Read the existing allowlist, treating an absent path as an empty list."""

    command = [executable, "config", "get", "tools.alsoAllow", "--json"]
    completed = run_process(command, timeout=60, check=False)
    if completed.returncode != 0:
        return []
    try:
        value = json.loads(completed.stdout or "[]")
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command, "invalid JSON") from error
    if not isinstance(value, list):
        raise SetupError.invalid_command_output(command, "tools.alsoAllow is not an array")
    return value


def read_config_value(executable: str, path: str) -> object | None:
    """Read a config value, returning ``None`` when the path is absent."""

    command = [executable, "config", "get", path, "--json"]
    completed = run_process(command, timeout=60, check=False)
    if completed.returncode != 0:
        return None
    try:
        return json.loads(completed.stdout or "null")
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command, "invalid JSON") from error


def run_openclaw(executable: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    """Run an OpenClaw command and convert failures to SetupError."""

    return run_process([executable, *arguments], timeout=180)


def run_process(
    command: list[str],
    *,
    timeout: int,
    check: bool = True,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a setup subprocess with captured, bounded output."""

    try:
        completed = subprocess.run(  # noqa: S603 - setup arguments are passed directly to fixed executables.
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise SetupError.command_unavailable(command, error) from error
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError.command_failed(command, detail)
    return completed


def run_openclaw_diagnostics() -> dict[str, Diagnostic]:
    """Collect diagnostics for the optional OpenClaw integration."""

    try:
        executable = openclaw_executable()
    except SetupError:
        return {
            "openclaw": Diagnostic(
                status=DiagnosticStatus.FAILED,
                detail="OpenClaw CLI is not installed or is not on PATH",
            ),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="not checked because OpenClaw CLI is unavailable",
            ),
        }
    try:
        output = run_openclaw(executable, "plugins", "list", "--enabled", "--json").stdout or ""
        active_memory_plugin = read_config_value(executable, "plugins.slots.memory")
        installed = openclaw_plugin_installed(output, active_memory_plugin=active_memory_plugin)
    except SetupError as error:
        return {
            "openclaw": Diagnostic(status=DiagnosticStatus.FAILED, detail=str(error)),
            "plugin": Diagnostic(
                status=DiagnosticStatus.SKIPPED,
                detail="plugin list is unavailable",
            ),
        }
    return {
        "openclaw": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
        "plugin": Diagnostic(
            status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
            detail=(
                f"{OPENCLAW_PLUGIN_NAME} is installed and active"
                if installed
                else "PowerContext OpenClaw plugin is not enabled, loaded, and selected as the memory plugin"
            ),
        ),
    }


def openclaw_plugin_installed(output: str, *, active_memory_plugin: object | None = None) -> bool:
    """Return whether OpenClaw reports the PowerContext plugin as the active memory plugin."""

    command = ["openclaw", "plugins", "list", "--enabled", "--json"]
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise SetupError.invalid_command_output(command, "invalid JSON") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
        raise SetupError.invalid_command_output(command, "an invalid plugin list")

    for plugin in payload["plugins"]:
        if not isinstance(plugin, dict):
            raise SetupError.invalid_command_output(command, "an invalid plugin entry")
        if plugin.get("id") != OPENCLAW_PLUGIN_NAME:
            continue
        memory_slot_selected = plugin.get("memorySlotSelected")
        if memory_slot_selected is None:
            memory_slot_selected = active_memory_plugin == OPENCLAW_PLUGIN_NAME
        return plugin.get("enabled") is True and plugin.get("status") == "loaded" and memory_slot_selected is True
    return False


__all__ = [
    "OPENCLAW_PACKAGE_NAME",
    "OPENCLAW_PLUGIN_NAME",
    "POWERCONTEXT_TOOLS",
    "build_openclaw_plugin",
    "checkout_target",
    "configure_openclaw",
    "github_clone_url",
    "install_openclaw_plugin",
    "is_openclaw_plugin",
    "materialize_remote_checkout",
    "normalize_server_url",
    "openclaw_executable",
    "openclaw_plugin_installed",
    "plugin_dir_from_checkout",
    "read_tools_allowlist",
    "resolve_openclaw_plugin_dir",
    "run_openclaw",
    "run_openclaw_diagnostics",
]
