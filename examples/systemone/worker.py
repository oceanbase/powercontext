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

"""Run one persisted Memory operation in an independent Python process."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import redirect_stdout
from pathlib import Path
from typing import Any

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    BuiltinRuntime,
    PrepareContextRequest,
    open_builtin_runtime,
)
from powercontext.builtin.scope import ScopeDraft

from .scenario import SCENARIO

_BEGIN = "BEGIN_POWERCONTEXT_PREPARED_CONTEXT_V1"
_END = "END_POWERCONTEXT_PREPARED_CONTEXT_V1"


async def _prepare(runtime: BuiltinRuntime, scope_id: str, query: str) -> dict[str, Any]:
    """Recall complete evidence and independently resolve its exact persisted citations."""
    prepared = await runtime.context.for_scope(scope_id).prepare(PrepareContextRequest(query=query, max_bytes=8000))
    citations: list[dict[str, Any]] = []
    if prepared.content is not None:
        payload = json.loads(prepared.content.split(_BEGIN, 1)[1].split(_END, 1)[0].strip())
        for item in payload["items"]:
            citation = item["citation"]
            # The current renderer uses relative citations for the requested Scope.
            # Resolving through that Scope validates ownership as well as entry identity.
            ref = ArtifactRef.model_validate(citation["artifact_ref"])
            if runtime.atomic_memory is None:
                raise RuntimeError("Atomic Memory is unavailable")  # noqa: TRY003
            record = await runtime.atomic_memory.for_scope(scope_id).get(ref.artifact_id, revision=ref.revision)
            if item.get("truncated") or record.artifact.content.text != item["content"]:
                raise ValueError("Prepared evidence does not match its persisted Atomic Memory")  # noqa: TRY003
            citations.append({"scope_id": scope_id, **citation})
    return {"prepared": prepared.model_dump(mode="json"), "citations": citations, "pid": os.getpid()}


async def execute(request: dict[str, Any]) -> dict[str, Any]:
    """Open a fresh Runtime, perform one operation, and close every database connection."""
    directory = Path(request["directory"])
    if not directory.is_absolute():
        raise ValueError("The run directory must be an absolute path")  # noqa: TRY003
    await asyncio.to_thread(directory.mkdir, parents=True, exist_ok=True)
    config = BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{directory / 'runtime.db'}"))
    async with open_builtin_runtime(config, scheduler_path=directory / "scheduler.db") as runtime:
        memory = runtime.atomic_memory
        if memory is None:
            raise RuntimeError("Atomic Memory is unavailable")  # noqa: TRY003
        if request["mode"] == "seed":
            if runtime.scopes is None:
                raise RuntimeError("The example requires the Scope registry")  # noqa: TRY003
            scope = await runtime.scopes.create(
                ScopeDraft(
                    title="Billing amount conventions",
                    summary="Synthetic project decisions remembered in the first session.",
                    idempotency_key="systemone-example:billing",
                )
            )
            other = await runtime.scopes.create(
                ScopeDraft(
                    title="Analytics amount conventions",
                    summary="A separate project with a conflicting rounding convention.",
                    idempotency_key="systemone-example:analytics",
                )
            )
            saved = await memory.for_scope(scope.scope_id).create((
                AtomicMemoryContent(kind="decision", text=SCENARIO["policy"]),
            ))
            other_saved = await memory.for_scope(other.scope_id).create((
                AtomicMemoryContent(
                    kind="decision",
                    text=(
                        f"{SCENARIO['query']}\n"
                        "Analytics project amount policy: use Decimal ROUND_HALF_EVEN for integer cents. "
                        "This decision belongs only to the separate analytics project."
                    ),
                ),
            ))
            return {
                "scope_id": scope.scope_id,
                "other_scope_id": other.scope_id,
                "artifact_ref": saved.primary.ref.model_dump(mode="json"),
                "other_artifact_ref": other_saved.primary.ref.model_dump(mode="json"),
                "pid": os.getpid(),
            }
        if request["mode"] == "recall":
            selected = await _prepare(runtime, request["scope_id"], SCENARIO["query"])
            other = await _prepare(runtime, request["other_scope_id"], SCENARIO["query"])
            isolated = (
                request["scope_id"] != request["other_scope_id"]
                and bool(selected["citations"])
                and bool(other["citations"])
                and all(citation["scope_id"] == request["scope_id"] for citation in selected["citations"])
                and all(citation["scope_id"] == request["other_scope_id"] for citation in other["citations"])
            )
            selected["isolation"] = {
                "status": "passed" if isolated else "failed",
                "scope_id": request["scope_id"],
                "other_scope_id": request["other_scope_id"],
                "content_bytes": selected["prepared"]["content_bytes"],
                "other_prepared": other["prepared"],
                "other_citations": other["citations"],
            }
            return selected
        if request["mode"] == "record":
            summary = request["summary"]
            if not isinstance(summary, str) or not summary.strip():
                raise ValueError("An observed outcome summary is required")  # noqa: TRY003
            saved = await memory.for_scope(request["scope_id"]).create((
                AtomicMemoryContent(kind="fact", text=f"invoice-outcome {summary}"),
            ))
            return {"artifact_ref": saved.primary.ref.model_dump(mode="json"), "pid": os.getpid()}
        if request["mode"] == "resume":
            return await _prepare(runtime, request["scope_id"], "invoice-outcome")
        raise ValueError("Unknown Memory worker mode")  # noqa: TRY003


def main() -> int:
    """Reserve stdout for the machine-readable result of one independent operation."""
    request = json.load(sys.stdin)
    with redirect_stdout(sys.stderr):
        result = asyncio.run(execute(request))
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
