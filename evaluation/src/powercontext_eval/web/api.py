"""Internal FastAPI control plane for the evaluation console."""

from __future__ import annotations

import asyncio
import json
import mimetypes
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from stat import S_ISDIR, S_ISREG
from typing import Annotated, Any, Literal

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse

from powercontext_eval.benchmarks.swebench_pro.catalog import (
    CatalogError,
    SweBenchProCatalog,
    instance_ids_for_task_set,
)
from powercontext_eval.codex import DEFAULT_REASONING_EFFORT
from powercontext_eval.errors import GitSourceError
from powercontext_eval.git_source import GitSource
from powercontext_eval.models import PowerContextRef
from powercontext_eval.web.batches import (
    BatchCreate,
    BatchPreviewResponse,
    BatchRecord,
    BatchRuntimeFailure,
    BatchRuntimeResponse,
    BatchRuntimeTask,
    PairCategory,
    TaskRetryRequest,
)
from powercontext_eval.web.config import WebConfig
from powercontext_eval.web.controls import BatchControlPatch, BatchPauseReason, BatchPreviewRequest
from powercontext_eval.web.estimation import BatchEstimate, EstimateBasis, estimate_batch
from powercontext_eval.web.models import Capabilities, HealthResponse, TaskCreate, TaskRecord, TaskStatus, TaskSummary
from powercontext_eval.web.reporting import (
    BenchmarkCatalog,
    InvalidReportArtifact,
    ReportingError,
    UnsafeReportPath,
    load_batch_estimate_samples,
    load_batch_report,
    load_batch_task_detail,
    load_batch_task_page,
    load_context_event,
    load_context_page,
    load_raw_report,
    load_report,
    task_run_dir,
)
from powercontext_eval.web.resources import FilesystemResourceProbe, ResourceProbe, ResourceUnavailable
from powercontext_eval.web.revision import RUNTIME_SCHEMA_VERSION, current_build_revision
from powercontext_eval.web.store import BatchNotFound, TaskAdmissionRejected, TaskConflict, TaskNotFound, TaskStore
from powercontext_eval.web.usage import AccountUsage, UsageSnapshot, is_fresh

_TERMINAL = {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.INTERRUPTED, TaskStatus.CANCELLED}
_NO_STORE = {"Cache-Control": "no-store"}
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}
_MAX_FRONTEND_FILE_BYTES = 8 * 1024 * 1024
_MAX_FRONTEND_TOTAL_BYTES = 32 * 1024 * 1024


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers={**_NO_STORE, **_SECURITY_HEADERS},
    )


def _task_payload(record: TaskRecord, store: TaskStore) -> dict[str, Any]:
    payload = record.model_dump(mode="json")
    payload["queue_position"] = store.queue_position(record.task_id)
    return payload


def _summary_payload(summary: TaskSummary, store: TaskStore) -> dict[str, Any]:
    payload = summary.model_dump(mode="json")
    payload["queue_position"] = store.queue_position(summary.task_id)
    return payload


def _batch_payload(record: BatchRecord) -> dict[str, Any]:
    return record.model_dump(mode="json")


