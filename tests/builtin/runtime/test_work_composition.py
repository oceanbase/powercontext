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

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from time import monotonic
from types import SimpleNamespace
from typing import Literal, cast

from sqlalchemy import func, insert, select

from powercontext.builtin.persistence.rate_limit import RATE_LIMIT_WINDOWS_TABLE
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.work import WorkRepository
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.builtin.runtime.artifact_processing import ArtifactProcessingBinding
from powercontext.builtin.runtime.composition import (
    _ledger_families,
    _open_work_background,
    _supervisor_bindings,
    _work_authorizer,
    _work_background_requested,
    _work_discoverers,
    _work_handlers,
    _work_role_components,
)
from powercontext.builtin.runtime.config import (
    CoordinationConfig,
    DeploymentConfig,
    OperationsConfig,
    RuntimeConfig,
    WorkerConfig,
)
from powercontext.builtin.runtime.relational import RelationalContexts
from powercontext.builtin.runtime.work_handlers import (
    EXPERIENCE_WORK_KIND,
    INTERNAL_MAINTENANCE_SCOPE,
    MAINTENANCE_WORK_KIND,
    MEMORY_WORK_KIND,
    PROFILE_WORK_KIND,
    memory_work_spec,
)
from powercontext.builtin.sources import ContentCapture


def _config(tmp_path, **overrides):
    return BuiltinConfig(
        database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'work.db'}"),
        **overrides,
    )


def _distributed(runtime: RuntimeConfig, role: Literal["api", "scheduler", "worker"] = "worker") -> BuiltinConfig:
    """A distributed config without an OceanBase handle: only top-level helpers read it."""

    return cast(
        "BuiltinConfig",
        SimpleNamespace(
            deployment=DeploymentConfig(mode="distributed", role=role),
            runtime=runtime,
        ),
    )


def test_ledger_only_starts_for_a_distributed_deployment() -> None:
    # A single-node install keeps its APScheduler supervisors: RFC 1430 moves
    # single_node/all onto the ledger only once that path has fully retired.
    assert _work_background_requested(BuiltinConfig()) is False
    assert _work_background_requested(BuiltinConfig(runtime=RuntimeConfig(memory_schedule_seconds=30))) is False
    assert _work_background_requested(BuiltinConfig(runtime=RuntimeConfig(profile_schedule_enabled=True))) is False
    assert _work_background_requested(_distributed(RuntimeConfig(memory_schedule_seconds=30))) is True


def test_ledger_owned_families_follow_the_configured_schedules() -> None:
    assert _ledger_families(BuiltinConfig(runtime=RuntimeConfig(memory_schedule_seconds=30))) == frozenset()

    families = _ledger_families(
        _distributed(
            RuntimeConfig(
                memory_schedule_seconds=30,
                experience_schedule_seconds=30,
                profile_schedule_enabled=True,
            )
        )
    )
    assert families == {"memory", "experience", "profile"}

    # An unscheduled Family stays with the supervisor even in a distributed run.
    assert _ledger_families(_distributed(RuntimeConfig(memory_schedule_seconds=30))) == {"memory"}


def test_supervisor_drops_only_the_families_the_ledger_owns() -> None:
    fakes = [SimpleNamespace(artifact_family=family) for family in ("memory", "experience", "skill")]
    bindings = cast("list[ArtifactProcessingBinding]", fakes)
    config = _distributed(RuntimeConfig(memory_schedule_seconds=30, profile_schedule_enabled=True))

    kept = _supervisor_bindings(config, bindings)
    assert [binding.artifact_family for binding in kept] == ["experience", "skill"]

    # A single-node config owns nothing, so every binding reaches the supervisor.
    assert len(_supervisor_bindings(BuiltinConfig(), bindings)) == 3


def test_work_roles_split_scanning_from_claiming() -> None:
    assert _work_role_components("all") == (True, True)
    assert _work_role_components("api") == (False, False)
    assert _work_role_components("scheduler") == (True, False)
    assert _work_role_components("worker") == (False, True)


