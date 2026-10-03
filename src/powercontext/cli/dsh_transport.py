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

"""Inspect DSH patches without booting plugins or changing host configuration."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from shutil import which
from typing import Any

import typer

from powercontext.cli.dsh_runtime import dsh_home
from powercontext.client.transport_policy import normalize_client_url, parse_client_boolean


def read_dsh_settings(
    *, profile: str = "web", candidate: Path | None = None, prospective: bool = False, require_installed: bool = False
) -> dict[str, Any]:
    """Compose the installed host's layers; optionally substitute the installation candidate."""
    from powercontext.cli.dsh import dsh_executable
    from powercontext.cli.system import SetupError

    home = dsh_home()
    if Path(profile).name != profile or profile in {".", ".."} or "\\" in profile:
        raise ValueError("Cannot inspect an invalid DSH profile name")  # noqa: TRY003
    # An absent profile with no patches has no native transport override. The
    # candidate check always composes the host's default bundles before install.
    if (
        candidate is None
        and not require_installed
        and not any(
            path.exists()
            for path in (
                home / "profiles" / profile / "package.json",
                home / "profiles" / profile / "cordis.patch.yml",
                home / "cordis.patch.yml",
            )
        )
    ):
        return {}
    try:
        executable = dsh_executable()
    except SetupError:
        raise ValueError("DSH CLI is required to inspect its configuration") from None  # noqa: TRY003
    sibling_node = Path(executable).parent / ("node.exe" if os.name == "nt" else "node")
    node = str(sibling_node) if sibling_node.is_file() else which("node")
    if not node:
        raise ValueError("Node.js is required to inspect DSH configuration")  # noqa: TRY003
    command = [
        node,
        str(Path(__file__).with_name("dsh_config.mjs")),
        executable,
        str(home),
        profile,
        str(candidate) if candidate else "",
        str(prospective).lower(),
        str(require_installed).lower(),
    ]
    try:
        result = subprocess.run(  # noqa: S603 - fixed helper and separate arguments, no shell.
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError):
        raise ValueError("Cannot inspect DSH configuration; check the installed DSH and Node.js versions") from None  # noqa: TRY003
    if not isinstance(payload, dict):
        raise ValueError("Invalid DSH configuration inspection result")  # noqa: TRY003, TRY004
    if error := payload.get("error"):
        raise ValueError(f"{error['reason']}: {error['location']}".rstrip(": "))
    settings = payload.get("settings")
    if result.returncode or not isinstance(settings, dict):
        raise ValueError("Cannot inspect DSH configuration")  # noqa: TRY003
    for warning in payload.get("warnings", []):
        typer.echo(f"WARNING: {warning}", err=True)
    return settings


def matching_dsh_consent(settings: dict[str, Any], endpoint: str) -> bool | None:
    """Native refusal is unconditional; native permission belongs to its endpoint."""
    consent = settings.get("allowInsecureHttp")
    if consent is False:
        return False
    native_url = settings.get("baseUrl")
    if native_url and _endpoint(native_url) == _endpoint(endpoint):
        return consent
    return None


def validate_dsh_setup_transport(settings: dict[str, Any], endpoint: str, allowed: bool) -> None:
    """Reject a native override that would undo setup's selected connection policy."""
    keys = (
        "POWERCONTEXT_DSH_BASE_URL",
        "POWERCONTEXT_DSH_SERVER_URL",
        "POWERCONTEXT_DSH_ENDPOINT",
        "POWERCONTEXT_CLIENT_SERVER_URL",
    )
    override = next((os.environ[key].strip() for key in keys if os.environ.get(key, "").strip()), None)
    native_url = settings.get("baseUrl")
    if _endpoint(override or native_url or endpoint) != _endpoint(endpoint):
        raise ValueError(  # noqa: TRY003
            "DSH PowerContext baseUrl conflicts with the selected setup endpoint. "
            "Align or remove baseUrl in the profile/home cordis.patch.yml or bundle, then rerun setup."
        )
    consent = next(
        (
            parse_client_boolean(os.environ[key])
            for key in ("POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP", "POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP")
            if key in os.environ
        ),
        matching_dsh_consent(settings, endpoint),
    )
    if consent is not None and consent != allowed:
        raise ValueError(  # noqa: TRY003
            "DSH PowerContext allowInsecureHttp conflicts with setup's HTTP consent. "
            "Align or remove the overriding setting in cordis.patch.yml, a bundle, or the runtime environment."
        )


def _endpoint(value: str) -> str:
    return normalize_client_url(value).removesuffix("/mcp").rstrip("/")
