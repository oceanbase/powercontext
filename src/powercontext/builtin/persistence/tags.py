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

"""Transactional, scope-local tag storage over a single relational table."""

from __future__ import annotations

import base64
import binascii
import hmac
import json
import secrets
from collections.abc import Callable
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, cast

import rfc8785
from sqlalchemy import ColumnElement, and_, delete, func, insert, literal, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.artifacts import Artifact, ArtifactRef
from powercontext.builtin.persistence.artifacts import ArtifactRepository
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.errors import RepositoryNotFoundError
from powercontext.builtin.persistence.tables import ARTIFACT_HEADS_TABLE, ARTIFACT_TAGS_TABLE
from powercontext.builtin.records import (
    BaseValueNotFoundError,
    CursorExpiredError,
    InvalidCursorError,
)
from powercontext.builtin.tags import (
    ArtifactTagSet,
    ArtifactTagTarget,
    TagFilter,
    TaggedTarget,
    TagPreconditionError,
    TagQuery,
    TagQueryPage,
    TagTarget,
    normalize_tags,
    tag_set,
)


def tag_predicate(
    scope_id: str,
    family: str | ColumnElement[str],
    artifact_id: ColumnElement[str],
    target_type: str,
    target_id: ColumnElement[str],
    tag_filter: TagFilter,
) -> ColumnElement[bool]:
    """A correlated exact match suitable for use *before* LIMIT/top-k."""

    tags = ARTIFACT_TAGS_TABLE.alias()
    count = (
        select(func.count())
        .where(
            tags.c.scope_id == scope_id,
            tags.c.family == family,
            tags.c.artifact_id == artifact_id,
            tags.c.target_type == target_type,
            tags.c.target_id == target_id,
            tags.c.tag_key_hash.in_(_key_hashes(tag_filter)),
            tags.c.tag_key.in_(tag_filter.keys),
        )
        .correlate_except(tags)
        .scalar_subquery()
    )
    return count == len(tag_filter.keys) if tag_filter.match == "all" else count > 0


def _key_hashes(tag_filter: TagFilter) -> tuple[bytes, ...]:
    return tuple(sha256(key.encode("utf-8")).digest() for key in tag_filter.keys)


def _identity(scope_id: str, target: TagTarget) -> dict[str, str]:
    return {
        "scope_id": scope_id,
        "family": target.family,
        "artifact_id": target.artifact_id,
        "target_type": target.type,
        "target_id": target.artifact_id,
    }


def _where(identity: dict[str, str]) -> ColumnElement[bool]:
    return and_(*(ARTIFACT_TAGS_TABLE.c[key] == value for key, value in identity.items()))


async def _begin_read_snapshot(connection: AsyncConnection) -> None:
    # sqlite3's legacy mode does not begin a read transaction for SELECT. Pin
    # the head, manifest, lifecycle and tag reads to one snapshot.
    if connection.dialect.name == "sqlite":
        await connection.exec_driver_sql("BEGIN")