def test_composition_registers_every_discoverer_and_handler(tmp_path) -> None:
    sentinel = object()
    config = _config(
        tmp_path,
        runtime=RuntimeConfig(
            memory_schedule_seconds=30,
            experience_schedule_seconds=30,
            profile_schedule_enabled=True,
        ),
    )

    discoverers = _work_discoverers(config, cast("RelationalContexts", sentinel))
    assert [discoverer.name for discoverer in discoverers] == [
        MEMORY_WORK_KIND,
        EXPERIENCE_WORK_KIND,
        PROFILE_WORK_KIND,
        MAINTENANCE_WORK_KIND,
    ]

    class _Profiles:
        """Stand-in for the shared Profile service the handler only stores."""

    fake_contexts = cast("RelationalContexts", type("Contexts", (), {"profiles": _Profiles()})())
    handlers = _work_handlers(config, fake_contexts)
    assert [handler.kind for handler in handlers] == [
        MEMORY_WORK_KIND,
        EXPERIENCE_WORK_KIND,
        PROFILE_WORK_KIND,
        MAINTENANCE_WORK_KIND,
    ]


def test_single_node_runtime_exposes_operations_without_ledger_execution(tmp_path) -> None:
    async def scenario() -> None:
        config = _config(tmp_path, runtime=RuntimeConfig(memory_schedule_seconds=30))
        async with (
            open_builtin_contexts(config) as contexts,
            _open_work_background(config, contexts) as background,
        ):
            assert background.operations is not None
            # The supervisor owns every Family here, so the ledger stays idle.
            assert background.scheduler is None
            assert background.worker is None
            assert background.membership is None

    asyncio.run(scenario())


class _RecordingObserver:
    """Record-only WorkObserver standing in for the ServerMetrics registry."""

    def __init__(self) -> None:
        self.enqueues: list[tuple[str, bool]] = []
        self.queue_samples: object = None

    def observe_work_enqueue(self, kind: str, *, created: bool) -> None:
        self.enqueues.append((kind, created))

    def observe_work_claim(self, kind: str, *, latency_seconds: float) -> None:
        pass

    def observe_work_attempt(
        self,
        kind: str,
        *,
        outcome: str,
        error_category: str,
        duration_seconds: float,
    ) -> None:
        pass

    def observe_work_lease_expiry(self, kind: str, *, outcome: str) -> None:
        pass

    def observe_scheduler_leadership(self, *, outcome: str) -> None:
        pass

    def set_work_queue(self, samples) -> None:
        self.queue_samples = list(samples)

    def set_runtime_members(self, counts) -> None:
        pass


def test_operations_refresh_the_queue_gauges_through_the_wired_observer(tmp_path) -> None:
    async def scenario() -> None:
        observer = _RecordingObserver()
        config = _config(tmp_path, runtime=RuntimeConfig(memory_schedule_seconds=30))
        async with (
            open_builtin_contexts(config) as contexts,
            _open_work_background(config, contexts, observer=observer) as background,
        ):
            assert background.operations is not None
            context = await contexts.get("project")
            await context.sources.capture(ContentCapture(source_id="one", content="remember this"))
            spec = await memory_work_spec(contexts, "project", limit=10, max_attempts=3, payload_version=1)
            assert spec is not None

            repository = WorkRepository(observer=observer)
            async with contexts.database.transaction() as connection:
                await repository.enqueue(connection, spec)
            assert observer.enqueues == [(MEMORY_WORK_KIND, True)]

            page = await background.operations.list(
                scope_id="project", kind=MEMORY_WORK_KIND, status=None, cursor=None, limit=10
            )
            stored = page.items[0]
            await background.operations.cancel(stored.work_id, expected_version=stored.state_version)
            # The cancel path refreshes the durable queue gauges through the
            # same observer the production composition wired in.
            assert observer.queue_samples is not None

    asyncio.run(scenario())


