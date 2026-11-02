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

"""Install and inspect Kimi Code memory and workflow profiles."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from functools import cached_property
from pathlib import Path
from shutil import which
from typing import Any
from urllib.parse import urlsplit

from powercontext.cli.guidance import guidance_region, preserve_installed_guidance
from powercontext.cli.installation import publish, write_json
from powercontext.cli.system import Diagnostic, DiagnosticStatus, SetupError
from powercontext.cli.transport import setup_transport_updates
from powercontext.client.transport_policy import normalize_client_url
from powercontext.transport import is_loopback_host

PLUGIN_RELATIVE = Path("integrations/kimi-code/plugins/powercontext")
OWNER_MARKER = ".powercontext-owned"


class KimiProfile(StrEnum):
    MINIMAL = "minimal"
    RECOMMENDED = "recommended"
    FULL = "full"


HOOK = {"event": "UserPromptSubmit", "command": "node ./hooks/user_prompt_submit.mjs", "timeout": 5}
REQUIRED_FILES = (
    "kimi.plugin.json",
    "profiles.json",
    "shared/mcp.mjs",
    "hooks/user_prompt_submit.mjs",
    "shared/runtime.mjs",
    "shared/observations.mjs",
    "scripts/doctor.mjs",
    "scripts/scope.mjs",
    "scripts/status.mjs",
    "skills/powercontext-project-context/SKILL.md",
)


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


def _configured_skill(
    skill: Path,
    *,
    scope: str | None,
    previous_scope: str | None,
    scope_script: Path,
    profile: KimiProfile,
    previous_profile: KimiProfile,
) -> bytes:
    existing = skill.read_bytes().decode("utf-8")
    region = guidance_region(existing)
    if region is None:
        raise SetupError("Add managed guidance markers to the installed Skill before reconfiguring")  # noqa: TRY003
    start, end = region
    content = existing[start:end]
    old_scope = (
        "__POWERCONTEXT_SCOPE_JSON__"
        if "__POWERCONTEXT_SCOPE_JSON__" in content
        else json.dumps(previous_scope, ensure_ascii=False)
    )
    for label in ("The configured Scope is", "The configured explicit Scope is"):
        content = content.replace(f"{label} {old_scope}.", f"{label} {json.dumps(scope, ensure_ascii=False)}.")
    content = content.replace("__POWERCONTEXT_SCOPE_SCRIPT_JSON__", json.dumps(str(scope_script), ensure_ascii=False))
    old_profile = (
        "__POWERCONTEXT_PROFILE_JSON__"
        if "__POWERCONTEXT_PROFILE_JSON__" in content
        else json.dumps(previous_profile.value)
    )
    content = content.replace(
        f"The enabled MCP profile is {old_profile}.", f"The enabled MCP profile is {json.dumps(profile.value)}."
    )
    workflow_start = "<!-- POWERCONTEXT-WORKFLOWS:START -->"
    workflow_end = "<!-- POWERCONTEXT-WORKFLOWS:END -->"
    if workflow_start not in content and workflow_end not in content and profile is KimiProfile.MINIMAL:
        return (existing[:start] + content + existing[end:]).encode("utf-8")
    if (
        content.count(workflow_start) != 1
        or content.count(workflow_end) != 1
        or content.index(workflow_start) > content.index(workflow_end)
    ):
        raise SetupError("Run powercontext setup kimi-code to install profile-aware guidance")  # noqa: TRY003
    before, remaining = content.split(workflow_start)
    _, after = remaining.split(workflow_end)
    content = before + workflow_start + "\n" + _profile_guidance(profile) + "\n" + workflow_end + after
    return (existing[:start] + content + existing[end:]).encode("utf-8")


def _profile_tools(plugin: Path, profile: KimiProfile) -> list[str]:
    value = _read_json(plugin / "profiles.json")
    try:
        tools = value["profiles"][profile]
    except (KeyError, TypeError) as error:
        raise SetupError("Missing Kimi MCP tool profile") from error  # noqa: TRY003
    if (
        value.get("schema") != "powercontext.kimi-code.profiles.v1"
        or not isinstance(tools, list)
        or not tools
        or any(not isinstance(tool, str) or not tool for tool in tools)
        or len(set(tools)) != len(tools)
    ):
        raise SetupError("Invalid Kimi MCP tool profile")  # noqa: TRY003
    return tools


def _profile_guidance(profile: KimiProfile) -> str:
    if profile is KimiProfile.MINIMAL:
        return ""
    result = (
        "For Work Contract, Handoff, continuation, acknowledgment and Task Outcome, "
        "read [Work Handoff](references/work-handoff.md) before the first operation. "
        "An ordinary transfer is temporary; commit only an explicitly requested durable milestone."
    )
    if profile is KimiProfile.FULL:
        result += (
            "\nFor Experience, managed/external Skill and candidate workflows, "
            "read [Experience and Skill](references/experience-skill.md) and "
            "[Review and publication](references/review-publication.md). "
            "Inspection grants no decision authority."
        )
    return result


@dataclass
class KimiInstaller:
    scope_id: str | None = None
    capture_prompts: bool | None = None
    workspace_scope: bool = False
    profile: KimiProfile | str | None = None

    @cached_property
    def _preferences(self):
        path = kimi_home() / "plugins/managed/powercontext/powercontext.json"
        previous = _read_json(path) if path.exists() else {}
        profile = KimiProfile(self.profile if self.profile is not None else previous.get("profile", "minimal"))
        if self.workspace_scope and self.scope_id is not None:
            raise SetupError("--workspace-scope and --scope-id are mutually exclusive")  # noqa: TRY003
        scope = (
            None if self.workspace_scope else self.scope_id if self.scope_id is not None else previous.get("scope_id")
        )
        if (scope is None and not self.workspace_scope and "scope_id" not in previous) or (
            scope is not None and (not isinstance(scope, str) or not scope.strip() or len(scope) > 256)
        ):
            raise SetupError("Specify --scope-id or --workspace-scope; setup never creates a Scope")  # noqa: TRY003
        capture = self.capture_prompts if self.capture_prompts is not None else previous.get("capture_prompts", True)
        if not isinstance(capture, bool):
            raise SetupError("Invalid saved capture preference")  # noqa: TRY003
        token = os.environ.get("POWERCONTEXT_KIMI_TOKEN")
        if token is not None and (
            not token or not token.isascii() or any(ord(c) < 33 or ord(c) > 126 for c in token) or len(token) > 8192
        ):
            raise SetupError("POWERCONTEXT_KIMI_TOKEN must contain one raw token")  # noqa: TRY003
        return previous, scope, capture, token is not None, profile

    def install(self, source):  # noqa: C901
        profile = self._preferences[-1]
        if kimi_executable() is None or which("node") is None:
            raise SetupError("Kimi CLI and Node must be available; KIMI_CLI_BIN may point to a built CLI")  # noqa: TRY003
        with source.package("kimi-code") as plugin:
            skill_template = (plugin / "skills/powercontext-project-context/SKILL.md").read_text(encoding="utf-8")
            if any(
                marker not in skill_template
                for marker in ("__POWERCONTEXT_SCOPE_JSON__", "__POWERCONTEXT_SCOPE_SCRIPT_JSON__")
            ):
                raise SetupError("Source must be an unconfigured plugin; use the original PowerContext checkout")  # noqa: TRY003
            if (plugin / OWNER_MARKER).exists():
                raise SetupError("Use the original PowerContext checkout or an unconfigured release package")  # noqa: TRY003
            if any(not (plugin / name).is_file() for name in REQUIRED_FILES):
                raise SetupError("Local source does not contain a complete PowerContext Kimi plugin")  # noqa: TRY003
            manifest = _read_json(plugin / "kimi.plugin.json")
            if (
                manifest.get("name") != "powercontext"
                or manifest.get("hooks") != [HOOK]
                or manifest.get("skills") != "./skills/"
                or manifest.get("sessionStart") != {"skill": "powercontext-project-context"}
            ):
                raise SetupError("Invalid PowerContext Kimi plugin manifest")  # noqa: TRY003
            _profile_tools(plugin, profile)
            home = kimi_home()
            registry_path, destination = home / "plugins/installed.json", home / "plugins/managed/powercontext"
            if registry_path.is_symlink() or destination.is_symlink():
                raise SetupError("Kimi installation paths must not be symbolic links")  # noqa: TRY003
            registry = _registry(registry_path)
            previous_record = next((item for item in registry["plugins"] if item["id"] == "powercontext"), {})
            if previous_record and Path(previous_record.get("root", "")).resolve() != destination.resolve():
                raise SetupError("A PowerContext plugin is already registered at another path")  # noqa: TRY003
            if destination.exists() and not (destination / OWNER_MARKER).is_file():
                raise SetupError("Existing Kimi PowerContext directory is not owned by this installer")  # noqa: TRY003
            previous = (
                _read_json(destination / "powercontext.json") if (destination / "powercontext.json").exists() else {}
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=".powercontext-", dir=destination.parent) as temporary:
                staged = Path(temporary) / "plugin"
                shutil.copytree(plugin, staged, ignore=shutil.ignore_patterns("tests", "node_modules"))
                preserve_installed_guidance(destination, staged)
                if previous:
                    shutil.copy2(destination / "powercontext.json", staged / "powercontext.json")
                    native = _read_json(destination / "kimi.plugin.json")
                    if "mcpServers" in native:
                        manifest["mcpServers"] = native["mcpServers"]
                (staged / OWNER_MARKER).write_text("powercontext-kimi-code-v1\n", encoding="utf-8")
                if previous:
                    skill = staged / "skills/powercontext-project-context/SKILL.md"
                    skill.write_bytes(
                        _configured_skill(
                            skill,
                            scope=previous["scope_id"],
                            previous_scope=previous["scope_id"],
                            scope_script=destination / "scripts/scope.mjs",
                            profile=KimiProfile(previous.get("profile", "minimal")),
                            previous_profile=KimiProfile(previous.get("profile", "minimal")),
                        )
                    )
                write_json(staged / "kimi.plugin.json", manifest)
                now = datetime.now(UTC).isoformat()
                record = {
                    **previous_record,
                    "id": "powercontext",
                    "root": str(destination),
                    "source": "local-path",
                    "enabled": previous_record.get("enabled", True),
                    "installedAt": previous_record.get("installedAt", now),
                    "updatedAt": now,
                    "originalSource": source.location,
                }
                registry["plugins"] = [item for item in registry["plugins"] if item["id"] != "powercontext"] + [record]
                publish(
                    [(staged, destination)],
                    {registry_path: (json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode()},
                )
            return {
                "plugin_path": str(destination),
                "config_file": str(registry_path),
                "server_url": previous.get("server_url", "http://127.0.0.1:8000"),
                "scope_id": previous.get("scope_id"),
                "profile": previous.get("profile", "minimal"),
            }

    def configure(self, connection):
        destination = kimi_home() / "plugins/managed/powercontext"
        previous, scope, capture, supplied_token, profile = self._preferences
        endpoint = connection.server_url
        use_token = supplied_token or (endpoint == previous.get("server_url") and previous.get("use_token") is True)
        settings = {
            "server_url": endpoint,
            "scope_id": scope,
            "capture_prompts": capture,
            "use_token": use_token,
            "profile": profile.value,
        }
        manifest = _read_json(destination / "kimi.plugin.json")
        server = manifest["mcpServers"]["powercontext"]
        server["url"] = endpoint + "/mcp"
        if self.profile is not None or not previous:
            server["enabledTools"] = _profile_tools(destination, profile)
        if use_token:
            server["bearerTokenEnvVar"] = "POWERCONTEXT_KIMI_TOKEN"
        elif server.get("bearerTokenEnvVar") == "POWERCONTEXT_KIMI_TOKEN":
            del server["bearerTokenEnvVar"]
        skill = destination / "skills/powercontext-project-context/SKILL.md"
        content = _configured_skill(
            skill,
            scope=scope,
            previous_scope=previous.get("scope_id"),
            scope_script=destination / "scripts/scope.mjs",
            profile=profile,
            previous_profile=KimiProfile(previous.get("profile", "minimal")),
        )
        publish(
            writes={
                **setup_transport_updates(connection),
                destination / "powercontext.json": (json.dumps(settings, ensure_ascii=False, indent=2) + "\n").encode(),
                destination / "kimi.plugin.json": (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode(),
                skill: content,
            }
        )
        return {"server_url": endpoint, "scope_id": scope, "profile": profile.value}

    def verify(  # noqa: C901 - installed declarations plus opt-in runtime and transport probes.
        self, *, runtime: bool = False, cwd: Path | None = None, session_id: str | None = None, mcp: bool = False
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
            profile = KimiProfile(settings.get("profile", "minimal"))
            tools = _profile_tools(destination, profile)
            checks["profile"] = Diagnostic(ok, profile.value, {"enabled_tools": str(len(tools))})
            capabilities = (record or {}).get("capabilities", {}).get("mcpServers", {}).get("powercontext", {})
            configured = (
                server.get("url") == _endpoint(settings["server_url"]) + "/mcp"
                and server.get("bearerTokenEnvVar")
                == ("POWERCONTEXT_KIMI_TOKEN" if settings.get("use_token") else None)
                and not server.get("headers")
                and server.get("enabledTools") == tools
                and server.get("enabled", True) is True
                and capabilities.get("enabled", True) is True
            )
            checks["mcp"] = Diagnostic(
                ok if configured else failed,
                "configured (not a live MCP check)" if configured else "disabled or configuration mismatch",
            )
            hooks_ok = manifest.get("hooks") == [HOOK] and all(
                (destination / name).is_file() for name in REQUIRED_FILES
            )
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
            arguments = [node, str(destination / "scripts/doctor.mjs"), "--cwd", str((cwd or Path.cwd()).absolute())]
            if mcp and enabled and configured:
                arguments.append("--mcp")
            elif mcp:
                checks["mcp_discovery"] = Diagnostic(skipped, "Plugin/MCP configuration is disabled or invalid")
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
            if result.returncode or report.get("schema") != "powercontext.kimi-code.diagnostics.v2":
                checks["probe"] = Diagnostic(
                    failed, "Unsupported installed diagnostics; run powercontext setup kimi-code"
                )
                return checks
            for name, value in report["checks"].items():
                checks[name] = Diagnostic(DiagnosticStatus(value["status"]), value["detail"], value.get("checks"))
            if runtime:
                checks["runtime"] = _runtime_diagnostic(report["runtime"])
        except (OSError, subprocess.TimeoutExpired, ValueError, KeyError, TypeError):
            checks["server"] = Diagnostic(
                failed, "Readonly probes failed; run powercontext doctor kimi-code after repair"
            )
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