class RelationalTagService:
    """Tag writes serialize on the owning head, even for an empty tag set."""

    def __init__(
        self,
        database: AsyncDatabase,
        artifacts: ArtifactRepository,
        *,
        cursor_secret: bytes | None = None,
        clock: Callable[[], datetime] | None = None,
        cursor_ttl_seconds: int = 3600,
        projection_hook=None,
        atomic_write_authorizer=None,
    ) -> None:
        self._database = database
        self._artifacts = artifacts
        self._cursor_secret = secrets.token_bytes(32) if cursor_secret is None else cursor_secret
        self._clock = (lambda: datetime.now(UTC)) if clock is None else clock
        self._cursor_ttl = cursor_ttl_seconds
        self._projection_hook = projection_hook
        self._atomic_write_authorizer = atomic_write_authorizer

    async def get(self, scope_id: str, target: TagTarget) -> ArtifactTagSet:
        async with self._database.transaction() as connection:
            await _begin_read_snapshot(connection)
            await self._target_reference(connection, scope_id, target)
            current = await self._read(connection, scope_id, target)
            return tag_set(scope_id, target, current.tags)

    async def replace(
        self, scope_id: str, target: TagTarget, tags: tuple[str, ...], *, expected_etag: str, execution_context=None
    ) -> ArtifactTagSet:
        async with self._database.transaction() as connection:
            return await self.replace_in_transaction(
                connection, scope_id, target, tags, expected_etag=expected_etag, execution_context=execution_context
            )

    async def lock_target(self, connection: AsyncConnection, scope_id: str, target: TagTarget) -> None:
        locked = await connection.execute(
            update(ARTIFACT_HEADS_TABLE)
            .where(
                ARTIFACT_HEADS_TABLE.c.scope_id == scope_id,
                ARTIFACT_HEADS_TABLE.c.family == target.family,
                ARTIFACT_HEADS_TABLE.c.artifact_id == target.artifact_id,
            )
            .values(revision=ARTIFACT_HEADS_TABLE.c.revision)
        )
        if locked.rowcount != 1:
            raise BaseValueNotFoundError("artifact", target)

    async def read_in_transaction(
        self, connection: AsyncConnection, scope_id: str, target: TagTarget
    ) -> ArtifactTagSet:
        return await self._read(connection, scope_id, target, current=True)

    async def replace_in_transaction(
        self,
        connection: AsyncConnection,
        scope_id: str,
        target: TagTarget,
        tags: tuple[str, ...],
        *,
        expected_etag: str,
        execution_context=None,
    ) -> ArtifactTagSet:
        desired = normalize_tags(tags)
        await self.lock_target(connection, scope_id, target)
        await self._target_reference(connection, scope_id, target)
        if target.family == "atomic-memory" and self._atomic_write_authorizer is not None:
            await self._atomic_write_authorizer(connection, scope_id, target.artifact_id, execution_context)
        latest = await self._read(connection, scope_id, target, current=True)
        current = tag_set(scope_id, target, latest.tags)
        if not hmac.compare_digest(expected_etag.encode("utf-8"), current.etag.encode("utf-8")):
            raise TagPreconditionError
        previous = normalize_tags(current.tags)
        identity = _identity(scope_id, target)
        removed = previous.keys() - desired.keys()
        if removed:
            await connection.execute(
                delete(ARTIFACT_TAGS_TABLE).where(_where(identity), ARTIFACT_TAGS_TABLE.c.tag_key.in_(removed))
            )
        assigned_at = self._clock()
        for key, label in desired.items():
            if key not in previous:
                await connection.execute(
                    insert(ARTIFACT_TAGS_TABLE).values(
                        **identity,
                        tag_key=key,
                        tag_key_hash=sha256(key.encode("utf-8")).digest(),
                        tag=label,
                        assigned_at=assigned_at,
                    )
                )
            elif label != previous[key]:
                await connection.execute(
                    update(ARTIFACT_TAGS_TABLE)
                    .where(_where(identity), ARTIFACT_TAGS_TABLE.c.tag_key == key)
                    .values(tag=label)
                )
        if target.family == "atomic-memory" and self._projection_hook is not None:
            await self._projection_hook(connection, scope_id, target.artifact_id, tuple(desired))
        return tag_set(scope_id, target, tags)

    async def query(self, scope_id: str, query: TagQuery, *, caller: str = "runtime") -> TagQueryPage:
        binding = sha256(
            rfc8785.dumps({
                "scope_id": scope_id,
                "keys": list(query.keys),
                "match": query.match,
                "families": sorted(query.families),
                "target_types": sorted(query.target_types),
                "include_inactive": query.include_inactive,
                "caller": caller,
            })
        ).hexdigest()
        after = self._decode_cursor(query.cursor, binding)
        table = ARTIFACT_TAGS_TABLE
        heads = ARTIFACT_HEADS_TABLE
        order = (table.c.family, table.c.target_type, table.c.artifact_id, table.c.target_id)
        items: list[TaggedTarget] = []
        keys: list[tuple[str, ...]] = []
        async with self._database.transaction() as connection:
            await _begin_read_snapshot(connection)
            statement = (
                select(*order, heads.c.revision)
                .select_from(
                    table.join(
                        heads,
                        and_(
                            heads.c.scope_id == table.c.scope_id,
                            heads.c.family == table.c.family,
                            heads.c.artifact_id == table.c.artifact_id,
                        ),
                    )
                )
                .where(
                    table.c.scope_id == scope_id,
                    table.c.tag_key_hash.in_(_key_hashes(query)),
                    table.c.tag_key.in_(query.keys),
                    table.c.family.in_(query.families),
                    table.c.target_type.in_(query.target_types),
                    tuple_(*order) > tuple_(*(literal(value) for value in after)),
                )
                .group_by(*order, heads.c.revision)
                .having(func.count() == len(query.keys) if query.match == "all" else func.count() > 0)
                .order_by(*order)
                .limit(query.limit + 1)
            )
            if not query.include_inactive:
                statement = statement.where(heads.c.lifecycle_state == "active")
            rows = (await connection.execute(statement)).all()
            artifacts: dict[tuple[str, str], Artifact[Any]] = {}
            for row in rows[: query.limit]:
                key = tuple(str(value) for value in row[:4])
                family, _target_type, artifact_id, _target_id = key
                target = ArtifactTagTarget(family=cast(Any, family), artifact_id=artifact_id)
                head_key = (family, artifact_id)
                if head_key not in artifacts:
                    ref = ArtifactRef(family=family, artifact_id=artifact_id, revision=int(row[4]))
                    artifacts[head_key] = await self._repository(family).get(connection, scope_id, ref)
                reference = artifacts[head_key].as_ref()
                labels = await self._read(connection, scope_id, target)
                items.append(TaggedTarget(**labels.model_dump(), reference=reference))
                keys.append(key)
        cursor = self._encode_cursor(keys[-1], binding) if len(rows) > query.limit else None
        return TagQueryPage(items=tuple(items), next_cursor=cursor)

    async def _read(
        self, connection: AsyncConnection, scope_id: str, target: TagTarget, *, current=False
    ) -> ArtifactTagSet:
        statement = select(ARTIFACT_TAGS_TABLE.c.tag).where(_where(_identity(scope_id, target)))
        if current:
            statement = statement.with_for_update()
        labels = await connection.scalars(statement)
        return tag_set(scope_id, target, tuple(labels))

    def _repository(self, family: str) -> Any:
        from powercontext.builtin.plugins.families import is_plugin_family, plugin_artifact_repository

        # Plugin families are registered per process, not composed into the shared repository.
        return plugin_artifact_repository(family) if is_plugin_family(family) else self._artifacts

    async def _target_reference(self, connection: AsyncConnection, scope_id: str, target: TagTarget) -> ArtifactRef:
        try:
            artifact = await self._repository(target.family).latest(
                connection, scope_id, target.family, target.artifact_id
            )
        except RepositoryNotFoundError:
            raise BaseValueNotFoundError("artifact", target) from None
        return artifact.as_ref()

    def _encode_cursor(self, after: tuple[str, ...], binding: str) -> str:
        payload = rfc8785.dumps({
            "after": list(after),
            "binding": binding,
            "expires": int(self._clock().timestamp()) + self._cursor_ttl,
        })
        signature = hmac.digest(self._cursor_secret, payload, "sha256")
        return base64.urlsafe_b64encode(signature + payload).decode("ascii")

    def _decode_cursor(self, cursor: str | None, binding: str) -> tuple[str, ...]:
        if cursor is None:
            return ("", "", "", "")
        try:
            raw = base64.b64decode(cursor, altchars=b"-_", validate=True)
            signature, payload = raw[:32], raw[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._cursor_secret, payload, "sha256")):
                raise InvalidCursorError
            value = json.loads(payload)
            if (
                not isinstance(value, dict)
                or set(value) != {"after", "binding", "expires"}
                or value["binding"] != binding
            ):
                raise InvalidCursorError
            after = value["after"]
            if not isinstance(after, list) or len(after) != 4 or not all(isinstance(item, str) for item in after):
                raise InvalidCursorError
            if not isinstance(value["expires"], int):
                raise InvalidCursorError
            if value["expires"] <= self._clock().timestamp():
                raise CursorExpiredError
            return tuple(after)
        except (ValueError, UnicodeError, binascii.Error) as error:
            raise InvalidCursorError from error
