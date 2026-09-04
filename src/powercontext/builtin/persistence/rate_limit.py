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

"""Database-time fixed-window rate limiting for horizontally scaled APIs."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.database import database_now, insert_if_absent
from powercontext.builtin.persistence.errors import InvalidRepositoryArgumentError, InvalidStoredColumnError
from powercontext.builtin.persistence.tables import RATE_LIMIT_WINDOWS_TABLE


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    """One atomic counter decision."""

    allowed: bool
    remaining: int
    retry_after_seconds: int


class RateLimitRepository:
    """Increment one shared fixed window under a row lock."""

    async def consume(
        self,
        connection: AsyncConnection,
        *,
        principal_key: str,
        policy_id: str,
        limit: int,
        window_seconds: int,
    ) -> RateLimitDecision:
        _require_digest(principal_key)
        _require_text("policy_id", policy_id, 64)
        _require_positive("limit", limit)
        _require_positive("window_seconds", window_seconds)
        now = await database_now(connection)
        window_start = _window_start(now, window_seconds)
        expires_at = window_start + timedelta(seconds=window_seconds)
        row, created = await _lock_or_create_window(
            connection,
            principal_key=principal_key,
            policy_id=policy_id,
            window_start=window_start,
            expires_at=expires_at,
        )
        count = _positive_integer(row["request_count"], "request_count")
        retry_after = max(1, math.ceil((expires_at - now).total_seconds()))
        if created:
            return RateLimitDecision(
                allowed=True,
                remaining=max(limit - count, 0),
                retry_after_seconds=retry_after,
            )
        if count >= limit:
            return RateLimitDecision(allowed=False, remaining=0, retry_after_seconds=retry_after)
        next_count = count + 1
        await connection.execute(
            update(RATE_LIMIT_WINDOWS_TABLE)
            .where(
                RATE_LIMIT_WINDOWS_TABLE.c.principal_key == principal_key,
                RATE_LIMIT_WINDOWS_TABLE.c.policy_id == policy_id,
                RATE_LIMIT_WINDOWS_TABLE.c.window_started_at == window_start,
            )
            .values(request_count=next_count)
        )
        return RateLimitDecision(
            allowed=True,
            remaining=max(limit - next_count, 0),
            retry_after_seconds=retry_after,
        )

    async def purge_expired(self, connection: AsyncConnection, /, *, limit: int = 500) -> int:
        """Delete one bounded batch of expired fixed-window counters."""

        if limit < 1 or limit > 500:
            raise InvalidRepositoryArgumentError("limit", "must be between 1 and 500")
        now = await database_now(connection)
        rows = (
            await connection.execute(
                select(
                    RATE_LIMIT_WINDOWS_TABLE.c.principal_key,
                    RATE_LIMIT_WINDOWS_TABLE.c.policy_id,
                    RATE_LIMIT_WINDOWS_TABLE.c.window_started_at,
                )
                .where(RATE_LIMIT_WINDOWS_TABLE.c.expires_at <= now)
                .order_by(RATE_LIMIT_WINDOWS_TABLE.c.expires_at)
                .limit(limit)
            )
        ).all()
        deleted = 0
        for principal_key, policy_id, window_started_at in rows:
            result = await connection.execute(
                delete(RATE_LIMIT_WINDOWS_TABLE).where(
                    RATE_LIMIT_WINDOWS_TABLE.c.principal_key == principal_key,
                    RATE_LIMIT_WINDOWS_TABLE.c.policy_id == policy_id,
                    RATE_LIMIT_WINDOWS_TABLE.c.window_started_at == window_started_at,
                    RATE_LIMIT_WINDOWS_TABLE.c.expires_at <= now,
                )
            )
            deleted += result.rowcount
        return deleted


async def _lock_or_create_window(
    connection: AsyncConnection,
    *,
    principal_key: str,
    policy_id: str,
    window_start: datetime,
    expires_at: datetime,
) -> tuple[Mapping[Any, Any], bool]:
    row = await _lock_window(connection, principal_key, policy_id, window_start)
    if row is not None:
        return row, False
    created = await insert_if_absent(
        connection,
        RATE_LIMIT_WINDOWS_TABLE,
        {
            "principal_key": principal_key,
            "policy_id": policy_id,
            "window_started_at": window_start,
            "request_count": 1,
            "expires_at": expires_at,
        },
    )
    row = await _lock_window(connection, principal_key, policy_id, window_start)
    if row is None:
        raise InvalidStoredColumnError(  # noqa: TRY003
            "rate limit window",
            "an initialized fixed window",
        )
    return row, created


async def _lock_window(
    connection: AsyncConnection,
    principal_key: str,
    policy_id: str,
    window_start: datetime,
) -> Mapping[Any, Any] | None:
    await connection.execute(
        update(RATE_LIMIT_WINDOWS_TABLE)
        .where(
            RATE_LIMIT_WINDOWS_TABLE.c.principal_key == principal_key,
            RATE_LIMIT_WINDOWS_TABLE.c.policy_id == policy_id,
            RATE_LIMIT_WINDOWS_TABLE.c.window_started_at == window_start,
        )
        .values(request_count=RATE_LIMIT_WINDOWS_TABLE.c.request_count)
    )
    return (
        (
            await connection.execute(
                select(RATE_LIMIT_WINDOWS_TABLE)
                .where(
                    RATE_LIMIT_WINDOWS_TABLE.c.principal_key == principal_key,
                    RATE_LIMIT_WINDOWS_TABLE.c.policy_id == policy_id,
                    RATE_LIMIT_WINDOWS_TABLE.c.window_started_at == window_start,
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )


def _window_start(now: datetime, window_seconds: int) -> datetime:
    aware = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    epoch = int(aware.timestamp())
    return datetime.fromtimestamp(epoch - epoch % window_seconds, tz=UTC).replace(tzinfo=None)


def _require_digest(value: str) -> None:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise InvalidRepositoryArgumentError("principal_key", "must be a lowercase SHA-256 digest")


def _require_text(field: str, value: str, maximum: int) -> None:
    if not value.strip() or value != value.strip() or len(value) > maximum:
        raise InvalidRepositoryArgumentError(field, f"must be trimmed and contain at most {maximum} characters")


def _require_positive(field: str, value: int) -> None:
    if isinstance(value, bool) or value < 1:
        raise InvalidRepositoryArgumentError(field, "must be a positive integer")


def _positive_integer(value: object, column: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise InvalidStoredColumnError(column, "a positive integer")
    return value


__all__ = ["RateLimitDecision", "RateLimitRepository"]
