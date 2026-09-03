# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Runtime role membership and single-node ownership leases."""

from __future__ import annotations

import asyncio
import hashlib
from contextlib import suppress
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from powercontext.builtin.persistence.coordination import (
    CoordinationRepository,
    CoordinatorLease,
    RuntimeMemberSpec,
)
from powercontext.builtin.persistence.database import AsyncDatabase
from powercontext.builtin.runtime.config import CoordinationConfig, DeploymentConfig
from powercontext.builtin.runtime.readiness import ReadinessCheckStatus
from powercontext.builtin.runtime.work_observability import WorkObserver

_SINGLE_NODE_LEASE = "single-node-runtime"
_SCHEMA_VERSION = 2
_PAYLOAD_VERSION = 1


class DuplicateSingleNodeError(RuntimeError):
    """Raised before serving when another single-node runtime owns the database."""

    def __init__(self) -> None:
        super().__init__("another single-node runtime already owns this database")


class RuntimeMembership:
    """Advertise compatibility and maintain exclusive local ownership."""

    def __init__(
        self,
        *,
        database: AsyncDatabase,
        deployment: DeploymentConfig,
        coordination: CoordinationConfig,
        build_version: str,
        repository: CoordinationRepository | None = None,
        observer: WorkObserver | None = None,
    ) -> None:
        self._database = database
        self._deployment = deployment
        self._config = coordination
        self._build_version = build_version
        self._repository = CoordinationRepository() if repository is None else repository
        self._observer = observer
        boot_id = uuid4().hex
        self._member_id = hashlib.sha256(f"{deployment.id}\0{boot_id}".encode()).hexdigest()
        self._single_node_lease: CoordinatorLease | None = None
        self._stop_requested = asyncio.Event()
        self._failed = False

    @property
    def instance_id(self) -> str:
        """Return the boot-unique, non-sensitive owner used for fencing."""

        return self._member_id

    async def start(self) -> None:
        """Acquire required ownership and publish the first heartbeat."""

        async with self._database.transaction() as connection:
            if self._deployment.mode == "single_node":
                self._single_node_lease = await self._repository.acquire_lease(
                    connection,
                    lease_name=_SINGLE_NODE_LEASE,
                    owner_id=self._member_id,
                    lease_seconds=self._config.member_ttl_seconds,
                )
                if self._single_node_lease is None:
                    raise DuplicateSingleNodeError
            await self._heartbeat(connection)

    async def run(self) -> None:
        """Renew role and optional owner leases until shutdown."""

        try:
            while not self._stop_requested.is_set():
                with suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._stop_requested.wait(),
                        timeout=self._config.member_heartbeat_seconds,
                    )
                if self._stop_requested.is_set():
                    return
                async with self._database.transaction() as connection:
                    if self._single_node_lease is not None:
                        renewed = await self._repository.acquire_lease(
                            connection,
                            lease_name=_SINGLE_NODE_LEASE,
                            owner_id=self._member_id,
                            lease_seconds=self._config.member_ttl_seconds,
                        )
                        self._single_node_lease = _validated_renewal(self._single_node_lease, renewed)
                    await self._heartbeat(connection)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed = True
            raise

    async def stop(self) -> None:
        """Stop heartbeats and conditionally release single-node ownership."""

        self._stop_requested.set()
        lease = self._single_node_lease
        self._single_node_lease = None
        if lease is not None:
            async with self._database.transaction() as connection:
                await self._repository.release_lease(connection, lease)

    async def readiness(self) -> str:
        """Verify this member is still live and compatible with its peers."""

        if self._failed:
            return ReadinessCheckStatus.UNAVAILABLE
        async with self._database.transaction() as connection:
            members = await self._repository.live_members(connection)
        current = next((member for member in members if member.member_id == self._member_id), None)
        if current is None:
            return ReadinessCheckStatus.UNAVAILABLE
        if any(
            member.behavior_revision != self._deployment.behavior_revision
            or member.schema_min > _SCHEMA_VERSION
            or member.schema_max < _SCHEMA_VERSION
            or member.payload_min > _PAYLOAD_VERSION
            or member.payload_max < _PAYLOAD_VERSION
            for member in members
        ):
            return ReadinessCheckStatus.MISCONFIGURED
        return ReadinessCheckStatus.READY

    async def role_readiness(self, role: str) -> str:
        """Report whether at least one compatible live member serves ``role``."""

        async with self._database.transaction() as connection:
            members = await self._repository.live_members(connection)
        return (
            ReadinessCheckStatus.READY
            if any(
                member.role in {role, "all"} and member.behavior_revision == self._deployment.behavior_revision
                for member in members
            )
            else ReadinessCheckStatus.UNAVAILABLE
        )

    async def _heartbeat(self, connection: AsyncConnection) -> None:
        await self._repository.heartbeat_member(
            connection,
            RuntimeMemberSpec(
                member_id=self._member_id,
                role=self._deployment.role,
                build_version=self._build_version,
                schema_min=_SCHEMA_VERSION,
                schema_max=_SCHEMA_VERSION,
                payload_min=_PAYLOAD_VERSION,
                payload_max=_PAYLOAD_VERSION,
                behavior_revision=self._deployment.behavior_revision,
            ),
            ttl_seconds=self._config.member_ttl_seconds,
        )
        if self._observer is not None:
            members = await self._repository.live_members(connection)
            counts = dict.fromkeys(("all", "api", "scheduler", "worker"), 0)
            for member in members:
                counts[member.role] += 1
            with suppress(Exception):
                self._observer.set_runtime_members(counts)


def _validated_renewal(current: CoordinatorLease, renewed: CoordinatorLease | None) -> CoordinatorLease:
    if renewed is None or renewed.fence != current.fence:
        raise DuplicateSingleNodeError
    return renewed


__all__ = ["DuplicateSingleNodeError", "RuntimeMembership"]
