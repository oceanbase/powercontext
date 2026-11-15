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
import shutil
import subprocess
import tempfile
from pathlib import Path
from shutil import which
from typing import Any, cast

from powercontext.cli.installation import publish
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import setup_transport_updates
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
_OPENCLAW_VERSION = re.compile(r"(?:OpenClaw\s+)?(\d+)\.(\d+)\.(\d+)(?:-beta\.(\d+))?")


def openclaw_executable() -> str:
    """Return a subprocess-launchable OpenClaw executable path."""

    candidates = ("openclaw.cmd", "openclaw") if os.name == "nt" else ("openclaw",)
    for candidate in candidates:
        executable = which(candidate)
        if executable is not None:
            return executable
    raise SetupError("OpenClaw CLI is not installed or is not on PATH.")  # noqa: TRY003


def pnpm_executable() -> str:
    """Return the package-manager executable used to build the plugin."""

    candidates = ("pnpm.cmd", "pnpm") if os.name == "nt" else ("pnpm",)
    for candidate in candidates:
        executable = which(candidate)
        if executable is not None:
            return executable
    raise SetupError("pnpm is not installed or is not on PATH; it is required to build the OpenClaw plugin.")  # noqa: TRY003


def require_supported_openclaw(executable: str) -> str:
    """Reject OpenClaw versions older than the plugin API contract."""

    completed = run_process([executable, "--version"], timeout=30, check=True)
    version_text = (completed.stdout or "").strip()
    match = _OPENCLAW_VERSION.search(version_text)
    if match is None:
        raise SetupError(f"OpenClaw {version_text or 'version unknown'} is unsupported; upgrade to >= 2026.8.1-beta.2")  # noqa: TRY003
    major = int(match.group(1))
    minor = int(match.group(2))
    patch = int(match.group(3))
    beta = int(match.group(4)) if match.group(4) is not None else 10**9
    actual = (major, minor, patch, beta)
    if actual < OPENCLAW_MIN_VERSION:
        raise SetupError(f"OpenClaw {version_text or 'version unknown'} is unsupported; upgrade to >= 2026.8.1-beta.2")  # noqa: TRY003
    help_text = run_process([executable, "plugins", "install", "--help"], timeout=30).stdout or ""
    if "--no-enable" not in help_text:
        raise SetupError("OpenClaw must support plugins install --no-enable; upgrade to a release such as 2026.9.7")  # noqa: TRY003
    return version_text


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
        raise SetupError(f"PowerContext OpenClaw plugin at {plugin_dir} is missing dist/index.js after build.")  # noqa: TRY003


def configure_openclaw(*, executable: str, server_url: str, allow_insecure_http: bool = False) -> None:
    """Configure the plugin connection without changing host policy or restarting it."""
    settings = [
        {"path": "plugins.entries.memory-powercontext.config.endpoint", "value": server_url},
        {"path": "plugins.entries.memory-powercontext.config.allowInsecureHttp", "value": allow_insecure_http},
    ]
    run_openclaw(executable, "config", "set", "--batch-json", json.dumps(settings, separators=(",", ":")))


def read_config_value(executable: str, path: str) -> object | None:
    """Read a config value, returning ``None`` when the path is absent."""

    command = [executable, "config", "get", path, "--json"]
    completed = run_process(command, timeout=60, check=False)
    try:
        value = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise SetupError(f"Cannot read OpenClaw configuration: {path}") from error  # noqa: TRY003
    if completed.returncode != 0:
        if (
            isinstance(value, dict)
            and value.get("ok") is False
            and value.get("error", {}).get("message", "").startswith(f"Config path is valid but unset: {path}.")
        ):
            return None
        raise SetupError(f"Cannot read OpenClaw configuration: {path}")  # noqa: TRY003
    return value


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
        raise SetupError(f"Cannot run {' '.join(command)}: {error}") from error  # noqa: TRY003
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or f"exit code {completed.returncode}"
        raise SetupError(f"`{' '.join(command)}` failed: {detail}")  # noqa: TRY003
    return completed


def openclaw_plugin_installed(output: str, *, active_memory_plugin: object | None = None) -> bool:
    """Return whether OpenClaw reports the PowerContext plugin as the active memory plugin."""

    command = ["openclaw", "plugins", "list", "--enabled", "--json"]
    try:
        payload = json.loads(output)
    except json.JSONDecodeError as error:
        raise SetupError(f"`{' '.join(command)}` returned invalid JSON") from error  # noqa: TRY003
    if not isinstance(payload, dict) or not isinstance(payload.get("plugins"), list):
        raise SetupError(f"`{' '.join(command)}` returned an invalid plugin list")  # noqa: TRY003

    for plugin in payload["plugins"]:
        if not isinstance(plugin, dict):
            raise SetupError(f"`{' '.join(command)}` returned an invalid plugin entry")  # noqa: TRY003
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
    "configure_openclaw",
    "openclaw_executable",
    "openclaw_plugin_installed",
    "run_openclaw",
]


