"""Persistent APScheduler adapter for built-in Source-window activations."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy import URL

SOURCE_WINDOW_JOB_ID = "powercontext.memory.source-window.v1"
EXPERIENCE_INCUBATION_JOB_ID = "powercontext.experience.incubation.v1"
SCHEDULER_TABLE = "powercontext_scheduler_jobs"

Processor = Callable[[], Awaitable[None]]
_SOURCE_WINDOW_PROCESSOR = "source-window"
_EXPERIENCE_INCUBATION_PROCESSOR = "experience-incubation"
_processors: dict[str, dict[str, Processor]] = {}


class SchedulerConfigurationError(ValueError):
    """Report an unsupported scheduler sidecar configuration."""

    def __init__(self) -> None:
        super().__init__("scheduler_path must reference a file")


class SchedulerStateError(RuntimeError):
    """Report conflicting runtime ownership for a persisted scheduler."""

    def __init__(self, code: str, runtime_key: str) -> None:
        messages = {
            "duplicate": f"a scheduled runtime is already open for {runtime_key}",
            "missing": f"no live scheduled runtime is registered for {runtime_key}",
        }
        super().__init__(messages[code])


def scheduler_runtime_key(scheduler_path: str | Path) -> str:
    """Return the stable in-process lookup key for one scheduler sidecar."""

    if str(scheduler_path) == ":memory:":
        raise SchedulerConfigurationError
    return str(Path(scheduler_path).expanduser().resolve())


def scheduler_database_path(scheduler_path: str | Path) -> str:
    """Return the sidecar database used only by the persisted job store."""

    return str(Path(scheduler_runtime_key(scheduler_path)))


def register_processors(
    runtime_key: str,
    *,
    source_window: Processor | None,
    experience_incubation: Processor | None,
) -> None:
    """Register live processors referenced by persisted jobs."""

    if runtime_key in _processors:
        raise SchedulerStateError("duplicate", runtime_key)
    registered = {
        name: processor
        for name, processor in (
            (_SOURCE_WINDOW_PROCESSOR, source_window),
            (_EXPERIENCE_INCUBATION_PROCESSOR, experience_incubation),
        )
        if processor is not None
    }
    if not registered:
        raise SchedulerStateError("missing", runtime_key)
    _processors[runtime_key] = registered


def unregister_processor(runtime_key: str) -> None:
    """Remove a live processor after its scheduler has stopped."""

    _processors.pop(runtime_key, None)


async def dispatch_source_windows(runtime_key: str) -> None:
    """Dispatch a persisted job to the live runtime owning its database."""

    await _processor(runtime_key, _SOURCE_WINDOW_PROCESSOR)()


async def dispatch_experience_incubation(runtime_key: str) -> None:
    """Dispatch persisted Experience incubation to its live Runtime."""

    await _processor(runtime_key, _EXPERIENCE_INCUBATION_PROCESSOR)()


def _processor(runtime_key: str, name: str) -> Processor:
    try:
        return _processors[runtime_key][name]
    except KeyError:
        raise SchedulerStateError("missing", runtime_key) from None


def create_scheduler(scheduler_path: str | Path) -> AsyncIOScheduler:
    """Create an APScheduler instance with an isolated SQLite job-store."""

    url = URL.create("sqlite+pysqlite", database=scheduler_database_path(scheduler_path))
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
    schedule_seconds: float | None,
) -> None:
    """Create or reconcile the persisted Source-window interval job."""

    _configure_interval_job(
        scheduler,
        job_id=SOURCE_WINDOW_JOB_ID,
        processor=dispatch_source_windows,
        runtime_key=runtime_key,
        schedule_seconds=schedule_seconds,
    )


def configure_experience_incubation_job(
    scheduler: AsyncIOScheduler,
    *,
    runtime_key: str,
    schedule_seconds: float | None,
) -> None:
    """Create or reconcile the persisted Experience incubation job."""

    _configure_interval_job(
        scheduler,
        job_id=EXPERIENCE_INCUBATION_JOB_ID,
        processor=dispatch_experience_incubation,
        runtime_key=runtime_key,
        schedule_seconds=schedule_seconds,
    )


def _configure_interval_job(
    scheduler: AsyncIOScheduler,
    *,
    job_id: str,
    processor: Callable[[str], Awaitable[None]],
    runtime_key: str,
    schedule_seconds: float | None,
) -> None:
    job = scheduler.get_job(job_id)
    if schedule_seconds is None:
        if job is not None:
            scheduler.remove_job(job_id)
        return
    if job is None:
        scheduler.add_job(
            processor,
            "interval",
            args=(runtime_key,),
            seconds=schedule_seconds,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=None,
            id=job_id,
        )
        return
    scheduler.modify_job(
        job_id,
        func=processor,
        args=(runtime_key,),
        coalesce=True,
        max_instances=1,
        misfire_grace_time=None,
    )
    if not isinstance(job.trigger, IntervalTrigger) or job.trigger.interval.total_seconds() != schedule_seconds:
        scheduler.reschedule_job(
            job_id,
            trigger="interval",
            seconds=schedule_seconds,
        )
