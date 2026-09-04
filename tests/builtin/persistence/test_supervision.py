# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from powercontext.builtin.persistence.errors import ArtifactProcessingLeadershipLostError
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.persistence.tables import ARTIFACT_PROCESSING_LEASES_TABLE, SHARED_TABLES


def test_single_process_terms_increment_and_fence_stale_workers() -> None:
    async def scenario() -> None:
        leases = ArtifactProcessingLeaseRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await leases.start_single_process_term(connection, "holder-a")
            async with profile.database.transaction() as connection:
                second = await leases.start_single_process_term(connection, "holder-b")

            assert first.supervisor_generation == 1
            assert first.lease_expires_at is None
            assert second.supervisor_generation == 2

            async with profile.database.transaction() as connection:
                with pytest.raises(ArtifactProcessingLeadershipLostError):
                    await leases.require_fence(connection, first.fence("single-process"))
                assert await leases.require_fence(connection, second.fence("single-process")) == second

    asyncio.run(scenario())


def test_expiring_lease_acquisition_renewal_and_database_time_binding_state() -> None:
    async def scenario() -> None:
        leases = ArtifactProcessingLeaseRepository()
        states = ArtifactProcessingBindingStateRepository()
        async with SQLiteProfile.open(SQLiteConfig(), tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                first = await leases.try_acquire(connection, "holder-a", 60)
            assert first is not None
            assert first.supervisor_generation == 1

            async with profile.database.transaction() as connection:
                assert await leases.try_acquire(connection, "holder-b", 60) is None
                await connection.execute(
                    update(ARTIFACT_PROCESSING_LEASES_TABLE)
                    .where(ARTIFACT_PROCESSING_LEASES_TABLE.c.supervisor_group == "global")
                    .values(lease_expires_at=datetime(2000, 1, 1, tzinfo=UTC).replace(tzinfo=None))
                )

            async with profile.database.transaction() as connection:
                second = await leases.try_acquire(connection, "holder-b", 60)
            assert second is not None
            assert second.supervisor_generation == 2

            async with profile.database.transaction() as connection:
                renewed = await leases.renew(connection, second.fence("oceanbase"), 120)
                completed = await states.mark_auto_wave_completed(connection, "topic-memory-source-window")
            assert renewed.supervisor_generation == second.supervisor_generation
            assert renewed.lease_expires_at is not None
            assert second.lease_expires_at is not None
            assert renewed.lease_expires_at > second.lease_expires_at
            assert completed.last_auto_wave_completed_at is not None
            now = datetime.now(UTC).replace(tzinfo=None)
            assert now - completed.last_auto_wave_completed_at < timedelta(seconds=5)

    asyncio.run(scenario())