def test_distributed_ledger_wires_the_work_observer_into_every_component(monkeypatch) -> None:
    """Distributed+SQLite is rejected by BuiltinConfig, so spy the ledger components."""

    async def scenario() -> None:
        observer = _RecordingObserver()
        constructed: dict[str, dict[str, object]] = {}

        class _SpyComponent:
            def __init__(self, *args, **kwargs) -> None:
                constructed[self._role] = kwargs
                self._observer = kwargs.get("observer")
                self._stopped = asyncio.Event()

            _role = ""
            instance_id = "instance-1"

            async def start(self) -> None:
                pass

            async def run(self) -> None:
                await self._stopped.wait()

            async def stop(self) -> None:
                self._stopped.set()

        class _SpyMembership(_SpyComponent):
            _role = "membership"

        class _SpyScheduler(_SpyComponent):
            _role = "scheduler"

        class _SpyWorker(_SpyComponent):
            _role = "worker"

        monkeypatch.setattr("powercontext.builtin.runtime.membership.RuntimeMembership", _SpyMembership)
        monkeypatch.setattr("powercontext.builtin.runtime.durable_scheduler.DurableScheduler", _SpyScheduler)
        monkeypatch.setattr("powercontext.builtin.runtime.worker.DurableWorker", _SpyWorker)
        monkeypatch.setattr("powercontext.builtin.runtime.composition._work_discoverers", lambda config, contexts: ())
        monkeypatch.setattr("powercontext.builtin.runtime.composition._work_handlers", lambda config, contexts, **_: ())

        config = cast(
            "BuiltinConfig",
            SimpleNamespace(
                deployment=DeploymentConfig(mode="distributed", role="all"),
                runtime=RuntimeConfig(memory_schedule_seconds=30),
                coordination=CoordinationConfig(),
                worker=WorkerConfig(),
                operations=OperationsConfig(),
            ),
        )
        contexts = cast("RelationalContexts", SimpleNamespace(database=None))
        async with _open_work_background(config, contexts, observer=observer) as background:
            assert background.membership is not None
            assert background.scheduler is not None
            assert background.worker is not None
            assert background.operations is not None
            # Every ledger component feeds the same bounded-label observer so
            # /metrics reports real durable work, not only direct test calls.
            assert background.membership._observer is observer
            assert background.scheduler._observer is observer
            assert background.worker._observer is observer
            assert background.operations._observer is observer
            assert constructed["scheduler"]["observer"] is observer
            assert constructed["worker"]["observer"] is observer
            assert constructed["membership"]["observer"] is observer

    asyncio.run(scenario())


def test_single_node_maintenance_purges_expired_rate_limit_windows(tmp_path) -> None:
    """The single-node background must bound the rate limit window table."""

    async def scenario() -> None:
        config = _config(
            tmp_path,
            operations=OperationsConfig(cleanup_interval_seconds=0.1, cleanup_batch_size=500),
        )
        async with (
            open_builtin_contexts(config) as contexts,
            _open_work_background(config, contexts) as background,
        ):
            assert background.rate_limit_maintenance is not None
            expired_at = datetime.now(tz=UTC) - timedelta(hours=1)
            async with contexts.database.transaction() as connection:
                await connection.execute(
                    insert(RATE_LIMIT_WINDOWS_TABLE).values(
                        principal_key="a" * 64,
                        policy_id="server.http",
                        window_started_at=expired_at,
                        expires_at=expired_at + timedelta(seconds=60),
                        request_count=1,
                    )
                )
            deadline = monotonic() + 5
            rows = -1
            while monotonic() < deadline:
                async with contexts.database.transaction() as connection:
                    rows = (
                        await connection.execute(select(func.count()).select_from(RATE_LIMIT_WINDOWS_TABLE))
                    ).scalar()
                if rows == 0:
                    break
                await asyncio.sleep(0.05)
            # The maintenance loop deleted the expired window row without any
            # distributed ledger execution.
            assert rows == 0

    asyncio.run(scenario())


def test_rate_limit_maintenance_reports_a_failed_purge_and_keeps_running(tmp_path, caplog) -> None:
    """A failing purge must be logged through the module logger, not dropped, and must not stop the loop."""

    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            attempts: list[int] = []
            twice = asyncio.Event()

            class _FailingPurge:
                async def purge_expired(self, connection, *, limit):
                    attempts.append(limit)
                    if len(attempts) >= 2:
                        twice.set()
                    raise RuntimeError("purge failed")  # noqa: TRY003

            from powercontext.builtin.runtime.composition import _RateLimitWindowMaintenance

            maintenance = _RateLimitWindowMaintenance(
                database=contexts.database,
                interval_seconds=0.05,
                batch_size=7,
                repository=_FailingPurge(),
            )
            maintenance.start()
            await asyncio.wait_for(twice.wait(), timeout=5)
            await maintenance.stop()

            # The loop retried after the failure instead of exiting.
            assert len(attempts) >= 2
            assert set(attempts) == {7}
            failures = [
                record
                for record in caplog.records
                if record.name == "powercontext.builtin.runtime.composition"
                and getattr(record, "event", None) == "rate-limit.window.maintenance-failed"
            ]
            assert len(failures) == len(attempts)
            assert all(
                record.getMessage() == "rate limit window maintenance failed: purge failed" for record in failures
            )
            assert all(record.levelno == logging.WARNING for record in failures)

    asyncio.run(scenario())


