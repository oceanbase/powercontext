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

"""CodeGraph's public library API, isolated behind a bounded child process."""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from powercontext.builtin.code.config import CodeGraphConfig
from powercontext.builtin.code.errors import (
    CodeUnavailableError,
    InvalidCodeRequestError,
    UnsupportedCodeCapabilityError,
)
from powercontext.builtin.code.models import CodeOperation
from powercontext.builtin.code.process import run_process

_ENGINE_VERSION = "1.6.0"
_BRIDGE = Path(__file__).with_name("engine.cjs")


@dataclass(frozen=True)
class EngineIdentity:
    """A concrete deployed engine and the adapter interpreting its output."""

    node: Path
    module: Path
    version: str
    digest: str


class CodeGraphAdapter:
    """Use one validated standalone build without installing or updating it."""

    def __init__(self, config: CodeGraphConfig) -> None:
        self.config = config

    async def identity(self, *, deadline: float) -> EngineIdentity:
        return await asyncio.to_thread(self._identity, deadline)

    def _identity(self, deadline: float) -> EngineIdentity:
        resolved = shutil.which(self.config.executable)
        if resolved is None:
            raise CodeUnavailableError("code_engine_missing")
        executable = Path(resolved).resolve()
        bundle = executable.parent.parent
        node = bundle / "node"
        module = bundle / "lib/dist/index.js"
        try:
            version = json.loads((bundle / "lib/package.json").read_text())["version"]
            if version != _ENGINE_VERSION:
                raise UnsupportedCodeCapabilityError("unsupported_capability")
            if not node.is_file() or not module.is_file():
                raise CodeUnavailableError("code_engine_layout")
            digest = hashlib.sha256(_BRIDGE.read_bytes())
            files = [node]
            files.extend(
                sorted(
                    path for path in (bundle / "lib").rglob("*") if path.suffix in {".js", ".node", ".wasm", ".json"}
                )
            )
            for path in files:
                if not path.is_file() or path.is_symlink():
                    raise CodeUnavailableError("code_engine_layout")
                digest.update(path.relative_to(bundle).as_posix().encode())
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        if monotonic() >= deadline:
                            raise CodeUnavailableError("code_timeout")
                        digest.update(chunk)
        except (OSError, ValueError, KeyError, TypeError):
            raise CodeUnavailableError("code_engine_layout") from None
        return EngineIdentity(node=node, module=module, version=version, digest=digest.hexdigest())

    async def build(self, root: Path, *, identity: EngineIdentity, deadline: float) -> dict[str, Any]:
        return await self._invoke(identity, {"command": "build", "root": str(root)}, deadline=deadline)

    async def query(
        self,
        root: Path,
        operation: CodeOperation,
        *,
        identity: EngineIdentity,
        deadline: float,
        max_cache_bytes: int,
        prepare: bool = False,
    ) -> dict[str, Any]:
        # CodeGraph.open() can write SQLite state despite its readOnly option.
        # Only this disposable copy is opened; the captured source is read by us.
        with tempfile.TemporaryDirectory(prefix="query-", dir=root.parent) as directory:
            query_root = Path(directory)
            await asyncio.to_thread(_copy_index, root, query_root, max_cache_bytes, deadline)
            return await self._invoke(
                identity,
                {
                    "command": "query",
                    "root": str(query_root),
                    "operation": operation.model_dump(mode="json"),
                    "prepare": prepare,
                },
                deadline=deadline,
            )

    async def _invoke(self, identity: EngineIdentity, payload: dict[str, Any], *, deadline: float) -> dict[str, Any]:
        output = await run_process(
            (
                str(identity.node),
                "--liftoff-only",
                "--disable-warning=ExperimentalWarning",
                str(_BRIDGE),
                str(identity.module),
            ),
            cwd=_BRIDGE.parent,
            deadline=deadline,
            max_output_bytes=4 * 1024 * 1024,
            stdin=json.dumps(payload, ensure_ascii=False).encode(),
        )
        try:
            envelope = json.loads(output)
            error = envelope.get("error")
            result = envelope.get("result")
        except (ValueError, AttributeError):
            raise CodeUnavailableError("code_engine_output") from None
        if error == "invalid_code_target":
            raise InvalidCodeRequestError("invalid_code_target")
        if error == "unsupported_capability":
            raise UnsupportedCodeCapabilityError("unsupported_capability")
        if error is not None or not isinstance(result, dict):
            raise CodeUnavailableError("code_engine_failed")
        return result


def _copy_index(source: Path, destination: Path, max_bytes: int, deadline: float) -> None:
    index = source / ".codegraph"
    size = 0
    try:
        if index.is_symlink():
            raise CodeUnavailableError("code_cache_invalid")
        for path in sorted(index.rglob("*")):
            if monotonic() >= deadline:
                raise CodeUnavailableError("code_timeout")
            if path.is_symlink():
                raise CodeUnavailableError("code_cache_invalid")
            target = destination / ".codegraph" / path.relative_to(index)
            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            size += path.stat().st_size
            if size > max_bytes:
                raise CodeUnavailableError("code_cache_limit")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    except OSError:
        raise CodeUnavailableError("code_cache_unavailable") from None
