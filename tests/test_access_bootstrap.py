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

from __future__ import annotations

import asyncio

import pytest
from pymysql.err import IntegrityError as MySQLIntegrityError
from sqlalchemy.exc import IntegrityError

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.authz import (
    AccessAction,
    AccessAuditContext,
    AccessConflictError,
    PrincipalRef,
    ResourceRef,
)
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.authz.repository import RelationalAccessRepository


def test_access_bootstrap_replays_a_lost_unique_key_race(monkeypatch: pytest.MonkeyPatch) -> None:
    original = RelationalAccessRepository.create_binding
    lost = False

    async def create_binding(self, binding):
        nonlocal lost
        if not lost:
            lost = True
            raise AccessConflictError("binding-version") from IntegrityError(
                "INSERT", {}, MySQLIntegrityError(1062, "duplicate policy head")
            )
        return await original(self, binding)

    monkeypatch.setattr(RelationalAccessRepository, "create_binding", create_binding)

    async def scenario() -> None:
        principal = PrincipalRef(type="service", id="admin")
        async with open_builtin_access_control(SQLiteConfig(), bootstrap_administrators=(principal,)) as service:
            assert (
                await service.require(
                    principal,
                    AccessAction.SERVER_ADMIN,
                    ResourceRef.server("powercontext"),
                    context=AccessAuditContext(transport="http", operation="test", request_id="bootstrap"),
                )
            ).allowed

    asyncio.run(scenario())


@pytest.mark.parametrize("duplicate_key", [False, True])
def test_access_bootstrap_propagates_a_persistent_conflict(
    monkeypatch: pytest.MonkeyPatch, duplicate_key: bool
) -> None:
    attempts = 0
    error = AccessConflictError("idempotency-key")

    async def create_binding(self, binding):
        nonlocal attempts
        attempts += 1
        if duplicate_key:
            raise error from IntegrityError("INSERT", {}, MySQLIntegrityError(1062, "duplicate idempotency key"))
        raise error

    monkeypatch.setattr(RelationalAccessRepository, "create_binding", create_binding)

    async def scenario() -> None:
        with pytest.raises(AccessConflictError) as caught:
            async with open_builtin_access_control(
                SQLiteConfig(), bootstrap_administrators=(PrincipalRef(type="service", id="admin"),)
            ):
                pass
        assert caught.value is error
        assert attempts == (3 if duplicate_key else 1)

    asyncio.run(scenario())
