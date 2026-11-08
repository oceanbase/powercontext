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

"""Opt-in import of legacy Codex memories through Source plugin contracts."""

from __future__ import annotations

import asyncio
from hashlib import sha256
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from powercontext.source_management import Capability, SourceTarget, TargetIdentity
from powercontext.source_management.models import ManagementError
from powercontext.sources import ConnectorRunCompletion, ConnectorRunStatus, ConnectorSubmissionStatus

from .definition import MEMORY_DEFINITION
from .files import Config, MemoryInput, scan, selection

plugin_api_version = "2"
connector_name = "codex-memory"
connector_version = "1"
definitions = (MEMORY_DEFINITION,)
capabilities = (
    Capability(
        name="codex.memory-history",
        version="1",
        description="Capture legacy Codex memory files as unreviewed derived Source evidence in an explicit Scope",
        input_schema=MemoryInput.model_json_schema(),
    ),
)


class MemoryCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["codex-memory.v1"] = "codex-memory.v1"
    snapshots: dict[str, dict[str, str]] = Field(default_factory=dict)


async def identify_target(request, config):
    value = MemoryInput.model_validate(request.input)
    selection(config, value)
    return TargetIdentity(
        target_key=sha256(value.path.encode()).hexdigest(), normalized_input=value.model_dump(mode="json")
    )


class CodexMemoryConnector:
    name = connector_name
    version = connector_version
    source_definitions = frozenset({MEMORY_DEFINITION.name})

    def __init__(self, config: Config, targets: tuple[SourceTarget, ...], *, managed_targets=None):
        self.config, self.targets, self.managed_targets = config, targets, managed_targets

    async def run(self, session):
        previous = (
            MemoryCheckpoint() if session.checkpoint is None else MemoryCheckpoint.model_validate(session.checkpoint)
        )
        checkpoint = previous.model_copy(deep=True)
        submitted: set[str] = set()
        attempted: set[str] = set()
        complete = True
        for target in self.targets:
            if target.scope_id != session.binding.scope_id:
                raise ValueError("Memory target belongs to a different Scope")  # noqa: TRY003
            result = await asyncio.to_thread(scan, self.config, MemoryInput.model_validate(target.input))
            for index, (_path, reason) in enumerate(result.failures):
                session.fail(f"file-{target.target_id}-{index}", MEMORY_DEFINITION.name, reason)
            complete = complete and not result.failures
            accepted = dict(previous.snapshots.get(target.target_id, {}))
            current: dict[str, str] = {}
            for capture in result.captures:
                path_key = sha256(capture.relative_path.encode()).hexdigest()
                identifier = capture.snapshot_id()
                if accepted.get(path_key) == identifier or identifier in submitted:
                    current[path_key] = identifier
                    continue
                if identifier in attempted:
                    complete = False
                    continue
                attempted.add(identifier)
                if self.managed_targets is None:
                    receipt = await session.submit(identifier, MEMORY_DEFINITION.name, capture)
                else:
                    receipt = await self.managed_targets.submit(
                        target,
                        session=session,
                        item_id=identifier,
                        definition_name=MEMORY_DEFINITION.name,
                        value=capture,
                    )
                if receipt.status != ConnectorSubmissionStatus.ACCEPTED:
                    complete = False
                    continue
                submitted.add(identifier)
                current[path_key] = identifier
            if not result.failures:
                checkpoint.snapshots[target.target_id] = current
        return ConnectorRunCompletion(
            status=ConnectorRunStatus.COMPLETE if complete else ConnectorRunStatus.INCOMPLETE,
            checkpoint=checkpoint.model_dump(mode="json"),
        )


async def apply(ctx, config):
    connector = CodexMemoryConnector(config, (), managed_targets=ctx.targets)
    ctx.connect(connector)
    while True:
        targets = await ctx.targets.list()
        for scope_id in sorted({target.scope_id for target in targets}):
            selected = tuple(target for target in targets if target.scope_id == scope_id)
            connector.targets = selected
            try:
                await ctx.run_batch(scope_id=scope_id, targets=selected)
            except ManagementError as error:
                if error.code != "stale-target":
                    raise
                break
        await asyncio.sleep(config.poll_interval)
