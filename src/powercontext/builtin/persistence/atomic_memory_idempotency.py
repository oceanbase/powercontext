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

"""Retry receipts for keyed ``remember_memory`` creations.

A receipt binds one namespace (operation, Scope, principal and caller key) to the
digest of the revision 1 content its creation stored. Only SHA-256 digests are
persisted: never the raw key, the principal ID, or the memory body.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256

import rfc8785
from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.atomic_memory.summary import default_description
from powercontext.builtin.artifacts.memory.canonical import canonical_error_code, normalize_text
from powercontext.builtin.artifacts.memory.errors import InvalidMemoryCandidateError
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.persistence.supervision import database_utc_now
from powercontext.builtin.persistence.tables import ATOMIC_MEMORY_IDEMPOTENCY_TABLE as TABLE

KEY_PATTERN = re.compile(r"[!-~]{1,256}")
RETENTION = timedelta(hours=24)
CLEANUP_BATCH = 32
DIGEST_VERSION = 1
OPERATION = "remember_memory"


@dataclass(frozen=True, slots=True)
class MemoryIdempotencyKey:
    """One keyed request: its namespace and the content it would store."""

    namespace_digest: str
    content: AtomicMemoryContent


@dataclass(frozen=True, slots=True)
class MemoryIdempotencyReceipt:
    """The creation a key is bound to, with the state recorded at creation."""

    artifact_id: str
    revision: int
    state_version: int


@dataclass(frozen=True, slots=True)
class _StoredReceipt:
    digest_version: int
    request_digest: str
    receipt: MemoryIdempotencyReceipt
    live: bool


def namespace_digest(scope_id: str, principal: tuple[str, str] | None, key: str) -> str:
    """Digest the key's namespace; an absent principal shares one namespace per Scope."""

    principal_type, principal_id = principal if principal is not None else ("", "")
    return sha256(rfc8785.dumps([OPERATION, scope_id, principal_type, principal_id, key])).hexdigest()


def request_digest(content: AtomicMemoryContent, version: int = DIGEST_VERSION) -> str | None:
    """Digest the revision 1 content the Atomic writer stores for this request.

    Only ``text`` is normalized, exactly as the writer normalizes it; other strings are
    hashed as submitted. Returns ``None`` for a version this binary cannot compute.
    """

    if version != 1:
        return None
    try:
        text = normalize_text(content.text)
    except (TypeError, ValueError) as error:
        raise InvalidMemoryCandidateError(
            "canonical", str(error), canonical_code=canonical_error_code(error)
        ) from error
    return sha256(
        rfc8785.dumps({
            "schema": content.schema_,
            "kind": content.kind,
            "text": text,
            "description": content.description if content.description is not None else default_description(text),
        })
    ).hexdigest()


class MemoryIdempotencyConflict(Exception):
    """The key is bound to a creation of different content."""


async def find_receipt(database: AsyncDatabase, key: MemoryIdempotencyKey) -> _StoredReceipt | None:
    """Read the key's record, live or expired, in its own read transaction."""

    async with database.transaction() as connection:
        now = await database_utc_now(connection)
        row = (
            await connection.execute(
                select(
                    TABLE.c.digest_version,
                    TABLE.c.request_digest,
                    TABLE.c.artifact_id,
                    TABLE.c.revision,
                    TABLE.c.state_version,
                    TABLE.c.expires_at,
                ).where(TABLE.c.namespace_digest == key.namespace_digest)
            )
        ).one_or_none()
    if row is None:
        return None
    return _StoredReceipt(
        digest_version=int(row.digest_version),
        request_digest=str(row.request_digest),
        receipt=MemoryIdempotencyReceipt(
            artifact_id=str(row.artifact_id), revision=int(row.revision), state_version=int(row.state_version)
        ),
        live=row.expires_at > now,
    )


def replay(stored: _StoredReceipt, key: MemoryIdempotencyKey) -> MemoryIdempotencyReceipt:
    """Return the bound creation for the same content, or raise a conflict."""

    if request_digest(key.content, stored.digest_version) != stored.request_digest:
        raise MemoryIdempotencyConflict
    return stored.receipt


async def bind_receipt(
    connection: AsyncConnection,
    key: MemoryIdempotencyKey,
    scope_id: str,
    receipt: MemoryIdempotencyReceipt,
    *,
    replace_expired: bool,
) -> None:
    """Claim the key inside the creating transaction.

    An expired record is removed only when the lookup saw one, and only while it is still
    expired, so a live key committed concurrently is never removed. A concurrent claim of
    the same key fails on the primary key.
    """

    now = await database_utc_now(connection)
    if replace_expired:
        await connection.execute(
            delete(TABLE).where(TABLE.c.namespace_digest == key.namespace_digest, TABLE.c.expires_at <= now)
        )
    await connection.execute(
        insert(TABLE).values(
            namespace_digest=key.namespace_digest,
            digest_version=DIGEST_VERSION,
            request_digest=request_digest(key.content),
            scope_id=scope_id,
            artifact_id=receipt.artifact_id,
            revision=receipt.revision,
            state_version=receipt.state_version,
            created_at=now,
            expires_at=now + RETENTION,
        )
    )


async def remove_expired_receipts(database: AsyncDatabase) -> None:
    """Delete at most one bounded batch of expired records through the expiry index."""

    async with database.transaction() as connection:
        now = await database_utc_now(connection)
        expired = (
            await connection.scalars(
                select(TABLE.c.namespace_digest)
                .where(TABLE.c.expires_at <= now)
                .order_by(TABLE.c.expires_at)
                .limit(CLEANUP_BATCH)
            )
        ).all()
        if expired:
            await connection.execute(
                delete(TABLE).where(TABLE.c.namespace_digest.in_(expired), TABLE.c.expires_at <= now)
            )
