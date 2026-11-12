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

"""Read-only checks of declared Memory contracts, separate from service health."""

from __future__ import annotations

import json
from pathlib import Path

from powercontext.cli.system import Diagnostic, DiagnosticStatus, _request_json

MEMORY_PROTOCOL = "atomic-artifact-v1"


def read_plugin_memory_protocol(package: Path, manifest: str = "package.json") -> object:
    """Read optional package metadata without treating unknown/old packages as current."""
    try:
        metadata = json.loads((package / manifest).read_text(encoding="utf-8"))
        return metadata.get("powercontext", {}).get("memoryProtocol")
    except (OSError, ValueError, AttributeError):
        return None


def installed_memory_protocol(protocol: object, host: str) -> Diagnostic:
    """Report unknown installations conservatively instead of inferring from package versions."""
    if protocol == MEMORY_PROTOCOL:
        return Diagnostic(
            DiagnosticStatus.OK,
            "Installed plugin declares Atomic Memory v1; restart the host after an update. "
            "The running process and write behavior are not observed by this check.",
            checks={"memory_protocol": MEMORY_PROTOCOL, "running_host_configuration": "not_observed"},
        )
    return Diagnostic(
        DiagnosticStatus.DEGRADED if protocol is None else DiagnosticStatus.FAILED,
        "Installed plugin Memory compatibility is unverified or incompatible; it may still use the legacy "
        "collection protocol. Update the CLI/SDK and plugin to a release compatible with the Server, "
        f"run powercontext setup {host} for the affected profile, fully restart the host, and rerun Doctor. "
        "Healthy liveness/readiness does not establish plugin compatibility.",
        checks={"memory_protocol": "unverified" if protocol is None else "incompatible"},
    )


def server_memory_protocol(server_url: str, *, authorization: str | None = None) -> Diagnostic:
    """Inspect the OpenAPI contract using the selected connection's authorization."""
    try:
        status, body = _request_json(server_url, "/openapi.json", authorization=authorization)
    except OSError:
        return Diagnostic(
            DiagnosticStatus.DEGRADED, "Server Memory protocol could not be checked; inspect connectivity."
        )
    if status in {401, 403}:
        return Diagnostic(
            DiagnosticStatus.SKIPPED if authorization is None else DiagnosticStatus.DEGRADED,
            f"Server Memory protocol is unverified (OpenAPI HTTP {status}); configure valid credentials "
            "with permission to read the contract for this Server and host/profile, rerun setup, then Doctor. "
            "Health checks alone do not establish compatibility.",
        )
    if status != 200:
        return Diagnostic(
            DiagnosticStatus.DEGRADED,
            f"Server Memory protocol is unverified (OpenAPI HTTP {status}); expose the contract at the configured "
            "endpoint and rerun Doctor. Health checks alone do not establish compatibility.",
        )
    contracts = body.get("x-powercontext-feature-contracts") if isinstance(body, dict) else None
    memory = contracts.get("memory.explicit") if isinstance(contracts, dict) else None
    openapi = body.get("openapi") if isinstance(body, dict) else None
    major = memory.get("major") if isinstance(memory, dict) else None
    minor = memory.get("minor") if isinstance(memory, dict) else None
    if (
        isinstance(body, dict)
        and isinstance(openapi, str)
        and openapi.startswith("3.")
        and isinstance(body.get("paths"), dict)
        and type(major) is int
        and major == 1
        and type(minor) is int
        and minor >= 0
    ):
        return Diagnostic(
            DiagnosticStatus.OK,
            "Server declares Atomic Memory v1. This does not verify the installed plugin, running host or writes.",
        )
    return Diagnostic(
        DiagnosticStatus.FAILED,
        "memory_protocol_mismatch: Server does not declare the Atomic Memory v1 contract required by this CLI. "
        "Use compatible Server, CLI/SDK and plugin releases, rerun setup for the affected host/profile, "
        "fully restart the host, and rerun Doctor. Do not drop collection CAS preconditions or replay writes.",
    )
