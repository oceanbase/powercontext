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

"""Install and inspect the minimal Kimi Code memory plugin."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlsplit

from powercontext.cli.guidance import preserve_installed_guidance
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.client.transport_policy import normalize_client_url
from powercontext.transport import is_loopback_host

PLUGIN_RELATIVE = Path("integrations/kimi-code/plugins/powercontext")
OWNER_MARKER = ".powercontext-owned"
MEMORY_TOOLS = ["search_memory", "remember_memory", "get_artifact_revision"]
HOOK = {"event": "UserPromptSubmit", "command": "node ./hooks/user_prompt_submit.mjs", "timeout": 5}
REQUIRED_FILES = (
    "kimi.plugin.json",
    "hooks/user_prompt_submit.mjs",
    "shared/runtime.mjs",
    "shared/observations.mjs",
    "scripts/doctor.mjs",
    "scripts/scope.mjs",
    "scripts/status.mjs",
    "skills/powercontext-project-context/SKILL.md",
)


@dataclass(frozen=True, slots=True)
class KimiSetupResult:
    plugin_path: str
    config_file: str
    server_url: str
    scope_id: str | None


def kimi_home() -> Path:
    value = os.environ.get("KIMI_CODE_HOME", "")
    return Path(value if value.strip() else Path.home() / ".kimi-code").expanduser().resolve()


def kimi_executable() -> str | None:
    override = os.environ.get("KIMI_CLI_BIN")
    if override:
        path = Path(override).expanduser()
        return str(path) if path.is_file() else None
    return which("kimi.cmd" if os.name == "nt" else "kimi") or which("kimi")


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise SetupError(f"Invalid or unreadable JSON: {path}") from error  # noqa: TRY003
    if not isinstance(value, dict):
        raise SetupError(f"Expected a JSON object: {path}")  # noqa: TRY003
    return value


def _registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": 1, "plugins": []}
    value = _read_json(path)
    records = value.get("plugins")
    if (
        value.get("version") != 1
        or not isinstance(records, list)
        or any(not isinstance(item, dict) or not isinstance(item.get("id"), str) for item in records)
        or len({item["id"] for item in records}) != len(records)
    ):
        raise SetupError("Unsupported Kimi installed.json; repair it before setup")  # noqa: TRY003
    return value


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _endpoint(value: str) -> str:
    endpoint = normalize_client_url(value).removesuffix("/mcp").rstrip("/")
    parsed = urlsplit(endpoint)
    if (
        parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or parsed.scheme not in {"http", "https"}
        or (parsed.scheme == "http" and not is_loopback_host(parsed.hostname or ""))
    ):
        raise SetupError("Kimi Server URL must be a loopback HTTP or HTTPS origin")  # noqa: TRY003
    return endpoint


def install_kimi_plugin(  # noqa: C901 - validates and atomically replaces two user-owned destinations.
    *,
    source: Path,
    server_url: str | None = None,
    scope_id: str | None = None,
    capture_prompts: bool | None = None,
    workspace_scope: bool = False,
) -> KimiSetupResult:
    """Install a local plugin and preserve other records and existing enablement choices."""
    if kimi_executable() is None or which("node") is None:
        raise SetupError("Kimi CLI and Node must be available; KIMI_CLI_BIN may point to a built CLI")  # noqa: TRY003
    source = source.expanduser().resolve()
    plugin = source if (source / "kimi.plugin.json").is_file() else source / PLUGIN_RELATIVE
    if any(not (plugin / name).is_file() for name in REQUIRED_FILES):
        raise SetupError("Local source does not contain a complete PowerContext Kimi plugin")  # noqa: TRY003
    skill_template = (plugin / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
    if any(
        marker not in skill_template for marker in ("__POWERCONTEXT_SCOPE_JSON__", "__POWERCONTEXT_SCOPE_SCRIPT_JSON__")
    ):
        raise SetupError("Source must be an unconfigured plugin; use the original PowerContext checkout")  # noqa: TRY003
    manifest = _read_json(plugin / "kimi.plugin.json")
    if (
        manifest.get("name") != "powercontext"
        or manifest.get("hooks") != [HOOK]
        or manifest.get("skills") != "./skills/"
        or manifest.get("sessionStart") != {"skill": "powercontext-project-context"}
    ):
        raise SetupError("Invalid PowerContext Kimi plugin manifest")  # noqa: TRY003
    home = kimi_home()
    registry_path = home / "plugins/installed.json"
    destination = home / "plugins/managed/powercontext"
    if registry_path.is_symlink() or destination.is_symlink():
        raise SetupError("Kimi installation paths must not be symbolic links")  # noqa: TRY003
    registry = _registry(registry_path)
    previous_record = next((item for item in registry["plugins"] if item["id"] == "powercontext"), {})
    if previous_record and Path(previous_record.get("root", "")).resolve() != destination.resolve():
        raise SetupError("A PowerContext plugin is already registered at another path")  # noqa: TRY003
    if destination.exists() and not (destination / OWNER_MARKER).is_file():
        raise SetupError("Existing Kimi PowerContext directory is not owned by this installer")  # noqa: TRY003
    previous = _read_json(destination / "powercontext.json") if destination.exists() else {}
    endpoint = _endpoint(server_url if server_url is not None else previous.get("server_url", "http://127.0.0.1:8000"))
    if workspace_scope and scope_id is not None:
        raise SetupError("--workspace-scope and --scope-id are mutually exclusive")  # noqa: TRY003
    selected_scope = None if workspace_scope else scope_id if scope_id is not None else previous.get("scope_id")
    if (selected_scope is None and not workspace_scope and "scope_id" not in previous) or (
        selected_scope is not None
        and (not isinstance(selected_scope, str) or not selected_scope.strip() or len(selected_scope) > 256)
    ):
        raise SetupError("Specify --scope-id or --workspace-scope; setup never creates a Scope")  # noqa: TRY003
    token = os.environ.get("POWERCONTEXT_KIMI_TOKEN")
    if token is not None and (
        not token or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token) or len(token) > 8192
    ):
        raise SetupError("POWERCONTEXT_KIMI_TOKEN must contain one raw token")  # noqa: TRY003
    use_token = token is not None or (endpoint == previous.get("server_url") and previous.get("use_token") is True)
    capture = capture_prompts if capture_prompts is not None else previous.get("capture_prompts", True)
    if not isinstance(capture, bool):
        raise SetupError("Invalid saved capture preference")  # noqa: TRY003
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = destination.with_name(".powercontext-kimi-backup")
    if backup.exists():
        raise SetupError(f"Restore the retained Kimi plugin backup at {backup} before setup")  # noqa: TRY003
    original_registry = registry_path.read_bytes() if registry_path.exists() else None
    with tempfile.TemporaryDirectory(prefix=".powercontext-kimi-", dir=destination.parent) as temporary:
        staging = Path(temporary) / "plugin"
        shutil.copytree(plugin, staging, ignore=shutil.ignore_patterns("tests", "node_modules"))
        preserve_installed_guidance(destination, staging)
        (staging / OWNER_MARKER).write_text("powercontext-kimi-code-v1\n", encoding="utf-8")
        _write_json(
            staging / "powercontext.json",
            {
                "server_url": endpoint,
                "scope_id": selected_scope,
                "capture_prompts": capture,
                "use_token": use_token,
            },
        )
        manifest["mcpServers"] = {
            "powercontext": {
                "url": endpoint + "/mcp",
                **({"bearerTokenEnvVar": "POWERCONTEXT_KIMI_TOKEN"} if use_token else {}),
                "enabledTools": MEMORY_TOOLS,
                "startupTimeoutMs": 10000,
                "toolTimeoutMs": 10000,
            }
        }
        skill = staging / "skills/powercontext-project-context/SKILL.md"
        skill.write_text(
            skill
            .read_text(encoding="utf-8")
            .replace("__POWERCONTEXT_SCOPE_JSON__", json.dumps(selected_scope, ensure_ascii=False))
            .replace(
                "__POWERCONTEXT_SCOPE_SCRIPT_JSON__",
                json.dumps(str(destination / "scripts/scope.mjs"), ensure_ascii=False),
            ),
            encoding="utf-8",
        )
        _write_json(staging / "kimi.plugin.json", manifest)
        now = datetime.now(UTC).isoformat()
        record = {
            **previous_record,
            "id": "powercontext",
            "root": str(destination),
            "source": "local-path",
            "enabled": previous_record.get("enabled", True),
            "installedAt": previous_record.get("installedAt", now),
            "updatedAt": now,
            "originalSource": str(plugin),
        }
        registry["plugins"] = [item for item in registry["plugins"] if item["id"] != "powercontext"] + [record]
        moved = False
        installed = False
        try:
            if destination.exists():
                destination.rename(backup)
                moved = True
            staging.rename(destination)
            installed = True
            _write_json(registry_path, registry)
        except BaseException:
            try:
                if installed:
                    shutil.rmtree(destination)
                if moved:
                    backup.rename(destination)
                if original_registry is None:
                    registry_path.unlink(missing_ok=True)
                else:
                    registry_path.write_bytes(original_registry)
            except OSError as error:
                message = f"Kimi setup rollback failed; previous plugin backup is retained at {backup}"
                raise SetupError(message) from error
            raise
    if moved:
        shutil.rmtree(backup)
    return KimiSetupResult(str(destination), str(registry_path), endpoint, selected_scope)


def run_kimi_diagnostics(
    *, runtime: bool = False, cwd: Path | None = None, session_id: str | None = None
) -> dict[str, Diagnostic]:
    """Check installed declarations and perform readonly Server and Scope probes."""
    ok, failed, skipped = DiagnosticStatus.OK, DiagnosticStatus.FAILED, DiagnosticStatus.SKIPPED
    node = which("node")
    checks = {
        "kimi_code": Diagnostic(
            ok if kimi_executable() else failed, "available" if kimi_executable() else "Kimi not found"
        ),
        "node": Diagnostic(ok if node else failed, "available" if node else "Node not found"),
    }
    destination = kimi_home() / "plugins/managed/powercontext"
    try:
        records = _registry(kimi_home() / "plugins/installed.json")["plugins"]
        record = next((item for item in records if item["id"] == "powercontext"), None)
        registered = record is not None and Path(record.get("root", "")).resolve() == destination.resolve()
        enabled = registered and record.get("enabled") is True
        checks["plugin"] = Diagnostic(
            ok if enabled else failed,
            "registered and enabled" if enabled else "disabled" if registered else "not installed",
        )
        settings = _read_json(destination / "powercontext.json")
        manifest = _read_json(destination / "kimi.plugin.json")
        server = manifest["mcpServers"]["powercontext"]
        capabilities = (record or {}).get("capabilities", {}).get("mcpServers", {}).get("powercontext", {})
        configured = (
            server.get("url") == _endpoint(settings["server_url"]) + "/mcp"
            and server.get("bearerTokenEnvVar") == ("POWERCONTEXT_KIMI_TOKEN" if settings.get("use_token") else None)
            and not server.get("headers")
            and server.get("enabledTools") == MEMORY_TOOLS
            and server.get("enabled", True) is True
            and capabilities.get("enabled", True) is True
        )
        checks["mcp"] = Diagnostic(
            ok if configured else failed,
            "configured (not a live MCP check)" if configured else "disabled or configuration mismatch",
        )
        hooks_ok = manifest.get("hooks") == [HOOK] and all((destination / name).is_file() for name in REQUIRED_FILES)
        skill_ok = manifest.get("skills") == "./skills/" and manifest.get("sessionStart") == {
            "skill": "powercontext-project-context"
        }
        checks["skill"] = Diagnostic(
            ok if skill_ok else failed, "configured" if skill_ok else "Skill is not registered"
        )
        checks["hook"] = Diagnostic(
            ok if hooks_ok else failed,
            "configured (injection not verified)" if hooks_ok else "missing or mismatched Hook files",
        )
    except (SetupError, KeyError, TypeError, AttributeError, ValueError):
        checks.setdefault("plugin", Diagnostic(failed, "invalid installation"))
        checks["configuration"] = Diagnostic(failed, "Missing or invalid installed plugin configuration")
        return checks
    if node is None:
        checks["server"] = Diagnostic(skipped, "Node is required for readonly probes")
        return checks
    try:
        arguments = [node, str(destination / "scripts/doctor.mjs"), "--cwd", str((cwd or Path.cwd()).resolve())]
        if runtime:
            arguments.append("--runtime")
            if session_id is not None:
                arguments.extend(["--session-id", session_id])
        result = subprocess.run(  # noqa: S603 - fixed Node script and no shell.
            arguments,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=6,
            check=False,
        )
        report = json.loads(result.stdout)
        if result.returncode or report.get("schema") != "powercontext.kimi-code.diagnostics.v1":
            raise ValueError  # noqa: TRY301 - invalid external probe output joins the failure path.
        for name, value in report["checks"].items():
            checks[name] = Diagnostic(DiagnosticStatus(value["status"]), value["detail"], value.get("checks"))
        if runtime:
            checks["runtime"] = _runtime_diagnostic(report["runtime"])
    except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
        checks["server"] = Diagnostic(failed, "Readonly probes failed; run powercontext doctor kimi-code after repair")
    return checks


def _runtime_diagnostic(history: dict[str, Any]) -> Diagnostic:
    """Project local history separately from the current Server and Scope probes."""
    ok, failed, skipped = DiagnosticStatus.OK, DiagnosticStatus.FAILED, DiagnosticStatus.SKIPPED
    state = history["status"]
    status = (
        failed if state == "failed" else ok if state == "observed" and history["freshness"] == "recent" else skipped
    )
    details = {"selection": history["selection"]} if "selection" in history else {}
    details.update({
        name: ": ".join(part for part in (value["state"], value.get("code") or value.get("reason")) if part)
        for name, value in history.get("stages", {}).items()
    })
    if "freshness" in history:
        details["freshness"] = history["freshness"]
        if history.get("scope_id") is not None:
            details["scope_id"] = history["scope_id"]
        details["started_at"] = datetime.fromtimestamp(history["started_at"] / 1000, tz=UTC).isoformat()
    return Diagnostic(status, history.get("code", state), details)