class TaskEventStream:
    """Poll task snapshots without retaining a database connection between polls."""

    def __init__(
        self,
        request: Request,
        store: TaskStore,
        task_id: str,
        *,
        poll_seconds: float,
        heartbeat_seconds: float = 15.0,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        load: Callable[[str], Awaitable[TaskRecord]] | None = None,
    ) -> None:
        self._request = request
        self._store = store
        self._task_id = task_id
        self._poll_seconds = poll_seconds
        self._heartbeat_seconds = min(heartbeat_seconds, 15.0)
        self._sleep = sleep
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._load = load

    async def __aiter__(self) -> AsyncIterator[str]:
        last_version: int | None = None
        started = self._monotonic()
        next_poll = started
        next_heartbeat = started + self._heartbeat_seconds
        pending_load: asyncio.Task[TaskRecord] | None = None
        heartbeat_timer: asyncio.Future[None] | None = None
        try:
            while not await self._request.is_disconnected():
                now = self._monotonic()
                if now >= next_heartbeat:
                    yield ": heartbeat\n\n"
                    next_heartbeat = now + self._heartbeat_seconds
                    continue

                if pending_load is None and now >= next_poll:
                    pending_load = asyncio.create_task(self._load_record())
                    await asyncio.sleep(0)

                if pending_load is None:
                    await self._sleep(max(0.0, min(next_poll, next_heartbeat) - now))
                    continue

                if not pending_load.done():
                    heartbeat_timer = asyncio.ensure_future(self._sleep(max(0.0, next_heartbeat - now)))
                    done, _ = await asyncio.wait(
                        {pending_load, heartbeat_timer},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if pending_load not in done:
                        await heartbeat_timer
                        heartbeat_timer = None
                        now = self._monotonic()
                        yield ": heartbeat\n\n"
                        next_heartbeat = now + self._heartbeat_seconds
                        continue

                    heartbeat_timer.cancel()
                    with suppress(asyncio.CancelledError):
                        await heartbeat_timer
                    heartbeat_timer = None
                record = await pending_load
                pending_load = None
                now = self._monotonic()
                next_poll = now + self._poll_seconds
                if record.version != last_version:
                    event = {
                        "task_id": record.task_id,
                        "status": record.status,
                        "phase": record.phase,
                        "version": record.version,
                        "occurred_at": self._wall_clock(),
                    }
                    data = json.dumps(event, default=lambda value: value.isoformat(), separators=(",", ":"))
                    yield f"event: task\ndata: {data}\n\n"
                    last_version = record.version
                    next_heartbeat = now + self._heartbeat_seconds
                    if record.status in _TERMINAL:
                        return
                elif now >= next_heartbeat:
                    yield ": heartbeat\n\n"
                    next_heartbeat = now + self._heartbeat_seconds
        finally:
            for task in (heartbeat_timer, pending_load):
                if task is not None and not task.done():
                    task.cancel()
                if task is not None:
                    with suppress(asyncio.CancelledError):
                        await task

    async def _load_record(self) -> TaskRecord:
        if self._load is not None:
            return await self._load(self._task_id)
        return await asyncio.to_thread(self._store.get, self._task_id)


def _safe_frontend(frontend: Path, root: Path) -> bool:
    """Validate a regular, symlink-free build under the configured deploy tree."""
    deploy = root / "deploy"
    try:
        relative = frontend.relative_to(deploy)
        if not relative.parts or ".." in relative.parts:
            return False
        for ancestor in (root, deploy):
            metadata = ancestor.lstat()
            if ancestor.is_symlink() or not S_ISDIR(metadata.st_mode):
                return False
        current = deploy
        for component in relative.parts:
            current /= component
            metadata = current.lstat()
            if current.is_symlink() or not S_ISDIR(metadata.st_mode):
                return False
        index = frontend / "index.html"
        assets = frontend / "assets"
        if index.is_symlink() or not S_ISREG(index.lstat().st_mode):
            return False
        if assets.is_symlink() or not S_ISDIR(assets.lstat().st_mode):
            return False
        for directory, directories, files in os.walk(frontend, followlinks=False):
            base = Path(directory)
            if any((base / name).is_symlink() for name in (*directories, *files)):
                return False
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
        return False
    return True


@dataclass(frozen=True)
class _FrontendSnapshot:
    index: bytes
    assets: dict[str, tuple[bytes, str]]


def _open_directory(parent_fd: int, name: str) -> int:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not S_ISDIR(before.st_mode):
        raise OSError("Frontend component is not a directory")
    descriptor = os.open(
        name,
        os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    after = os.fstat(descriptor)
    if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
        os.close(descriptor)
        raise OSError("Frontend directory changed while opening")
    return descriptor


def _read_snapshot_file(parent_fd: int, name: str, total: list[int]) -> bytes:
    before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    if not S_ISREG(before.st_mode) or before.st_size > _MAX_FRONTEND_FILE_BYTES:
        raise OSError("Frontend file is not a bounded regular file")
    descriptor = os.open(
        name,
        os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        dir_fd=parent_fd,
    )
    try:
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino) or not S_ISREG(after.st_mode):
            raise OSError("Frontend file changed while opening")
        chunks: list[bytes] = []
        remaining = _MAX_FRONTEND_FILE_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(remaining, 64 * 1024))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > _MAX_FRONTEND_FILE_BYTES or len(data) != after.st_size:
            raise OSError("Frontend file changed while reading")
        total[0] += len(data)
        if total[0] > _MAX_FRONTEND_TOTAL_BYTES:
            raise OSError("Frontend snapshot is too large")
        return data
    finally:
        os.close(descriptor)


def _snapshot_assets(directory_fd: int, prefix: str, total: list[int]) -> dict[str, tuple[bytes, str]]:
    assets: dict[str, tuple[bytes, str]] = {}
    for name in sorted(os.listdir(directory_fd)):
        metadata = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        relative = f"{prefix}/{name}" if prefix else name
        if S_ISDIR(metadata.st_mode):
            child_fd = _open_directory(directory_fd, name)
            try:
                assets.update(_snapshot_assets(child_fd, relative, total))
            finally:
                os.close(child_fd)
        elif S_ISREG(metadata.st_mode):
            media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
            assets[relative] = (_read_snapshot_file(directory_fd, name, total), media_type)
        else:
            raise OSError("Frontend tree contains a non-regular entry")
    return assets


