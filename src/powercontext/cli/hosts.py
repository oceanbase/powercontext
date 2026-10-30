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

"""Select agent targets and report independent setup and diagnostic results."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Sequence
from typing import Any

import typer


def stdin_is_tty() -> bool:
    return sys.stdin.isatty()


def select_hosts(requested: Sequence[str] | None, *, json_output: bool = False) -> tuple[str, ...]:
    """Validate all targets before writes; prompt once when no targets were supplied."""
    from powercontext.cli.installation import installers

    names = tuple(installers())
    if not requested:
        if json_output or not stdin_is_tty():
            raise typer.BadParameter("Specify one or more agents when running non-interactively")  # noqa: TRY003 - CLI input validation.
        for index, name in enumerate(names, 1):
            typer.echo(f"  {index}) {name}")
        typer.echo("Select agents by name or number, separated by commas; Enter cancels:")
        tokens = sys.stdin.readline().strip().split(",")
        requested = [
            names[int(token.strip()) - 1]
            if token.strip().isdigit() and 1 <= int(token.strip()) <= len(names)
            else token.strip()
            for token in tokens
            if token.strip()
        ]
    selected = tuple(dict.fromkeys(requested))
    unknown = set(selected) - set(names)
    if unknown:
        raise typer.BadParameter(f"Unknown agent: {', '.join(sorted(unknown))}. Choose from: {', '.join(names)}")  # noqa: TRY003 - CLI input validation.
    return selected


def validate_native_options(selected: Sequence[str], **options: Any) -> None:
    """Reject explicit flags that none of the selected agents can use."""
    owners = {
        "capture_prompts": ("claude-code", "kimi-code", "zcode"),
        "scope_id": ("kimi-code",),
        "workspace_scope": ("kimi-code",),
        "boundary_flush": ("zcode",),
        "profile": ("dsh", "kimi-code"),
        "dsh_command": ("dsh",),
        "minimax_data_dir": ("minimax",),
        "minimax_command": ("minimax",),
        "runtime": ("kimi-code",),
        "mcp": ("kimi-code",),
        "cwd": ("kimi-code",),
        "session_id": ("kimi-code",),
        "runtime_data_dir": ("zcode",),
        "prepare": ("zcode",),
    }
    for key, value in options.items():
        if value is not None and not set(selected).intersection(owners[key]):
            flag = "--" + key.replace("_", "-")
            raise typer.BadParameter(f"{flag} requires selecting {', '.join(owners[key])}")  # noqa: TRY003

    if options.get("profile") is not None:
        from powercontext.cli.dsh_runtime import DshProfile
        from powercontext.cli.kimi_code import KimiProfile

        profiles = {"dsh": DshProfile, "kimi-code": KimiProfile}
        for host in selected:
            profile_type = profiles.get(host)
            if profile_type is None:
                continue
            try:
                profile_type(options["profile"])
            except ValueError as error:
                choices = ", ".join(item.value for item in profile_type)
                raise typer.BadParameter(f"{host} profile must be one of: {choices}", param_hint="--profile") from error  # noqa: TRY003 - CLI input validation.


def native_options(host: str, **options: Any) -> dict[str, Any]:
    """Pass only options owned by the selected native adapter."""
    fields = {
        "claude-code": ("capture_prompts",),
        "dsh": ("dsh_target",),
        "kimi-code": ("scope_id", "capture_prompts", "workspace_scope", "profile"),
        "zcode": ("capture_prompts", "boundary_flush"),
        "minimax": ("minimax_data_dir", "minimax_command"),
    }
    return {key: options[key] for key in fields.get(host, ()) if options.get(key) is not None}


def doctor_checks(host: str, **options) -> dict[str, Any]:
    """Run native verification and Server checks through the doctor boundary."""
    from powercontext.cli.installation import installers
    from powercontext.cli.native_transport import resolve_host_transport
    from powercontext.cli.system import Diagnostic, DiagnosticStatus, server_diagnostics

    try:
        if host == "dsh" and "dsh_target" not in options:
            from powercontext.cli.dsh_runtime import resolve_dsh_target

            options["dsh_target"] = resolve_dsh_target(options.get("profile"), options.get("dsh_command"))
        target_options = native_options(host, **options)
        if host == "minimax":
            target_options["read_only"] = True
        installer = installers()[host](**target_options)
        fields = {"kimi-code": ("runtime", "cwd", "session_id", "mcp"), "zcode": ("runtime_data_dir", "prepare")}
        checks = installer.verify(**{key: options[key] for key in fields.get(host, ()) if options.get(key) is not None})
    except Exception as error:  # Each target must produce a result even when native configuration is malformed.
        return {"verification": Diagnostic(DiagnosticStatus.FAILED, str(error))}
    try:
        if host == "minimax":
            checks.update(installer.server_checks())
            return checks
        target = {"dsh_target": installer.dsh_target} if host == "dsh" else {}
        endpoint, allowed = resolve_host_transport(host, **target)
        checks.update(
            server_diagnostics(server_url=endpoint, allow_insecure_http=allowed, readiness=checks.get("server"))
        )
    except Exception as error:  # A failed Server probe must not hide completed installation.
        checks["transport"] = Diagnostic(DiagnosticStatus.FAILED, str(error))
    return checks


def diagnostic_result(checks) -> dict[str, Any]:
    from powercontext.cli.system import DiagnosticStatus, _diagnostics_status

    # Optional session history stays visible without pretending that it was observed.
    required = {
        key: item
        for key, item in checks.items()
        if item.status is not DiagnosticStatus.SKIPPED or key not in {"runtime", "mcp_session", "mcp_full_profile"}
    }
    status = _diagnostics_status(required)
    return {"status": status.value, "checks": {key: item.as_json() for key, item in checks.items()}}


def report_agents(selected: Sequence[str], action: Callable[[str], dict[str, Any]], *, json_output: bool) -> None:  # noqa: C901 - render one complete batch result.
    """Finish every selected target, preserving partial success and all diagnostics."""
    rows: dict[str, dict[str, Any]] = {}
    for name in selected:
        try:
            rows[name] = action(name)
        except Exception as error:  # Isolate agents; cancellation and process exits still propagate.
            rows[name] = {"status": "failed", "error": "\n".join([str(error), *getattr(error, "__notes__", ())])}
    for row in rows.values():
        row["ok"] = row["status"] in {"ok", "configured"}
    failed = any(row["status"] not in {"ok", "configured"} for row in rows.values())
    if json_output:
        typer.echo(json.dumps({"ok": not failed, "hosts": rows}, ensure_ascii=False, indent=2))
    else:
        for name, row in rows.items():
            typer.echo(f"{name}: {row['status']}" + (" (setup complete)" if row.get("setup") == "complete" else ""))
            for key in (
                "plugin_path",
                "package_path",
                "cache_dir",
                "profile",
                "profile_dir",
                "authorization_state",
                "private_mcp_state",
            ):
                if key in row.get("result", {}):
                    typer.echo(f"  {key}: {row['result'][key]}")
            if "error" in row:
                typer.echo(row["error"])
            for key, item in row.get("checks", {}).items():
                typer.echo(f"  {key}: {item['status']} - {item['detail']}")
                for check, status in item.get("checks", {}).items():
                    typer.echo(f"    {check}: {status}")
    if failed:
        raise typer.Exit(code=1)
