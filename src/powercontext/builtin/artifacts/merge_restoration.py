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

"""Whole-group traversal and optional signed endpoint previews."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError

from powercontext.artifacts import Artifact, ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.merge_models import (
    ArtifactMergeErrors,
    ArtifactMergePlan,
    ArtifactMergeRead,
    ArtifactMergeRecord,
    ArtifactMergeRestoreItem,
    ArtifactMergeRestoreOperation,
    ArtifactMergeWrite,
)
from powercontext.builtin.persistence.artifact_governance import ArtifactLifecycleState


async def calculate_restoration(
    *,
    scope_id: str,
    subject: str,
    target: ArtifactMergeRecord,
    operation: ArtifactMergeRestoreOperation,
    selected: Artifact[Any] | None,
    load_current: Callable[[str], Awaitable[ArtifactMergeRecord]],
    load_inputs: Callable[[str], Awaitable[tuple[ArtifactRef, ...]]],
    adapter: Any,
    errors: ArtifactMergeErrors,
) -> ArtifactMergePlan:
    """Follow the current merge path, then interpret it from its endpoint inward."""

    if target.state.lifecycle_state is ArtifactLifecycleState.RETIRED:
        raise errors.invalid_state("retired Artifact identities cannot be restored")  # noqa: TRY003
    if operation == "undo_merge" and selected is not None:
        raise errors.invalid_state("undo_merge does not accept a content revision")  # noqa: TRY003
    records = {target.artifact.artifact_id: target}
    result_ids: list[str] = []
    cursor = target
    while cursor.state.merged_into_id is not None:
        next_id = cursor.state.merged_into_id
        if next_id is None or next_id in records:
            raise errors.relation("the current merge path is missing or cyclic")  # noqa: TRY003
        cursor = await load_current(next_id)
        if cursor.state.lifecycle_state is ArtifactLifecycleState.RETIRED:
            raise errors.relation("a live merge path cannot end in a retired result")  # noqa: TRY003
        records[next_id] = cursor
        result_ids.append(next_id)
    endpoint = cursor.as_read()
    undo_ids = list(reversed(result_ids))
    if operation == "undo_merge":
        undo_ids.append(target.artifact.artifact_id)
    final = await _expand_undo(undo_ids, records, load_current, load_inputs, target.ref.family, errors)
    target_id = target.artifact.artifact_id
    if operation == "restore":
        final[target_id] = ArtifactLifecycleState.ACTIVE
    writes, restored = _restoration_writes(final, records, target_id, selected, adapter)
    return ArtifactMergePlan(
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
    records: dict[str, ArtifactMergeRecord],
    load_current: Callable[[str], Awaitable[ArtifactMergeRecord]],
    load_inputs: Callable[[str], Awaitable[tuple[ArtifactRef, ...]]],
    family: str,
    errors: ArtifactMergeErrors,
) -> dict[str, ArtifactLifecycleState]:
    """Verify frozen input relations and compute the final state of the affected group."""

    final: dict[str, ArtifactLifecycleState] = {}
    for result_id in undo_ids:
        refs = await load_inputs(result_id)
        if (
            len(refs) < 2
            or len({ref.artifact_id for ref in refs}) != len(refs)
            or any(ref.family != family or ref.artifact_id == result_id for ref in refs)
        ):
            raise errors.relation("the result has no valid marked merge inputs")  # noqa: TRY003
        selected_ids = {ref.artifact_id for ref in refs}
        if any(
            record.state.merged_into_id == result_id and identity not in selected_ids
            for identity, record in records.items()
        ):
            raise errors.relation("the merge path input is absent from the result marked inputs")  # noqa: TRY003
        final[result_id] = ArtifactLifecycleState.RETIRED
        for ref in refs:
            current = records.get(ref.artifact_id)
            if current is None:
                current = await load_current(ref.artifact_id)
                records[ref.artifact_id] = current
            if (
                current.state.lifecycle_state is not ArtifactLifecycleState.DEPRECATED
                or current.state.merged_into_id != result_id
                or current.ref != ref
            ):
                raise errors.relation(  # noqa: TRY003
                    "merge inputs do not match their current destination and frozen version"
                )
            final[ref.artifact_id] = ArtifactLifecycleState.ACTIVE
    return final


def _restoration_writes(
    final: dict[str, ArtifactLifecycleState],
    records: dict[str, ArtifactMergeRecord],
    target_id: str,
    selected: Artifact[Any] | None,
    adapter: Any,
) -> tuple[list[ArtifactMergeWrite], list[ArtifactMergeRestoreItem]]:
    """Build only final changes; intermediate results never need projections."""

    writes: list[ArtifactMergeWrite] = []
    restored: list[ArtifactMergeRestoreItem] = []
    for artifact_id, state in sorted(final.items()):
        current = records[artifact_id]
        source = selected if artifact_id == target_id and selected is not None else current.artifact
        changed = (
            state != current.state.lifecycle_state
            or current.state.merged_into_id is not None
            or current.state.replacement_artifact_id is not None
            or (artifact_id == target_id and selected is not None)
        )
        draft = None
        if changed:
            # Historical lineage_only Sources stay bound to their historical
            # target; a precise historical Artifact reference retains evidence.
            refs = tuple(
                dict.fromkeys((ref.family, ref.artifact_id, ref.revision) for ref in (current.ref, source.as_ref()))
            )
            draft = adapter.draft(
                source.content,
                ArtifactLineage(
                    artifacts=tuple(
                        ArtifactRef(family=family, artifact_id=identity, revision=revision)
                        for family, identity, revision in refs
                    )
                ),
                historical=True,
            )
        if state is ArtifactLifecycleState.ACTIVE:
            restored.append(
                ArtifactMergeRestoreItem(
                    artifact_id=artifact_id,
                    source_revision=source.revision,
                    creates_revision=draft is not None,
                )
            )
        if changed:
            writes.append(ArtifactMergeWrite(artifact_id=artifact_id, state=state, draft=draft, current=current))
    return writes, restored


class _PreviewClaims(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    format: str
    key_id: str
    scope_id: str
    subject: str
    operation: ArtifactMergeRestoreOperation
    artifact_id: str
    revision: StrictInt | None
    endpoint: dict[str, Any]
    expires_at: StrictInt


class ArtifactMergePreviewSigner:
    """Sign previews with explicit shared keys; no per-process random fallback."""

    def __init__(
        self,
        *,
        keys: Mapping[str, bytes],
        active_key_id: str,
        ttl_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
        endpoint_type: type[BaseModel] = ArtifactMergeRead,
        preview_format: str = "powercontext.artifact.restoration-preview.v1",
        errors: ArtifactMergeErrors | None = None,
    ) -> None:
        if active_key_id not in keys or any(not key_id or len(secret) < 32 for key_id, secret in keys.items()):
            raise ValueError("preview keys need a configured active key and at least 32 secret bytes")  # noqa: TRY003
        if isinstance(ttl_seconds, bool) or ttl_seconds < 1:
            raise ValueError("preview TTL must be positive")  # noqa: TRY003
        self._endpoint_type = endpoint_type
        self._format = preview_format
        self._errors = errors or ArtifactMergeErrors()
        self._keys = dict(keys)
        self._active_key_id = active_key_id
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock or (lambda: datetime.now(UTC))

    def encode(self, plan: Any) -> tuple[str, datetime]:
        if plan.endpoint is None or plan.operation not in {"restore", "undo_merge"}:
            raise self._errors.invalid_preview("only restoration plans can be previewed")  # noqa: TRY003
        expires_at = self._now() + self._ttl
        claims = _PreviewClaims(
            format=self._format,
            key_id=self._active_key_id,
            scope_id=plan.scope_id,
            subject=plan.subject,
            operation=cast(ArtifactMergeRestoreOperation, plan.operation),
            artifact_id=plan.primary_artifact_id,
            revision=plan.target_revision,
            endpoint=plan.endpoint.model_dump(mode="json"),
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
        operation: ArtifactMergeRestoreOperation,
        artifact_id: str,
        revision: int | None,
    ) -> Any:
        if len(token) > 16_384:
            raise self._errors.invalid_preview("the restoration preview token is invalid")  # noqa: TRY003
        try:
            payload_part, signature_part = token.split(".")
            payload, signature = _decode(payload_part), _decode(signature_part)
            claims = _PreviewClaims.model_validate_json(payload, strict=True)
            key = self._keys[claims.key_id]
            endpoint = self._endpoint_type.model_validate_json(
                json.dumps(claims.endpoint, ensure_ascii=False), strict=True
            )
        except (ValueError, UnicodeError, binascii.Error, ValidationError, KeyError) as error:
            raise self._errors.invalid_preview("the restoration preview token is invalid") from error  # noqa: TRY003
        if claims.format != self._format:
            raise self._errors.invalid_preview("the restoration preview format is invalid")  # noqa: TRY003
        if not hmac.compare_digest(signature, hmac.digest(key, payload, "sha256")):
            raise self._errors.invalid_preview("the restoration preview signature is invalid")  # noqa: TRY003
        if (claims.scope_id, claims.subject, claims.operation, claims.artifact_id, claims.revision) != (
            scope_id,
            subject,
            operation,
            artifact_id,
            revision,
        ):
            raise self._errors.invalid_preview("the restoration preview does not match this request")  # noqa: TRY003
        if int(self._now().timestamp()) >= claims.expires_at:
            raise self._errors.preview_expired("the restoration preview has expired")  # noqa: TRY003
        return endpoint

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
