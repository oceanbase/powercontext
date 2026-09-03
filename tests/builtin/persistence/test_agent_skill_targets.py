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

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from powercontext.builtin.persistence import (
    RemoteAgentSkillTarget,
    RemoteAgentSkillTargetRepository,
    RemoteAgentSkillTargetState,
    StoredPayloadConflictError,
)
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.tables import BUILTIN_TABLES


def _pending_target(
    *,
    scope_id: str = "project:one",
    target_id: str = "codex-a",
    token_digest: str = "a" * 64,
) -> RemoteAgentSkillTarget:
    now = datetime.now(UTC)
    return RemoteAgentSkillTarget(
        scope_id=scope_id,
        target_id=target_id,
        display_name="Test machine",
        agent_kind="codex",
        state=RemoteAgentSkillTargetState.PENDING,
        enrollment_token_digest=token_digest,
        enrollment_expires_at=now + timedelta(minutes=10),
        generation=0,
        created_at=now,
        updated_at=now,
    )


def test_remote_agent_skill_target_enrollment_is_credential_addressable_and_cas_guarded() -> None:
    async def exercise() -> None:
        repository = RemoteAgentSkillTargetRepository()
        pending = _pending_target()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await repository.create(connection, pending)
            restored_pending = await repository.find_by_enrollment_token(connection, "a" * 64)
            assert restored_pending is not None
            assert restored_pending.target_id == pending.target_id
            assert restored_pending.state is RemoteAgentSkillTargetState.PENDING

            active_payload = pending.model_copy(
                update={
                    "state": RemoteAgentSkillTargetState.ACTIVE,
                    "installation_id": "workspace-8d3a",
                    "enrollment_token_digest": None,
                    "enrollment_expires_at": None,
                    "credential_subject": "installation-01",
                    "credential_verifier": "b" * 64,
                    "receiver_version": "0.1.0",
                }
            )
            active = await repository.replace(connection, active_payload, 0)

            assert active.generation == 1
            assert active.updated_at >= pending.updated_at
            assert await repository.find_by_enrollment_token(connection, "a" * 64) is None
            restored_active = await repository.find_by_credential(connection, "b" * 64)
            assert restored_active is not None
            assert restored_active.target_id == active.target_id
            assert restored_active.installation_id == "workspace-8d3a"

            with pytest.raises(StoredPayloadConflictError):
                await repository.replace(connection, active, 0)

    asyncio.run(exercise())


def test_remote_agent_skill_target_rejects_duplicate_installation_identity() -> None:
    async def exercise() -> None:
        repository = RemoteAgentSkillTargetRepository()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            first = _pending_target()
            second = _pending_target(target_id="codex-b", token_digest="c" * 64)
            await repository.create(connection, first)
            await repository.create(connection, second)

            for pending, subject, verifier in (
                (first, "installation-01", "b" * 64),
                (second, "installation-02", "d" * 64),
            ):
                active = pending.model_copy(
                    update={
                        "state": RemoteAgentSkillTargetState.ACTIVE,
                        "installation_id": "workspace-8d3a",
                        "enrollment_token_digest": None,
                        "enrollment_expires_at": None,
                        "credential_subject": subject,
                        "credential_verifier": verifier,
                    }
                )
                if pending is first:
                    await repository.replace(connection, active, 0)
                else:
                    with pytest.raises(StoredPayloadConflictError):
                        await repository.replace(connection, active, 0)

    asyncio.run(exercise())


def test_remote_agent_skill_targets_are_listed_by_scope_with_a_bound() -> None:
    async def exercise() -> None:
        repository = RemoteAgentSkillTargetRepository()
        async with (
            SQLiteProfile.open(SQLiteConfig(), tables=BUILTIN_TABLES) as profile,
            profile.database.transaction() as connection,
        ):
            await repository.create(connection, _pending_target(target_id="codex-a", token_digest="a" * 64))
            await repository.create(connection, _pending_target(target_id="codex-b", token_digest="b" * 64))
            await repository.create(
                connection,
                _pending_target(scope_id="project:other", target_id="codex-c", token_digest="c" * 64),
            )

            first = await repository.list_for_scope(connection, "project:one", limit=1)
            all_in_scope = await repository.list_for_scope(connection, "project:one", limit=10)

            assert [target.target_id for target in first] == ["codex-a"]
            assert [target.target_id for target in all_in_scope] == ["codex-a", "codex-b"]

    asyncio.run(exercise())