def test_rate_limit_maintenance_stops_when_requested(tmp_path) -> None:
    async def scenario() -> None:
        async with open_builtin_contexts(_config(tmp_path)) as contexts:
            purges: list[int] = []

            class _PurgeSpy:
                async def purge_expired(self, connection, *, limit):
                    purges.append(limit)
                    return 0

            from powercontext.builtin.runtime.composition import _RateLimitWindowMaintenance

            maintenance = _RateLimitWindowMaintenance(
                database=contexts.database,
                interval_seconds=0.05,
                batch_size=10,
                repository=_PurgeSpy(),
            )
            maintenance.start()
            await asyncio.sleep(0.25)
            while_count = len(purges)
            assert while_count >= 2
            await maintenance.stop()
            after_stop = len(purges)
            await asyncio.sleep(0.15)
            assert len(purges) == after_stop

    asyncio.run(scenario())


def test_distributed_worker_authorizes_every_claimed_scope(monkeypatch) -> None:
    """A ledger Worker must check the background principal before preparing work."""

    async def scenario() -> None:
        authorized: list[str] = []

        class _Security:
            async def authorize_scope(self, scope_id: str) -> None:
                authorized.append(scope_id)

        @asynccontextmanager
        async def _open_security(spec, database):
            assert spec == {"principal": {"type": "service", "id": "background"}}
            yield _Security()

        monkeypatch.setattr("powercontext.server.processing_security.open_worker_security", _open_security)

        constructed: dict[str, dict[str, object]] = {}

        class _SpyComponent:
            def __init__(self, *args, **kwargs) -> None:
                constructed[self._role] = kwargs
                self._stopped = asyncio.Event()

            _role = ""
            instance_id = "instance-1"

            async def start(self) -> None:
                pass

            async def run(self) -> None:
                await self._stopped.wait()

            async def stop(self) -> None:
                self._stopped.set()

        class _SpyMembership(_SpyComponent):
            _role = "membership"

        class _SpyScheduler(_SpyComponent):
            _role = "scheduler"

        class _SpyWorker(_SpyComponent):
            _role = "worker"

        monkeypatch.setattr("powercontext.builtin.runtime.membership.RuntimeMembership", _SpyMembership)
        monkeypatch.setattr("powercontext.builtin.runtime.durable_scheduler.DurableScheduler", _SpyScheduler)
        monkeypatch.setattr("powercontext.builtin.runtime.worker.DurableWorker", _SpyWorker)
        monkeypatch.setattr("powercontext.builtin.runtime.composition._work_discoverers", lambda config, contexts: ())
        monkeypatch.setattr("powercontext.builtin.runtime.composition._work_handlers", lambda config, contexts, **_: ())

        config = cast(
            "BuiltinConfig",
            SimpleNamespace(
                deployment=DeploymentConfig(mode="distributed", role="worker"),
                runtime=RuntimeConfig(memory_schedule_seconds=30),
                coordination=CoordinationConfig(),
                worker=WorkerConfig(),
                operations=OperationsConfig(),
            ),
        )
        contexts = cast("RelationalContexts", SimpleNamespace(database=None))
        spec = {"principal": {"type": "service", "id": "background"}}
        async with _open_work_background(config, contexts, worker_security=spec) as background:
            assert background.worker is not None
            authorizer = cast("Callable[[object], Awaitable[None]]", constructed["worker"]["authorizer"])
            await authorizer(SimpleNamespace(scope_id="project"))
            assert authorized == ["project"]

        async with _open_work_background(config, contexts) as background:
            assert background.worker is not None
            assert constructed["worker"]["authorizer"] is None

    asyncio.run(scenario())


def test_internal_maintenance_is_authorized_outside_business_scope_grants() -> None:
    """Retention must not require a grant on the synthetic ``system:operations`` Scope."""

    class _Security:
        def __init__(self) -> None:
            self.scopes: list[str] = []

        async def authorize_scope(self, scope_id: str) -> None:
            self.scopes.append(scope_id)

    security = _Security()
    authorize = _work_authorizer(security)
    assert authorize is not None
    maintenance = SimpleNamespace(kind=MAINTENANCE_WORK_KIND, scope_id=INTERNAL_MAINTENANCE_SCOPE)
    business = SimpleNamespace(kind=MAINTENANCE_WORK_KIND, scope_id="scope-1")
    memory = SimpleNamespace(kind=MEMORY_WORK_KIND, scope_id=INTERNAL_MAINTENANCE_SCOPE)

    asyncio.run(authorize(maintenance))
    asyncio.run(authorize(business))
    asyncio.run(authorize(memory))

    # Only the exact internal maintenance pair skips the business Scope grant; a
    # business Scope carrying the marker, or a ledger claim on the marker, is still
    # authorized normally.
    assert security.scopes == ["scope-1", INTERNAL_MAINTENANCE_SCOPE]
