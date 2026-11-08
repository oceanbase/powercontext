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

"""Plugin-local preview, registration and one-shot import; no host-specific core CLI."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from hashlib import sha256
from pathlib import Path

from pydantic import ValidationError

from powercontext.client import PowerContextClient, RemoteConnectorWorker, register_source_definitions
from powercontext.client.errors import ClientError, ServerResponseError
from powercontext.client.settings import ClientSettings
from powercontext.source_management.config import secret
from powercontext.source_management.models import SourceTarget
from powercontext.sources import ConnectorBinding, ConnectorRunStatus, SourceDefinitionRegistry

from . import CodexMemoryConnector
from .definition import MEMORY_DEFINITION
from .files import Config, MemoryInput, scan


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Import legacy Codex memories as derived Source snapshots")
    operations = result.add_subparsers(dest="operation", required=True)
    for name in ("preview", "register", "import"):
        command = operations.add_parser(name)
        if name != "register":
            command.add_argument("--codex-home", type=Path, default=Path(os.getenv("CODEX_HOME", "~/.codex")))
            command.add_argument("--namespace", required=True)
            command.add_argument("--path", default=".")
            command.add_argument("--max-file-bytes", type=int, default=262_144)
            command.add_argument("--max-total-bytes", type=int, default=16_777_216)
            command.add_argument("--max-files", type=int, default=1000)
            command.add_argument("--max-entries", type=int, default=10_000)
        if name != "preview":
            command.add_argument("--server-url")
            command.add_argument("--credential-file", type=Path)
            command.add_argument("--allow-insecure-http", action="store_true")
        if name == "import":
            command.add_argument("--scope-id", required=True)
            command.add_argument("--binding-id", required=True)
    return result


def configuration(options: argparse.Namespace) -> Config:
    return Config(**{
        key: getattr(options, key)
        for key in ("codex_home", "namespace", "max_file_bytes", "max_total_bytes", "max_files", "max_entries")
    })


async def execute(options: argparse.Namespace) -> int:
    registry = SourceDefinitionRegistry((MEMORY_DEFINITION,))
    if options.operation == "preview":
        config = configuration(options)
        value = MemoryInput(path=options.path)
        result = await asyncio.to_thread(scan, config, value)
        print(
            json.dumps(
                {
                    "files": [
                        {"path": item.relative_path, "source_id": item.snapshot_id()} for item in result.captures
                    ],
                    "failures": [{"path": path, "reason": reason} for path, reason in result.failures],
                    "skipped_empty": list(result.skipped),
                },
                ensure_ascii=False,
            )
        )
        return 1 if result.failures else 0
    settings = ClientSettings(
        allow_insecure_http=options.allow_insecure_http,
        **({} if options.server_url is None else {"server_url": options.server_url}),
    )
    token = (
        secret(options.credential_file)
        if options.credential_file is not None
        else (None if settings.api_token is None else settings.api_token.get_secret_value())
    )
    async with PowerContextClient(
        settings.server_url, token=token, timeout=settings.timeout, allow_insecure_http=options.allow_insecure_http
    ) as client:
        if options.operation == "register":
            await register_source_definitions(client=client, registry=registry)
            print(json.dumps({"registered": MEMORY_DEFINITION.name, "version": MEMORY_DEFINITION.version}))
            return 0
        config = configuration(options)
        value = MemoryInput(path=options.path)
        target = SourceTarget(
            target_id=sha256(value.path.encode()).hexdigest(),
            scope_id=options.scope_id,
            instance_id=options.binding_id,
            capability="codex.memory-history",
            capability_version="1",
            input=value.model_dump(mode="json"),
        )
        binding = ConnectorBinding(
            scope_id=options.scope_id,
            binding_id=options.binding_id,
            connector_name=CodexMemoryConnector.name,
            connector_version=CodexMemoryConnector.version,
        )
        result = await RemoteConnectorWorker(client=client, registry=registry, register_definitions=False).run(
            CodexMemoryConnector(config, (target,)), binding
        )
        # Checkpoints and file bodies stay on the Server; the local result exposes only receipts and bounded reasons.
        print(
            json.dumps(
                {
                    "status": result.status.value,
                    "items": [
                        {
                            "status": item.status.value,
                            "source_ref": None if item.source_ref is None else item.source_ref.model_dump(),
                            "reason": item.detail,
                        }
                        for item in result.items
                    ],
                },
                ensure_ascii=False,
            )
        )
        return 0 if result.status == ConnectorRunStatus.COMPLETE else 1


def main() -> int:
    options = parser().parse_args()
    try:
        return asyncio.run(execute(options))
    except ServerResponseError as error:
        print(json.dumps({"error": "server_rejected", "http_status": error.status_code}))
    except ClientError:
        print(json.dumps({"error": "server_unavailable"}))
    except (ValueError, OSError, ValidationError):
        print(json.dumps({"error": "configuration_invalid"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
