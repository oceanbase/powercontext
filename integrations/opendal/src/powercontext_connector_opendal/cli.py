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

"""Command-line entry point for one independently scheduled Connector run."""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

from powercontext.client import PowerContextClient, RemoteConnectorWorker
from powercontext.sources import (
    ConnectorBinding,
    ConnectorRunStatus,
    SourceDefinitionRegistry,
)

from powercontext_connector_opendal.connector import (
    DEFAULT_MAX_FILE_SIZE,
    OPENDAL_TEXT_FILE_CONNECTOR_NAME,
    OpenDALTextFileConnector,
)
from powercontext_connector_opendal.source import TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION


def main() -> None:
    """Run one scan and return a process status suitable for an external scheduler."""

    raise SystemExit(asyncio.run(_run(_parser().parse_args())))


async def _run(args: argparse.Namespace) -> int:
    options = _storage_options(args.storage_option)
    if args.pattern:
        connector = OpenDALTextFileConnector.from_service(
            args.service,
            source_namespace=args.source_namespace,
            root=args.root,
            storage_options=options,
            patterns=tuple(args.pattern),
            max_files=args.max_files,
            max_file_size=args.max_file_size,
        )
    else:
        connector = OpenDALTextFileConnector.from_service(
            args.service,
            source_namespace=args.source_namespace,
            root=args.root,
            storage_options=options,
            max_files=args.max_files,
            max_file_size=args.max_file_size,
        )
    binding = ConnectorBinding(
        scope_id=args.scope_id,
        binding_id=args.binding_id,
        connector_name=OPENDAL_TEXT_FILE_CONNECTOR_NAME,
        connector_version=connector.version,
    )
    registry = SourceDefinitionRegistry((TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,))
    async with PowerContextClient(args.base_url, token=os.environ.get("POWERCONTEXT_TOKEN")) as client:
        result = await RemoteConnectorWorker(client=client, registry=registry).run(connector, binding)
    return 0 if result.status is ConnectorRunStatus.COMPLETE else 1


def _storage_options(values: Sequence[str]) -> dict[str, str]:
    options: dict[str, str] = {}
    for value in values:
        key, separator, option = value.partition("=")
        if not separator or not key or key.strip() != key:
            raise ValueError("storage options must use KEY=VALUE")  # noqa: TRY003
        options[key] = option
    return options


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--scope-id", required=True)
    parser.add_argument("--binding-id", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--source-namespace", required=True)
    parser.add_argument("--root", default="")
    parser.add_argument("--storage-option", action="append", default=[])
    parser.add_argument("--pattern", action="append")
    parser.add_argument("--max-files", type=int, default=10_000)
    parser.add_argument("--max-file-size", type=int, default=DEFAULT_MAX_FILE_SIZE)
    return parser


if __name__ == "__main__":
    main()
