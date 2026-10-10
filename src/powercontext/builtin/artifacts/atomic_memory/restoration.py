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

"""Whole-group restoration planning and independently typed signed previews."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.artifacts.atomic_memory.errors import (
    AtomicMemoryPreviewExpiredError,
    AtomicMemoryRelationError,
    InvalidAtomicMemoryPreviewError,
    InvalidAtomicMemoryStateError,
)
from powercontext.builtin.artifacts.atomic_memory.models import (
    AtomicMemory,
    AtomicMemoryDraft,
    AtomicMemoryPlan,
    AtomicMemoryRead,
    AtomicMemoryRecord,
    AtomicMemoryRestoreItem,
    AtomicMemoryRestoreOperation,
    AtomicMemoryStateValue,
    AtomicMemoryWrite,
)


def merge_inputs(result: AtomicMemory) -> tuple[ArtifactRef, ...]:
    """Resolve only selected exact inputs; ordinary lineage is not a merge relation."""

    creation = result.content.creation
    if result.revision != 1 or creation is None:
        raise AtomicMemoryRelationError("the result has no valid merge creation revision")  # noqa: TRY003
    selected: list[ArtifactRef] = []
    for artifact_id in creation.input_artifact_ids:
        refs = tuple(
            ref
            for ref in result.lineage.artifacts
            if ref.family == AtomicMemory.family and ref.artifact_id == artifact_id
        )
        if len(refs) != 1 or artifact_id == result.artifact_id:
            raise AtomicMemoryRelationError("merge creation must select exactly one distinct exact input reference")  # noqa: TRY003
        selected.append(refs[0])
    return tuple(selected)


async def calculate_restoration(
    *,
    scope_id: str,
    subject: str,
    target: AtomicMemoryRecord,
    operation: AtomicMemoryRestoreOperation,
    selected: AtomicMemory | None,
    load_current: Callable[[str], Awaitable[AtomicMemoryRecord]],
    load_first: Callable[[str], Awaitable[AtomicMemory]],
) -> AtomicMemoryPlan:
    """Follow the current merge path, then interpret it from its endpoint inward."""

    if target.state.state is AtomicMemoryStateValue.RETIRED:
        raise InvalidAtomicMemoryStateError("retired memory identities cannot be restored")  # noqa: TRY003
    if operation == "undo_merge" and selected is not None:
        raise InvalidAtomicMemoryStateError("undo_merge does not accept a content revision")  # noqa: TRY003
    records = {target.artifact.artifact_id: target}
    result_ids: list[str] = []
    cursor = target
    while cursor.state.state is AtomicMemoryStateValue.MERGED:
        next_id = cursor.state.merged_into_id
        if next_id is None or next_id in records:
            raise AtomicMemoryRelationError("the current merge path is missing or cyclic")  # noqa: TRY003
        cursor = await load_current(next_id)
        if cursor.state.state is AtomicMemoryStateValue.RETIRED:
            raise AtomicMemoryRelationError("a live merge path cannot end in a retired result")  # noqa: TRY003
        records[next_id] = cursor
        result_ids.append(next_id)
    endpoint = cursor.as_read()
    undo_ids = list(reversed(result_ids))
    if operation == "undo_merge":
        undo_ids.append(target.artifact.artifact_id)
    final = await _expand_undo(undo_ids, records, load_current, load_first)
    target_id = target.artifact.artifact_id
    if operation == "restore":
        final[target_id] = AtomicMemoryStateValue.ACTIVE
    writes, restored = _restoration_writes(final, records, target_id, selected)
    return AtomicMemoryPlan(
        scope_id=scope_id,
        subject=subject,
        reads=tuple(records[identity].as_read() for identity in sorted(records)),
        writes=tuple(writes),
        primary_artifact_id=target_id,
        operation=operation,
        endpoint=endpoint,
        target_revision=None if selected is None else selected.revision,
        undo_merge_results=tuple(undo_ids),
        restore=tuple(restored),
    )


async def _expand_undo(
    undo_ids: list[str],
    records: dict[str, AtomicMemoryRecord],
    load_current: Callable[[str], Awaitable[AtomicMemoryRecord]],
    load_first: Callable[[str], Awaitable[AtomicMemory]],
) -> dict[str, AtomicMemoryStateValue]:
    """Verify frozen input relations and compute the final state of the affected group."""

    final: dict[str, AtomicMemoryStateValue] = {}
    for result_id in undo_ids:
        first = await load_first(result_id)
        refs = merge_inputs(first)
        selected_ids = {ref.artifact_id for ref in refs}
        if any(
            record.state.merged_into_id == result_id and identity not in selected_ids
            for identity, record in records.items()
        ):
            raise AtomicMemoryRelationError("the merge path input is absent from the result creation selector")  # noqa: TRY003
        final[result_id] = AtomicMemoryStateValue.RETIRED
        for ref in refs:
            current = records.get(ref.artifact_id)
            if current is None:
                current = await load_current(ref.artifact_id)
                records[ref.artifact_id] = current
            if (
                current.state.state is not AtomicMemoryStateValue.MERGED
                or current.state.merged_into_id != result_id
                or current.ref != ref
            ):
                raise AtomicMemoryRelationError(  # noqa: TRY003
                    "merge inputs do not match their current destination and frozen version"
                )
            final[ref.artifact_id] = AtomicMemoryStateValue.ACTIVE
    return final


def _restoration_writes(
    final: dict[str, AtomicMemoryStateValue],
    records: dict[str, AtomicMemoryRecord],
    target_id: str,
    selected: AtomicMemory | None,
) -> tuple[list[AtomicMemoryWrite], list[AtomicMemoryRestoreItem]]:
    """Build only final changes; intermediate results never need projections."""

    writes: list[AtomicMemoryWrite] = []
    restored: list[AtomicMemoryRestoreItem] = []
    for artifact_id, state in sorted(final.items()):
        current = records[artifact_id]
        draft = None
        if artifact_id == target_id and selected is not None:
            # Historical lineage_only Sources stay bound to their historical
            # target; a precise historical Artifact reference retains evidence.
            refs = tuple(
                dict.fromkeys((ref.family, ref.artifact_id, ref.revision) for ref in (current.ref, selected.as_ref()))
            )
            draft = AtomicMemoryDraft(
                content=selected.content.without_creation(),
                artifacts=tuple(
                    ArtifactRef(family=family, artifact_id=identity, revision=revision)
                    for family, identity, revision in refs
                ),
            )
        if state is AtomicMemoryStateValue.ACTIVE:
            restored.append(
                AtomicMemoryRestoreItem(
                    artifact_id=artifact_id,
                    source_revision=selected.revision
                    if draft is not None and selected is not None
                    else current.artifact.revision,
                    creates_revision=draft is not None,
                )
            )
        if state != current.state.state or draft is not None:
            writes.append(AtomicMemoryWrite(artifact_id=artifact_id, state=state, draft=draft, current=current))
    return writes, restored


class _PreviewClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: Literal["powercontext.atomic-memory.restoration-preview.v1"]
    key_id: str
    scope_id: str
    subject: str
    operation: AtomicMemoryRestoreOperation
    artifact_id: str
    revision: StrictInt | None
    endpoint: AtomicMemoryRead
    expires_at: StrictInt


class AtomicMemoryPreviewSigner:
    """Sign previews with explicit shared keys; no per-process random fallback."""

    def __init__(
        self,
        *,
        keys: Mapping[str, bytes],
        active_key_id: str,
        ttl_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if active_key_id not in keys or any(not key_id or len(secret) < 32 for key_id, secret in keys.items()):
            raise ValueError("preview keys need a configured active key and at least 32 secret bytes")  # noqa: TRY003
        if isinstance(ttl_seconds, bool) or ttl_seconds < 1:
            raise ValueError("preview TTL must be positive")  # noqa: TRY003
        self._keys = dict(keys)
        self._active_key_id = active_key_id
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    def encode(self, plan: AtomicMemoryPlan) -> tuple[str, datetime]:
        if plan.endpoint is None or plan.operation not in {"restore", "undo_merge"}:
            raise InvalidAtomicMemoryPreviewError("only restoration plans can be previewed")  # noqa: TRY003
        expires_at = self._now() + self._ttl
        claims = _PreviewClaims(
            format="powercontext.atomic-memory.restoration-preview.v1",
            key_id=self._active_key_id,
            scope_id=plan.scope_id,
            subject=plan.subject,
            operation=cast(AtomicMemoryRestoreOperation, plan.operation),
            artifact_id=plan.primary_artifact_id,
            revision=plan.target_revision,
            endpoint=plan.endpoint,
            expires_at=int(expires_at.timestamp()),
        )
        payload = json.dumps(
            claims.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ).encode()
        signature = hmac.digest(self._keys[self._active_key_id], payload, "sha256")
        return f"{_encode(payload)}.{_encode(signature)}", datetime.fromtimestamp(claims.expires_at, UTC)

    def validate(
        self,
        token: str,
        *,
        scope_id: str,
        subject: str,
        operation: AtomicMemoryRestoreOperation,
        artifact_id: str,
        revision: int | None,
    ) -> AtomicMemoryRead:
        if len(token) > 16_384:
            raise InvalidAtomicMemoryPreviewError("the restoration preview token is invalid")  # noqa: TRY003
        try:
            payload_part, signature_part = token.split(".")
            payload, signature = _decode(payload_part), _decode(signature_part)
            claims = _PreviewClaims.model_validate_json(payload, strict=True)
            key = self._keys[claims.key_id]
        except (ValueError, UnicodeError, binascii.Error, ValidationError, KeyError) as error:
            raise InvalidAtomicMemoryPreviewError("the restoration preview token is invalid") from error  # noqa: TRY003
        if not hmac.compare_digest(signature, hmac.digest(key, payload, "sha256")):
            raise InvalidAtomicMemoryPreviewError("the restoration preview signature is invalid")  # noqa: TRY003
        if (claims.scope_id, claims.subject, claims.operation, claims.artifact_id, claims.revision) != (
            scope_id,
            subject,
            operation,
            artifact_id,
            revision,
        ):
            raise InvalidAtomicMemoryPreviewError("the restoration preview does not match this request")  # noqa: TRY003
        if int(self._now().timestamp()) >= claims.expires_at:
            raise AtomicMemoryPreviewExpiredError("the restoration preview has expired")  # noqa: TRY003
        return claims.endpoint

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("preview clock must return an aware timestamp")  # noqa: TRY003
        return now.astimezone(UTC)


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _decode(value: str) -> bytes:
    raw = value.encode("ascii")
    return base64.b64decode(raw + b"=" * (-len(raw) % 4), altchars=b"-_", validate=True)
