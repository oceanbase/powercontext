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

"""Prepared-context proof issuance and comparison against current Atomic Memory and code evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, cast

from pydantic import ValidationError

from powercontext.artifacts import ArtifactAddress, ArtifactRef
from powercontext.artifacts.search import ArtifactSearchExecutionContext
from powercontext.builtin.code.errors import CodeError
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
    from powercontext.builtin.code.application import ScopedCodeApplication
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
    return tuple(roots)


def _code_dependencies(scope_id: str, build: PreparedContextBuild) -> list[dict[str, str]]:
    """One dependency per cited file: its path and digest, with the index fingerprint kept for the record.

    Revalidation compares each cited file, not the whole index fingerprint, so an edit elsewhere in the
    repository does not invalidate a proof whose cited files are unchanged.
    """
    files: dict[str, dict[str, str]] = {}
    for origin in build.code_origins:
        if origin.scope_id != scope_id:
            raise ContextReuseError("context-reuse-cross-scope-unsupported")
        dependency = {"path": origin.path, "file_sha256": origin.file_sha256, "fingerprint": origin.fingerprint}
        if files.setdefault(origin.path, dependency)["file_sha256"] != origin.file_sha256:
            raise ContextReuseError("context-reuse-source-changed")
    return [files[path] for path in sorted(files)]


def _valid_code_dependencies(value: object) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise TypeError
    dependencies: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {"path", "file_sha256", "fingerprint"}:
            raise ValueError
        fields = cast("dict[str, object]", item)
        path, digest, fingerprint = fields["path"], fields["file_sha256"], fields["fingerprint"]
        if not (
            isinstance(path, str)
            and path
            and isinstance(fingerprint, str)
            and fingerprint
            and isinstance(digest, str)
            and _is_digest(digest)
        ):
            raise ValueError
        dependencies.append({"path": path, "file_sha256": digest, "fingerprint": fingerprint})
    return dependencies


def _is_digest(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


async def _current_code(code: ScopedCodeApplication | None, dependencies: list[dict[str, str]]) -> None:
    """Fail closed unless every cited file still has its recorded digest in the Scope's repository."""
    if not dependencies:
        return
    if code is None:
        raise ContextReuseError("context-reuse-code-unavailable")
    try:
        current = await code.file_digests(tuple(item["path"] for item in dependencies))
    except CodeError as error:
        raise ContextReuseError("context-reuse-code-unavailable") from error
    if any(current.get(item["path"]) != item["file_sha256"] for item in dependencies):
        raise ContextReuseError("context-reuse-source-changed")


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
    code: ScopedCodeApplication | None = None,
) -> PreparedContextWithReuse:
    if memory is None:
        raise ContextReuseError("context-reuse-unsupported")
    roots = _selected_roots(scope_id, build)
    code_dependencies = _code_dependencies(scope_id, build)
    if len(roots) + len(code_dependencies) > MAX_REUSE_DEPENDENCIES:
        raise ContextReuseError("context-reuse-dependency-limit")
    await _current_code(code, code_dependencies)
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
        "code": code_dependencies,
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
    code: ScopedCodeApplication | None = None,
) -> ValidatedContextReuse:
    if memory is None:
        raise ContextReuseError("context-reuse-unsupported")
    payload = codec.open(binding, purpose=REUSE_PURPOSE, now=now)
    if payload.get("scope_id") != scope_id or payload.get("identity") != binding_identity:
        raise ContextBindingError("context-binding-identity-mismatch")
    try:
        roots = tuple(ArtifactRef.model_validate(ref) for ref in payload["ordered_roots"])
        # Proofs issued before code dependencies existed carry no "code" field and cite no code.
        code_dependencies = _valid_code_dependencies(payload.get("code", []))
        expires_at = datetime.fromisoformat(payload["expires_at"])
        content_digest, content_bytes = payload["content_digest"], payload["content_bytes"]
        if (
            len(roots) + len(code_dependencies) > MAX_REUSE_DEPENDENCIES
            or expires_at.tzinfo is None
            or not isinstance(content_digest, str)
            or not _is_digest(content_digest)
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
    await _current_code(code, code_dependencies)
    return ValidatedContextReuse(content_digest, content_bytes, expires_at, roots)
