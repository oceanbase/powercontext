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

"""Prepared-context proof issuance and comparison against current Atomic Memory evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING

from pydantic import ValidationError

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.runtime.atomic_memory import (
    MAX_ATOMIC_MEMORY_DEPENDENCIES,
    MAX_ATOMIC_MEMORY_DEPENDENCY_DEPTH,
    AtomicMemoryDependencyError,
)
from powercontext.builtin.runtime.context_bindings import BindingCodec, ContextBindingError
from powercontext.builtin.runtime.errors import InvalidRuntimeRequestError
from powercontext.builtin.runtime.models import ContextReuse, PreparedContext, PreparedContextWithReuse
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild

if TYPE_CHECKING:
    from powercontext.builtin.runtime.application import ScopedAtomicMemoryApplication

MAX_REUSE_DEPENDENCIES = MAX_ATOMIC_MEMORY_DEPENDENCIES
MAX_REUSE_DEPTH = MAX_ATOMIC_MEMORY_DEPENDENCY_DEPTH
REUSE_RETENTION = timedelta(minutes=5)
REUSE_PURPOSE = "context-reuse"


class ContextReuseError(InvalidRuntimeRequestError):
    """A value cannot be frozen or reused with complete current provenance."""


@dataclass(frozen=True)
class ValidatedContextReuse:
    """Private verified metadata; the final executor must locate the complete value."""

    content_digest: str
    content_bytes: int
    expires_at: datetime
    ordered_roots: tuple[ArtifactRef, ...]
    trust: str = "historical-data"
    preserve: tuple[str, ...] = ("content", "citations", "trust", "order")


def _selected_roots(scope_id: str, build: PreparedContextBuild) -> tuple[ArtifactRef, ...]:
    roots = []
    for origin in build.origins:
        if isinstance(origin, ArtifactAddress):
            if origin.scope_id != scope_id:
                raise ContextReuseError("context-reuse-cross-scope-unsupported")
            ref = origin.artifact
        else:
            ref = origin
        if ref.family != "atomic-memory":
            raise ContextReuseError("context-reuse-family-unsupported")
        roots.append(ref)
    if build.code_origins:
        raise ContextReuseError("context-reuse-code-unsupported")
    if len(roots) > MAX_REUSE_DEPENDENCIES:
        raise ContextReuseError("context-reuse-dependency-limit")
    return tuple(roots)


async def prepare_reuse(
    memory: ScopedAtomicMemoryApplication | None,
    scope_id: str,
    build: PreparedContextBuild,
    prepared: PreparedContext,
    *,
    codec: BindingCodec,
    binding_identity: str,
    now: datetime,
    context: ArtifactSearchExecutionContext | None,
) -> PreparedContextWithReuse:
    if memory is None:
        raise ContextReuseError("context-reuse-unsupported")
    roots = _selected_roots(scope_id, build)
    try:
        dependencies = await memory.read_dependency_snapshot(roots, context=context, selected=build.reuse_roots)
    except AtomicMemoryDependencyError as error:
        raise ContextReuseError(f"context-reuse-{error.reason}") from error
    expires_at = now.astimezone(UTC) + REUSE_RETENTION
    payload = {
        "scope_id": scope_id,
        "identity": binding_identity,
        "content_digest": sha256((prepared.content or "").encode("utf-8")).hexdigest(),
        "content_bytes": prepared.content_bytes,
        "ordered_roots": [ref.model_dump(mode="json") for ref in roots],
        "dependencies": dependencies,
        "expires_at": expires_at.isoformat(),
        "trust": "historical-data",
    }
    binding = codec.seal(payload, purpose=REUSE_PURPOSE, expires_at=expires_at)
    return PreparedContextWithReuse(context=prepared, reuse=ContextReuse(expires_at=expires_at, binding=binding))


async def validate_reuse(
    memory: ScopedAtomicMemoryApplication | None,
    scope_id: str,
    binding: str,
    *,
    codec: BindingCodec,
    binding_identity: str,
    now: datetime,
    context: ArtifactSearchExecutionContext | None,
) -> ValidatedContextReuse:
    if memory is None:
        raise ContextReuseError("context-reuse-unsupported")
    payload = codec.open(binding, purpose=REUSE_PURPOSE, now=now)
    if payload.get("scope_id") != scope_id or payload.get("identity") != binding_identity:
        raise ContextBindingError("context-binding-identity-mismatch")
    try:
        roots = tuple(ArtifactRef.model_validate(ref) for ref in payload["ordered_roots"])
        expires_at = datetime.fromisoformat(payload["expires_at"])
        content_digest, content_bytes = payload["content_digest"], payload["content_bytes"]
        if (
            len(roots) > MAX_REUSE_DEPENDENCIES
            or expires_at.tzinfo is None
            or not isinstance(content_digest, str)
            or len(content_digest) != 64
            or any(character not in "0123456789abcdef" for character in content_digest)
            or type(content_bytes) is not int
            or not 0 <= content_bytes <= 32768
            or payload["trust"] != "historical-data"
        ):
            raise ValueError  # noqa: TRY301 - signed payload validation has one public failure code
    except (ValidationError, ValueError, TypeError, KeyError) as error:
        raise ContextBindingError("context-binding-invalid") from error
    try:
        dependencies = await memory.read_dependency_snapshot(roots, context=context)
    except AtomicMemoryDependencyError as error:
        raise ContextReuseError(f"context-reuse-{error.reason}") from error
    if dependencies != payload.get("dependencies"):
        raise ContextReuseError("context-reuse-source-changed")
    return ValidatedContextReuse(content_digest, content_bytes, expires_at, roots)