class OpenClawInstaller:
    def install(self, source):
        executable = openclaw_executable()
        require_supported_openclaw(executable)
        with source.package("openclaw") as package:
            with tempfile.TemporaryDirectory(prefix="powercontext-openclaw-") as temporary:
                staging = Path(temporary)
                candidate = staging / "plugin"
                shutil.copytree(package, candidate, ignore=shutil.ignore_patterns("node_modules", ".git"))
                build_openclaw_plugin(candidate)
                # Archive installation delegates runtime dependencies and replacement to OpenClaw.
                run_process(
                    [pnpm_executable(), "--dir", str(candidate), "pack", "--pack-destination", str(staging)],
                    timeout=120,
                )
                (archive,) = staging.glob("*.tgz")
                run_openclaw(executable, "plugins", "install", "--force", "--no-enable", str(archive))
            try:
                record = json.loads(
                    run_openclaw(executable, "plugins", "inspect", OPENCLAW_PLUGIN_NAME, "--json").stdout
                )
                path = record["install"]["installPath"]
                if not isinstance(path, str) or not Path(path).is_dir():
                    raise ValueError  # noqa: TRY301
            except (ValueError, KeyError, TypeError) as error:
                raise SetupError("OpenClaw managed installation path could not be read back") from error  # noqa: TRY003
            return {
                "plugin": OPENCLAW_PLUGIN_NAME,
                "plugin_path": path,
                "server_url": "",
                "data_dir": str(powercontext_data_dir()),
            }

    def configure(self, connection):
        executable = openclaw_executable()
        run_openclaw(executable, "plugins", "inspect", OPENCLAW_PLUGIN_NAME, "--json")
        endpoint = connection.server_url
        publish(
            writes=setup_transport_updates(connection),
            action=lambda: configure_openclaw(
                executable=executable, server_url=endpoint, allow_insecure_http=connection.allow_insecure_http
            ),
        )
        return {"server_url": endpoint}

    def enable(self) -> None:  # noqa: C901 - initialize only absent native activation settings.
        executable = openclaw_executable()
        plugins = read_config_value(executable, "plugins")
        if plugins is None:
            plugins = {}
        if not isinstance(plugins, dict):
            raise SetupError("OpenClaw plugins configuration must be an object")  # noqa: TRY003
        plugins = cast(dict[str, Any], plugins)
        entry = plugins.get("entries", {}).get(OPENCLAW_PLUGIN_NAME, {})
        slot = plugins.get("slots", {}).get("memory")
        if (
            plugins.get("enabled") is False
            or entry.get("enabled") is False
            or OPENCLAW_PLUGIN_NAME in plugins.get("deny", [])
            or slot not in {None, OPENCLAW_PLUGIN_NAME}
        ):
            return
        settings = []
        defaults = {
            "plugins.slots.memory": (slot, OPENCLAW_PLUGIN_NAME),
            "plugins.entries.memory-powercontext.config.autoRecall": (entry.get("config", {}).get("autoRecall"), True),
            "plugins.entries.memory-powercontext.config.autoCapture": (
                entry.get("config", {}).get("autoCapture"),
                True,
            ),
            "plugins.entries.memory-powercontext.hooks.allowConversationAccess": (
                entry.get("hooks", {}).get("allowConversationAccess"),
                True,
            ),
        }
        for path, (current, default) in defaults.items():
            if current is None:
                settings.append({"path": path, "value": default})
        if slot is None:
            allowed = read_config_value(executable, "tools.alsoAllow")
            if allowed is None:
                allowed = []
            if not isinstance(allowed, list):
                raise SetupError("OpenClaw tools.alsoAllow must be an array")  # noqa: TRY003
            tools = allowed + [tool for tool in POWERCONTEXT_TOOLS if tool not in allowed]
            if tools != allowed:
                settings.append({"path": "tools.alsoAllow", "value": tools})
        activate = entry.get("enabled") is not True
        if settings:
            run_openclaw(executable, "config", "set", "--batch-json", json.dumps(settings, separators=(",", ":")))
        if activate:
            run_openclaw(executable, "plugins", "enable", OPENCLAW_PLUGIN_NAME)
        if activate or settings:
            run_openclaw(executable, "gateway", "restart")

    def verify(self) -> dict[str, Diagnostic]:
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
        from powercontext.cli.memory_protocol import installed_memory_protocol, read_plugin_memory_protocol

        try:
            record = json.loads(run_openclaw(executable, "plugins", "inspect", OPENCLAW_PLUGIN_NAME, "--json").stdout)
            protocol = read_plugin_memory_protocol(Path(record["install"]["installPath"]))
        except (SetupError, OSError, ValueError, KeyError, TypeError, AttributeError):
            protocol = None
        return {
            "plugin_memory_protocol": installed_memory_protocol(protocol, "openclaw"),
            "openclaw": Diagnostic(status=DiagnosticStatus.OK, detail=executable),
            "plugin": Diagnostic(
                status=DiagnosticStatus.OK if installed else DiagnosticStatus.FAILED,
                detail=(
                    f"{OPENCLAW_PLUGIN_NAME} is installed, enabled and selected in native configuration; "
                    "running Gateway loading is not observed"
                    if installed
                    else "PowerContext OpenClaw plugin is not enabled and selected as the memory plugin"
                ),
                checks={"running_host_configuration": "not_observed"},
            ),
        }
