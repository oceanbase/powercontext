"""Persistent APScheduler integration for the local runtime."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import URL

SOURCE_WINDOW_JOB_ID = "powercontext.memory.source-window.v1"
SCHEDULER_TABLE = "powercontext_scheduler_jobs"

_Processor = Callable[[], Awaitable[None]]
_processors: dict[str, _Processor] = {}


class SchedulerConfigurationError(ValueError):
    """Report an unsupported local scheduler configuration."""

    def __init__(self) -> None:
        super().__init__("scheduled runtimes require a file-backed SQLite database")


class SchedulerStateError(RuntimeError):
    """Report conflicting runtime ownership for a persisted scheduler."""

    def __init__(self, code: str, runtime_key: str) -> None:
        messages = {
            "duplicate": f"a scheduled runtime is already open for {runtime_key}",
            "missing": f"no live scheduled runtime is registered for {runtime_key}",
        }
        super().__init__(messages[code])


def scheduler_runtime_key(database: str | Path) -> str:
    """Return the stable in-process lookup key for a file-backed runtime."""

    if str(database) == ":memory:":
        raise SchedulerConfigurationError
    return str(Path(database).expanduser().resolve())


def scheduler_database_path(database: str | Path) -> str:
    """Return the sidecar database used only by the persisted job store."""

    return f"{scheduler_runtime_key(database)}.scheduler"


def register_processor(runtime_key: str, processor: _Processor) -> None:
    """Register the live processor referenced by a persisted job."""

    if runtime_key in _processors:
        raise SchedulerStateError("duplicate", runtime_key)
    _processors[runtime_key] = processor


def unregister_processor(runtime_key: str) -> None:
    """Remove a live processor after its scheduler has stopped."""

    _processors.pop(runtime_key, None)


async def dispatch_source_windows(runtime_key: str) -> None:
    """Dispatch a persisted job to the live runtime owning its database."""

    processor = _processors.get(runtime_key)
    if processor is None:
        raise SchedulerStateError("missing", runtime_key)
    await processor()


def create_scheduler(database: str | Path) -> AsyncIOScheduler:
    """Create a scheduler with an isolated SQLite job-store sidecar."""

    database_path = scheduler_database_path(database)
    url = URL.create("sqlite+pysqlite", database=database_path)
    return AsyncIOScheduler(
        jobstores={
            "default": SQLAlchemyJobStore(
                url=url,
                tablename=SCHEDULER_TABLE,
                engine_options={"connect_args": {"timeout": 30}},
            )
        },
        timezone="UTC",
    )


def configure_source_window_job(
    scheduler: AsyncIOScheduler,
    *,
    runtime_key: str,
    schedule_seconds: float,
) -> None:
    """Create or reconcile the one persisted Source-window interval job."""

    job = scheduler.get_job(SOURCE_WINDOW_JOB_ID)
    if job is None:
        scheduler.add_job(
            dispatch_source_windows,
            "interval",
            args=(runtime_key,),
            seconds=schedule_seconds,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=None,
            id=SOURCE_WINDOW_JOB_ID,
        )
        return
    scheduler.modify_job(
        SOURCE_WINDOW_JOB_ID,
        func=dispatch_source_windows,
        args=(runtime_key,),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=None,
    )
    if not isinstance(job.trigger, IntervalTrigger) or job.trigger.interval.total_seconds() != schedule_seconds:
        scheduler.reschedule_job(
            SOURCE_WINDOW_JOB_ID,
            trigger="interval",
            seconds=schedule_seconds,
        )