def _snapshot_frontend(frontend: Path, root: Path) -> _FrontendSnapshot | None:
    if not _safe_frontend(frontend, root):
        return None
    relative = frontend.relative_to(root / "deploy")
    descriptors: list[int] = []
    try:
        root_fd = os.open(
            root,
            os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
        )
        descriptors.append(root_fd)
        current_fd = _open_directory(root_fd, "deploy")
        descriptors.append(current_fd)
        for component in relative.parts:
            current_fd = _open_directory(current_fd, component)
            descriptors.append(current_fd)
        total = [0]
        index = _read_snapshot_file(current_fd, "index.html", total)
        assets_fd = _open_directory(current_fd, "assets")
        descriptors.append(assets_fd)
        return _FrontendSnapshot(index=index, assets=_snapshot_assets(assets_fd, "", total))
    except (FileNotFoundError, NotADirectoryError, OSError, ValueError):
        return None
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)


def create_app(
    config: WebConfig,
    store: TaskStore | None = None,
    *,
    catalog: BenchmarkCatalog | None = None,
    resource_probe: ResourceProbe | None = None,
) -> FastAPI:
    """Create an API application; evaluation execution remains worker-owned."""
    task_store = store or TaskStore(
        config.database_path,
        lease_duration=timedelta(seconds=config.lease_seconds),
        max_attempts=config.max_attempts,
    )
    task_store.initialize()
    task_store.record_runtime_revision(
        "web",
        build_revision=current_build_revision(),
        schema_version=RUNTIME_SCHEMA_VERSION,
        now=datetime.now(UTC),
    )
    benchmark_catalog = catalog
    powercontext_source = GitSource(cache_root=config.run_root / "cache" / "powercontext-git")
    filesystem_probe = resource_probe or FilesystemResourceProbe(config.run_root)

    def get_catalog() -> BenchmarkCatalog:
        nonlocal benchmark_catalog
        if benchmark_catalog is None:
            benchmark_catalog = SweBenchProCatalog.load(config.dataset_path)
        return benchmark_catalog

    def current_usage() -> UsageSnapshot | None:
        if config.usage_mode == "api_key":
            return None
        snapshot = task_store.latest_usage_snapshot()
        if snapshot is None or not is_fresh(
            snapshot,
            now=datetime.now(UTC),
            max_age=timedelta(seconds=config.usage_snapshot_max_age_seconds),
        ):
            return None
        return snapshot

    def resolve_powercontext_ref(ref: str) -> str:
        requested = PowerContextRef.parse(ref)
        resolved = powercontext_source.resolve(config.powercontext_source, requested)
        return resolved.sha

    def historical_estimate(request: BatchPreviewRequest, *, total_tasks: int) -> BatchEstimate:
        samples = []
        for batch in task_store.list_batches():
            candidate = batch.request
            if (
                candidate.benchmark != "swebench-pro"
                or candidate.task_set != request.task_set
                or candidate.model != request.model
                or candidate.reasoning_effort != DEFAULT_REASONING_EFFORT
                or candidate.treatment_mode != "off_on"
            ):
                continue
            try:
                samples.extend(
                    load_batch_estimate_samples(
                        batch,
                        task_store.list_batch_tasks(batch.batch_id),
                        runs_root=config.run_root / "runs",
                        catalog=get_catalog(),
                    )
                )
            except (CatalogError, ReportingError, OSError, ValueError):
                continue
        if not samples:
            return BatchEstimate.unavailable(remaining_tasks=total_tasks)
        return estimate_batch(
            samples=samples,
            remaining_tasks=total_tasks,
            basis=EstimateBasis.HISTORICAL_COMPATIBLE,
        )

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(RequestValidationError)
    def validation_error(_request: Request, _error_value: RequestValidationError) -> JSONResponse:
        return _error(422, "invalid_request", "The evaluation request is invalid.")

    @app.exception_handler(Exception)
    def internal_error(_request: Request, _error_value: Exception) -> JSONResponse:
        return _error(500, "internal_error", "The evaluation service could not complete the request.")

    @app.get("/api/health")
    def health() -> HealthResponse:
        queue_health = task_store.health_snapshot(now=datetime.now(UTC))
        deployment = task_store.deployment_snapshot()
        task_parallelism = queue_health["task_parallelism"]
        min_free_bytes = config.filesystem_min_free_bytes_for(task_parallelism)
        min_free_inodes = config.filesystem_min_free_inodes_for(task_parallelism)
        try:
            capacity = filesystem_probe.read()
        except ResourceUnavailable:
            capacity = None
        admission_open = capacity is not None and capacity.admission_open(
            min_free_bytes=min_free_bytes,
            min_free_inodes=min_free_inodes,
        )
        return HealthResponse(
            service="ok",
            **queue_health,
            **deployment,
            resource_admission_open=admission_open,
            filesystem_free_bytes=None if capacity is None else capacity.free_bytes,
            filesystem_total_bytes=None if capacity is None else capacity.total_bytes,
            filesystem_min_free_bytes=min_free_bytes,
            filesystem_free_inodes=None if capacity is None else capacity.free_inodes,
            filesystem_total_inodes=None if capacity is None else capacity.total_inodes,
            filesystem_min_free_inodes=min_free_inodes,
        )

    @app.get("/api/capabilities")
    def capabilities() -> Capabilities:
        return Capabilities(models=config.codex_models)

    @app.put("/api/auth")
    def update_auth(body: dict[str, Any]) -> Response:
        raw = body.get("auth_json")
        if not isinstance(raw, str):
            return _error(422, "invalid_auth_json", "auth_json must be a JSON string.")
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return _error(422, "invalid_auth_json", "auth_json is not valid JSON.")
        if not isinstance(parsed, dict) or "tokens" not in parsed or "auth_mode" not in parsed:
            return _error(422, "invalid_auth_json", "auth_json must contain auth_mode and tokens.")
        auth_path = config.auth_json
        auth_path.parent.mkdir(parents=True, exist_ok=True)
        if auth_path.exists():
            backup = auth_path.with_name(f"auth.json.backup-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}")
            auth_path.replace(backup)
        tmp = auth_path.with_suffix(".json.tmp")
        tmp.write_text(raw, encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, auth_path)
        return JSONResponse({"updated_at": datetime.now(UTC).isoformat()}, headers=_NO_STORE)

    @app.post("/api/batches/preview")
    def preview_batch(request: BatchPreviewRequest) -> Response:
        if not config.accepts_codex_model(request.model):
            return _error(422, "invalid_request", "The evaluation request is invalid.")
        snapshot = current_usage()
        if config.usage_mode == "subscription" and snapshot is None:
            return _error(503, "usage_unavailable", "Current Codex subscription usage is unavailable.")
        try:
            total_tasks = len(instance_ids_for_task_set(get_catalog().instance_ids, request.task_set))
        except CatalogError:
            return _error(503, "benchmark_unavailable", "The pinned benchmark task set is unavailable.")
        blocked = snapshot is not None and (
            snapshot.rate_limit_reached_type is not None or snapshot.used_percent >= request.usage_pause_percent
        )
        response = BatchPreviewResponse(
            powercontext_ref=request.powercontext_ref,
            benchmark="swebench-pro",
            task_set=request.task_set,
            model=request.model,
            reasoning_effort=DEFAULT_REASONING_EFFORT,
            treatment_mode="off_on",
            total_tasks=total_tasks,
            usage_pause_percent=request.usage_pause_percent,
            usage=snapshot,
            estimate=historical_estimate(request, total_tasks=total_tasks),
            can_start=not blocked,
            block_reason="usage_threshold_reached" if blocked else None,
        )
        return JSONResponse(content=response.model_dump(mode="json"), headers=_NO_STORE)

    @app.post("/api/batches")
    def create_batch(request: BatchCreate) -> Response:
        try:
            replay = task_store.find_batch_replay(request)
        except TaskConflict:
            return _error(409, "idempotency_conflict", "The idempotency key belongs to a different request.")
        if replay is not None:
            return JSONResponse(status_code=200, content=_batch_payload(replay), headers=_NO_STORE)
        snapshot = current_usage()
        if config.usage_mode == "subscription" and snapshot is None:
            return _error(503, "usage_unavailable", "Current Codex subscription usage is unavailable.")
        if snapshot is not None and (
            snapshot.rate_limit_reached_type is not None or snapshot.used_percent >= request.usage_pause_percent
        ):
            return _error(
                409,
                "usage_threshold_reached",
                "Current Codex subscription usage is at or above the selected threshold.",
            )
        try:
            selected_catalog = get_catalog()
            selected_instance_ids = instance_ids_for_task_set(selected_catalog.instance_ids, request.task_set)
            resolved_powercontext_sha = resolve_powercontext_ref(request.powercontext_ref)
            record, created = task_store.create_batch(
                request,
                selected_instance_ids,
                resolved_powercontext_sha=resolved_powercontext_sha,
                now=datetime.now(UTC),
                admit_model=config.accepts_codex_model,
            )
        except CatalogError:
            return _error(503, "benchmark_unavailable", "The pinned benchmark task set is unavailable.")
        except GitSourceError:
            return _error(503, "source_unavailable", "The selected PowerContext source could not be resolved.")
        except TaskAdmissionRejected:
            return _error(422, "invalid_request", "The evaluation request is invalid.")
        except TaskConflict:
            return _error(409, "idempotency_conflict", "The idempotency key belongs to a different request.")
        return JSONResponse(
            status_code=201 if created else 200,
            content=_batch_payload(record),
            headers=_NO_STORE,
        )

    @app.get("/api/batches")
    def list_batches() -> Response:
        return JSONResponse(
            content=[_batch_payload(batch) for batch in task_store.list_batches()],
            headers=_NO_STORE,
        )

    @app.get("/api/batches/{batch_id}")
    def get_batch(batch_id: str) -> Response:
        try:
            return JSONResponse(content=_batch_payload(task_store.get_batch(batch_id)), headers=_NO_STORE)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")

    @app.post("/api/batches/{batch_id}/pause")
    def pause_batch(batch_id: str) -> Response:
        try:
            record = task_store.request_pause(
                batch_id,
                reason=BatchPauseReason.USER,
                now=datetime.now(UTC),
            )
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskConflict:
            return _error(409, "batch_control_conflict", "The requested batch control change is not available.")
        return JSONResponse(content=_batch_payload(record), headers=_NO_STORE)

    @app.post("/api/batches/{batch_id}/resume")
    def resume_batch(batch_id: str) -> Response:
        try:
            record = task_store.request_resume(batch_id, now=datetime.now(UTC))
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskConflict:
            return _error(409, "batch_control_conflict", "The requested batch control change is not available.")
        return JSONResponse(content=_batch_payload(record), headers=_NO_STORE)

    @app.post("/api/batches/{batch_id}/cancel")
    def cancel_batch(batch_id: str) -> Response:
        try:
            record = task_store.request_cancel(batch_id, now=datetime.now(UTC))
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskConflict:
            return _error(409, "batch_control_conflict", "The requested batch control change is not available.")
        return JSONResponse(content=_batch_payload(record), headers=_NO_STORE)

    @app.patch("/api/batches/{batch_id}/controls")
    def update_batch_controls(batch_id: str, request: BatchControlPatch) -> Response:
        try:
            record = task_store.update_usage_threshold(
                batch_id,
                percent=request.usage_pause_percent,
                expected_version=request.expected_version,
                now=datetime.now(UTC),
            )
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskConflict:
            return _error(
                409,
                "batch_control_version_conflict",
                "The batch controls changed before this update was applied.",
            )
        return JSONResponse(content=_batch_payload(record), headers=_NO_STORE)

    @app.get("/api/batches/{batch_id}/control-events")
    def batch_control_events(batch_id: str) -> Response:
        try:
            events = task_store.list_control_events(batch_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        return JSONResponse(
            content=[event.model_dump(mode="json") for event in events],
            headers=_NO_STORE,
        )

    @app.get("/api/account-usage")
    def account_usage() -> Response:
        if config.usage_mode == "api_key":
            response = AccountUsage(mode="api_key", sufficient=True, usage=None)
            return JSONResponse(content=response.model_dump(mode="json"), headers=_NO_STORE)
        snapshot = current_usage()
        if snapshot is None:
            return _error(503, "usage_unavailable", "Current Codex subscription usage is unavailable.")
        sufficient = snapshot.rate_limit_reached_type is None and snapshot.used_percent < config.usage_pause_percent
        response = AccountUsage(mode="subscription", sufficient=sufficient, usage=snapshot)
        return JSONResponse(content=response.model_dump(mode="json"), headers=_NO_STORE)

    @app.post("/api/batches/{batch_id}/tasks/{task_id}/retry")
    def retry_batch_task(batch_id: str, task_id: str, request: TaskRetryRequest) -> Response:
        snapshot = current_usage()
        if config.usage_mode == "subscription" and snapshot is None:
            return _error(503, "usage_unavailable", "Current Codex subscription usage is unavailable.")
        try:
            batch = task_store.get_batch(batch_id)
            if snapshot is not None and (
                snapshot.rate_limit_reached_type is not None
                or snapshot.used_percent >= batch.control.usage_pause_percent
            ):
                return _error(
                    409,
                    "usage_threshold_reached",
                    "Current Codex subscription usage is at or above the batch threshold.",
                )
            attempt, created = task_store.retry_failed_task(
                batch_id,
                task_id,
                idempotency_key=request.idempotency_key,
                now=datetime.now(UTC),
            )
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        except TaskConflict:
            return _error(409, "task_not_retryable", "The current task outcome cannot be retried.")
        return JSONResponse(
            status_code=201 if created else 200,
            content=attempt.model_dump(mode="json"),
            headers=_NO_STORE,
        )

    @app.get("/api/batches/{batch_id}/tasks/{task_id}/attempts")
    def batch_task_attempts(batch_id: str, task_id: str) -> Response:
        try:
            attempts = task_store.list_task_attempts(batch_id, task_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        return JSONResponse(
            content=[attempt.model_dump(mode="json") for attempt in attempts],
            headers=_NO_STORE,
        )

    @app.get("/api/batches/{batch_id}/runtime")
    def batch_runtime(batch_id: str) -> Response:
        try:
            tasks = task_store.list_batch_tasks(batch_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")

        status_counts = {status: 0 for status in TaskStatus}
        runtime_tasks: list[BatchRuntimeTask] = []
        for task in tasks:
            status_counts[task.status] += 1
            if task.status is not TaskStatus.RUNNING and not (
                task.status is TaskStatus.QUEUED and task.attempt_number > 1
            ):
                continue
            if task.attempt_id is None or task.instance_id is None or task.source_index is None:
                continue
            previous_failure = next(
                (
                    attempt
                    for attempt in reversed(task_store.list_task_attempts(batch_id, task.task_id))
                    if attempt.attempt_number < task.attempt_number
                    and attempt.failure_category is not None
                    and attempt.failure_code is not None
                    and attempt.failure_summary is not None
                    and attempt.finished_at is not None
                ),
                None,
            )
            last_failure = None
            if previous_failure is not None:
                category = previous_failure.failure_category
                code = previous_failure.failure_code
                summary = previous_failure.failure_summary
                finished_at = previous_failure.finished_at
                if category is not None and code is not None and summary is not None and finished_at is not None:
                    last_failure = BatchRuntimeFailure(
                        category=category,
                        code=code,
                        phase=previous_failure.failure_phase,
                        summary=summary,
                        finished_at=finished_at,
                    )
            runtime_tasks.append(
                BatchRuntimeTask(
                    task_id=task.task_id,
                    attempt_id=task.attempt_id,
                    instance_id=task.instance_id,
                    source_index=task.source_index,
                    status=task.status,
                    phase=task.phase,
                    attempt_number=task.attempt_number,
                    attempt_count=task.attempt_count,
                    created_at=task.created_at,
                    eligible_at=task.eligible_at,
                    started_at=task.started_at,
                    last_failure=last_failure,
                )
            )
        response = BatchRuntimeResponse(
            batch_id=batch_id,
            generated_at=datetime.now(UTC),
            status_counts=status_counts,
            tasks=tuple(runtime_tasks),
        )
        return JSONResponse(content=response.model_dump(mode="json"), headers=_NO_STORE)

    @app.get("/api/batches/{batch_id}/events")
    def batch_events(batch_id: str, request: Request) -> Response:
        try:
            task_store.get_batch(batch_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")

        async def stream() -> AsyncIterator[str]:
            previous: str | None = None
            heartbeat_at = time.monotonic() + 15
            while not await request.is_disconnected():
                record = await asyncio.to_thread(task_store.get_batch, batch_id)
                serialized = json.dumps(_batch_payload(record), separators=(",", ":"))
                if serialized != previous:
                    yield f"event: batch\ndata: {serialized}\n\n"
                    previous = serialized
                    heartbeat_at = time.monotonic() + 15
                    if record.status.value in {"completed", "cancelled"}:
                        return
                elif time.monotonic() >= heartbeat_at:
                    yield ": heartbeat\n\n"
                    heartbeat_at = time.monotonic() + 15
                await asyncio.sleep(config.poll_seconds)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={**_NO_STORE, "X-Accel-Buffering": "no"},
        )

    def batch_inputs(batch_id: str) -> tuple[BatchRecord, list[TaskRecord]] | JSONResponse:
        try:
            batch = task_store.get_batch(batch_id)
            return batch, task_store.list_batch_tasks(batch_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")

    def selected_batch_task(batch_id: str, task_id: str, attempt_id: str | None) -> TaskRecord | None:
        current = task_store.get_batch_task(batch_id, task_id)
        if attempt_id is None or attempt_id == current.attempt_id:
            return current
        attempt = next(
            (
                candidate
                for candidate in task_store.list_task_attempts(batch_id, task_id)
                if candidate.attempt_id == attempt_id
            ),
            None,
        )
        if attempt is None:
            return None
        payload = current.model_dump(mode="python")
        payload.update(
            {
                "attempt_id": attempt.attempt_id,
                "attempt_number": attempt.attempt_number,
                "retryable": False,
                "status": attempt.status,
                "phase": attempt.phase,
                "created_at": attempt.created_at,
                "started_at": attempt.started_at,
                "finished_at": attempt.finished_at,
                "version": attempt.version,
                "failure_category": attempt.failure_category,
                "failure_code": attempt.failure_code,
                "retry_disposition": attempt.retry_disposition,
                "failure_phase": attempt.failure_phase,
                "failure_summary": attempt.failure_summary,
                "result": attempt.result,
            }
        )
        return TaskRecord.model_validate(payload, strict=True)

    @app.get("/api/batches/{batch_id}/report")
    def batch_report(batch_id: str) -> Response:
        selected = batch_inputs(batch_id)
        if isinstance(selected, JSONResponse):
            return selected
        batch, tasks = selected
        try:
            report = load_batch_report(
                batch,
                tasks,
                runs_root=config.run_root / "runs",
                catalog=get_catalog(),
                latest_usage=(task_store.latest_usage_snapshot() if config.usage_mode == "subscription" else None),
            )
        except (CatalogError, ReportingError, OSError, ValueError):
            return _error(409, "report_unavailable", "The batch report is not available.")
        return JSONResponse(content=report.model_dump(mode="json"), headers=_NO_STORE)

    @app.get("/api/batches/{batch_id}/tasks")
    def batch_tasks(
        batch_id: str,
        category: PairCategory | None = None,
        q: Annotated[str | None, Query(max_length=200)] = None,
        sort: Literal["source", "token_delta_asc", "token_delta_desc"] = "source",
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Response:
        selected = batch_inputs(batch_id)
        if isinstance(selected, JSONResponse):
            return selected
        batch, tasks = selected
        try:
            page = load_batch_task_page(
                batch,
                tasks,
                runs_root=config.run_root / "runs",
                catalog=get_catalog(),
                category=category,
                query=q,
                sort=sort,
                limit=limit,
                offset=offset,
            )
        except (CatalogError, ReportingError, OSError, ValueError):
            return _error(409, "report_unavailable", "The batch task report is not available.")
        return JSONResponse(content=page.model_dump(mode="json"), headers=_NO_STORE)

    @app.get("/api/batches/{batch_id}/tasks/{task_id}")
    def batch_task_detail(
        batch_id: str,
        task_id: str,
        attempt_id: Annotated[str | None, Query(max_length=500)] = None,
    ) -> Response:
        try:
            batch = task_store.get_batch(batch_id)
            task = selected_batch_task(batch_id, task_id, attempt_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        if task is None:
            return _error(404, "attempt_not_found", "The requested task attempt does not exist.")
        try:
            detail = load_batch_task_detail(
                batch,
                task,
                runs_root=config.run_root / "runs",
                catalog=get_catalog(),
                finalizations=(
                    task_store.tokensflow_finalizations_for_attempt(task.attempt_id)
                    if task.attempt_id is not None
                    else ()
                ),
            )
        except (CatalogError, ReportingError, OSError, ValueError):
            return _error(409, "report_unavailable", "The task detail report is not available.")
        return JSONResponse(content=detail.model_dump(mode="json"), headers=_NO_STORE)

    def context_inputs(
        batch_id: str,
        task_id: str,
        attempt_id: str | None,
    ) -> tuple[BatchRecord, TaskRecord] | JSONResponse:
        try:
            batch = task_store.get_batch(batch_id)
            task = selected_batch_task(batch_id, task_id, attempt_id)
        except BatchNotFound:
            return _error(404, "batch_not_found", "The requested evaluation batch does not exist.")
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        if task is None:
            return _error(404, "attempt_not_found", "The requested task attempt does not exist.")
        return batch, task

    @app.get("/api/batches/{batch_id}/tasks/{task_id}/context/{arm}")
    def task_context(
        batch_id: str,
        task_id: str,
        arm: Literal["off", "on"],
        limit: Annotated[int, Query(ge=1, le=200)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        attempt_id: Annotated[str | None, Query(max_length=500)] = None,
    ) -> Response:
        selected = context_inputs(batch_id, task_id, attempt_id)
        if isinstance(selected, JSONResponse):
            return selected
        batch, task = selected
        try:
            page = load_context_page(
                batch,
                task,
                runs_root=config.run_root / "runs",
                arm=arm,
                limit=limit,
                offset=offset,
            )
        except (ReportingError, OSError, ValueError):
            return _error(409, "context_unavailable", "The task context timeline is not available.")
        return JSONResponse(content=page.model_dump(mode="json"), headers=_NO_STORE)

    @app.get("/api/batches/{batch_id}/tasks/{task_id}/context/{arm}/{sequence}")
    def task_context_event(
        batch_id: str,
        task_id: str,
        arm: Literal["off", "on"],
        sequence: int,
        attempt_id: Annotated[str | None, Query(max_length=500)] = None,
    ) -> Response:
        selected = context_inputs(batch_id, task_id, attempt_id)
        if isinstance(selected, JSONResponse):
            return selected
        batch, task = selected
        try:
            event = load_context_event(
                batch,
                task,
                runs_root=config.run_root / "runs",
                arm=arm,
                sequence=sequence,
            )
        except (ReportingError, OSError, ValueError):
            return _error(409, "context_unavailable", "The task context event is not available.")
        return JSONResponse(content=event.model_dump(mode="json"), headers=_NO_STORE)

    @app.post("/api/tasks")
    def create_task(task: TaskCreate) -> Response:
        try:
            replay = task_store.find_task_replay(task)
        except TaskConflict:
            return _error(409, "idempotency_conflict", "The idempotency key belongs to a different request.")
        if replay is not None:
            return JSONResponse(status_code=200, content=_task_payload(replay, task_store), headers=_NO_STORE)
        try:
            record, created = task_store.create(
                task,
                now=datetime.now(UTC),
                admit_model=config.accepts_codex_model,
            )
        except TaskAdmissionRejected:
            return _error(422, "invalid_request", "The evaluation request is invalid.")
        except TaskConflict:
            return _error(409, "idempotency_conflict", "The idempotency key belongs to a different request.")
        return JSONResponse(
            status_code=201 if created else 200,
            content=_task_payload(record, task_store),
            headers=_NO_STORE,
        )

    @app.get("/api/tasks")
    def list_tasks(
        status: TaskStatus | None = None,
        order: Literal["oldest", "newest"] = "oldest",
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> Response:
        items = task_store.list_tasks(status=status, order=order, limit=limit, offset=offset)
        return JSONResponse(content=[_summary_payload(item, task_store) for item in items], headers=_NO_STORE)

    @app.get("/api/tasks/{task_id}")
    def get_task(task_id: str) -> Response:
        try:
            return JSONResponse(content=_task_payload(task_store.get(task_id), task_store), headers=_NO_STORE)
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")

    @app.post("/api/tasks/{task_id}/cancel")
    def cancel_task(task_id: str) -> Response:
        try:
            record = task_store.cancel_queued(task_id, now=datetime.now(UTC))
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        except TaskConflict:
            return _error(409, "task_conflict", "The evaluation task cannot be cancelled in its current state.")
        return JSONResponse(content=_task_payload(record, task_store), headers=_NO_STORE)

    @app.get("/api/tasks/{task_id}/events")
    def events(task_id: str, request: Request) -> Response:
        try:
            task_store.get(task_id)
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        return StreamingResponse(
            TaskEventStream(request, task_store, task_id, poll_seconds=config.poll_seconds),
            media_type="text/event-stream",
            headers={**_NO_STORE, "X-Accel-Buffering": "no"},
        )

    def report_record(task_id: str) -> TaskRecord | JSONResponse:
        try:
            record = task_store.get(task_id)
        except TaskNotFound:
            return _error(404, "task_not_found", "The requested evaluation task does not exist.")
        if record.status is not TaskStatus.SUCCEEDED or record.result is None:
            return _error(409, "report_unavailable", "The evaluation report is not available.")
        return record

    @app.get("/api/tasks/{task_id}/report")
    def report(task_id: str) -> Response:
        record = report_record(task_id)
        if isinstance(record, JSONResponse):
            return record
        try:
            retained_root = config.run_root / "runs"
            projected = load_report(task_run_dir(record, retained_root), retained_root)
            projected = projected.model_copy(update={"task_id": record.task_id})
        except (ReportingError, OSError):
            return _error(409, "report_unavailable", "The evaluation report is not available.")
        return JSONResponse(content=projected.model_dump(mode="json"), headers=_NO_STORE)

    @app.get("/api/tasks/{task_id}/report.md")
    def raw_report(task_id: str) -> Response:
        record = report_record(task_id)
        if isinstance(record, JSONResponse):
            return record
        try:
            retained_root = config.run_root / "runs"
            markdown = load_raw_report(task_run_dir(record, retained_root), retained_root)
        except (InvalidReportArtifact, UnsafeReportPath, OSError):
            return _error(409, "report_unavailable", "The evaluation report is not available.")
        return PlainTextResponse(markdown, media_type="text/plain; charset=utf-8", headers=_NO_STORE)

    @app.get("/api/{path:path}")
    def unknown_api_get(path: str) -> JSONResponse:
        return _error(404, "not_found", "The requested API route does not exist.")

    @app.api_route(
        "/api/{path:path}",
        methods=["POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD", "TRACE", "CONNECT"],
    )
    def unknown_api(path: str) -> JSONResponse:
        return _error(404, "not_found", "The requested API route does not exist.")

    frontend_snapshot = _snapshot_frontend(config.frontend_dist, config.root)
    if frontend_snapshot is not None:

        @app.get("/assets/{asset_path:path}")
        def frontend_asset(asset_path: str) -> Response:
            asset = frontend_snapshot.assets.get(asset_path)
            if asset is None:
                return PlainTextResponse("Not found.", status_code=404)
            content, media_type = asset
            cache = (
                "public, max-age=31536000, immutable"
                if re.search(r"[-.][A-Za-z0-9_-]{8,}\.", Path(asset_path).name)
                else "no-cache"
            )
            return Response(content, media_type=media_type, headers={"Cache-Control": cache})

        @app.get("/{path:path}")
        def frontend_fallback(path: str) -> Response:
            return Response(
                frontend_snapshot.index,
                media_type="text/html",
                headers={"Cache-Control": "no-store"},
            )
    else:

        @app.get("/assets/{asset_path:path}")
        def frontend_asset_unavailable(asset_path: str) -> Response:
            return PlainTextResponse("Evaluation console frontend is not built.", status_code=503)

        @app.get("/{path:path}")
        def frontend_unavailable(path: str) -> Response:
            return PlainTextResponse("Evaluation console frontend is not built.", status_code=503)

    return app
