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

"""Collect selected legacy Codex memory files as immutable Source snapshots."""

from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from powercontext.plugins import (
    Collect,
    CollectedItem,
    CollectRequest,
    CollectResult,
    Interval,
    SourcePlugin,
    TargetSpec,
)

_package_name = "powercontext_codex_memory_plugin"
if _package_name not in sys.modules:
    package = ModuleType(_package_name)
    package.__path__ = [str(Path(__file__).parent)]
    sys.modules[_package_name] = package


def _load_sibling(name: str):
    module_name = f"{_package_name}.{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    path = Path(__file__).with_name(f"{name}.py")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError from None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


MEMORY_DEFINITION = _load_sibling("definition").MEMORY_DEFINITION
_files = _load_sibling("files")
Config, MemoryInput, scan = _files.Config, _files.MemoryInput, _files.scan


class MemoryTarget(BaseModel):
    # Ignore fields added by a newer Server so older runners can continue.
    model_config = ConfigDict(extra="ignore")
    path: str = Field(default=".", min_length=1, description="Path inside CODEX_HOME/memories")


class MemoryCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["codex-memory.v1"] = "codex-memory.v1"
    snapshots: dict[str, str] = Field(default_factory=dict)


class CodexMemoryCollector:
    def __init__(self):
        home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
        self.config = Config(
            codex_home=home,
            namespace="codex",
        )

    async def collect(self, request: CollectRequest) -> CollectResult:
        result = CollectResult()
        remaining = request.max_items
        for target in request.targets:
            selection = MemoryInput.model_validate(target.parameters)
            try:
                scanned = await asyncio.to_thread(scan, self.config, selection)
            except (OSError, ValueError) as error:
                result.fail(target, code="memory_unreadable", message=type(error).__name__)
                continue
            if scanned.failures:
                # Do not commit a partial directory snapshot; retry the complete target next run.
                result.fail(target, code="memory_unreadable", message="One or more selected files could not be read")
                continue
            checkpoint = MemoryCheckpoint.model_validate(target.checkpoint or {})
            items = []
            snapshots = dict(checkpoint.snapshots)
            has_more = False
            for capture in scanned.captures:
                if snapshots.get(capture.relative_path) == capture.snapshot_id():
                    continue
                items.append(CollectedItem(definition=MEMORY_DEFINITION.name, value=capture))
                snapshots[capture.relative_path] = capture.snapshot_id()
                if len(items) >= remaining:
                    has_more = True
                    break
            result.add(
                target,
                items,
                checkpoint=MemoryCheckpoint(snapshots=snapshots).model_dump(mode="json"),
                has_more=has_more,
            )
            remaining -= len(items)
            if remaining <= 0:
                break
        return result


async def setup(ctx):
    return CodexMemoryCollector()


plugin = SourcePlugin(
    version="2.0.0",
    min_compatible_version="2.0.0",
    definitions=(MEMORY_DEFINITION,),
    collect=Collect(schedule=Interval(seconds=60)),
    target=TargetSpec(
        description="Collect a selected file or directory inside CODEX_HOME/memories",
        parameters=MemoryTarget,
        identity={"path": ("strip",)},
    ),
    setup=setup,
)
