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

"""Server-owned standard library logging configuration."""

from __future__ import annotations

import atexit
import gzip
import json
import logging
import logging.config
import shutil
import socket
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import Full, Queue
from threading import Lock
from typing import TYPE_CHECKING, Any, TextIO

from filelock import FileLock
from opentelemetry import trace
from typing_extensions import override

from powercontext._logging import current_log_context, error_category
from powercontext.server._log_transport import LogReceiver, LogSender
from powercontext.server.context import current_request_id
from powercontext.server.settings import ServerLoggingConfig
from powercontext.server.telemetry import service_resource

if TYPE_CHECKING:
    _StreamHandler = logging.StreamHandler[TextIO]
else:
    _StreamHandler = logging.StreamHandler

_PROCESSING_FIELDS = (
    "stage",
    "binding",
    "family",
    "scope",
    "worker_id",
    "request_generation",
    "supervisor_group",
    "supervisor_generation",
    "trigger",
    "exception_type",
    "retry_count",
    "retry_delay_seconds",
    "source_after",
    "source_through",
    "attempts",
    "requests",
    "tokens",
    "processing_stage",
    "failure_reason",
    "input_item_count",
    "estimated_input_tokens",
    "token_estimator_id",
    "effective_budget",
    "provider_request_id",
    "finish_reason",
    "failure_location",
    "cause_exception_type",
)
_OPERATIONAL_FIELDS = (
    "event",
    "operation",
    "outcome",
    "request_id",
    "transport",
    "unit",
    "duration_ms",
    "status_code",
    "error_code",
    "error_category",
    "code",
    "mode",
    "source_count",
    "held_count",
    "hold_codes",
    "consumer",
    "policy_id",
    "policy_version",
    "privacy_boundary",
    "privacy_outcome",
    "coverage",
    "verdict",
    "final_action",
    "fallback_reason",
    "reason",
    "candidate_count",
    "evidence_count",
    "trace_id",
    "span_id",
    "service_name",
    "service_version",
    "service_role",
    "service_instance_id",
    *_PROCESSING_FIELDS,
)
_CODE_FIELDS = (
    "seconds",
    "process_lifetime_max_rss_bytes",
    "children_lifetime_max_rss_bytes",
    "selected_count",
    "code_operation",
    "hit_count",
    "hit_count_is_lower_bound",
    "cache_bytes",
    "status",
    "limitations",
    "reason",
)


class OperationalContextFilter(logging.Filter):
    def __init__(self, *, role: str = "server") -> None:
        super().__init__()
        resource = service_resource(role).attributes
        self._identity = {
            "service_name": resource.get("service.name"),
            "service_version": resource.get("service.version"),
            "service_instance_id": resource.get("service.instance.id"),
            "service_role": resource.get("powercontext.service.role"),
        }

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        context = current_log_context()
        if getattr(record, "request_id", None) is None:
            record.request_id = context.get("request_id", current_request_id())
        for field in _OPERATIONAL_FIELDS:
            if getattr(record, field, None) is None:
                setattr(record, field, context.get(field))
        for field, value in self._identity.items():
            setattr(record, field, value)
        if getattr(record, "error_category", None) is None:
            record.error_category = error_category(
                getattr(record, "error_code", None),
                status_code=getattr(record, "status_code", None),
                outcome=getattr(record, "outcome", None),
            )
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            if getattr(record, "trace_id", None) is None:
                record.trace_id = format(span_context.trace_id, "032x")
            if getattr(record, "span_id", None) is None:
                record.span_id = format(span_context.span_id, "016x")
        return True


class JsonFormatter(logging.Formatter):
    """Render a stable operational record without serializing arbitrary extras."""

    @override
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        payload.update(
            (field, value) for field in _OPERATIONAL_FIELDS if (value := getattr(record, field, None)) is not None
        )
        if str(getattr(record, "event", "")).startswith("code."):
            payload.update(
                (field, value) for field in _CODE_FIELDS if (value := getattr(record, field, None)) is not None
            )
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _HumanContextFilter(OperationalContextFilter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        super().filter(record)
        request_id = getattr(record, "request_id", None)
        trace_id = getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)
        record.request_context = "" if request_id is None else f" request_id={request_id}"
        record.trace_context = (
            "" if trace_id is None else f" trace_id={trace_id}" + ("" if span_id is None else f" span_id={span_id}")
        )
        event = getattr(record, "event", None)
        # Correlation and diagnostics apply to every event, including nested code stages.
        fields = tuple(
            field
            for field in _OPERATIONAL_FIELDS
            if field not in self._identity and field not in {"request_id", "trace_id", "span_id"}
        )
        if isinstance(event, str) and event.startswith("code."):
            fields = tuple(dict.fromkeys((*fields, *_CODE_FIELDS)))
        record.processing_context = "".join(
            f" {field}={json.dumps(value, ensure_ascii=False)}"
            for field in fields
            if (value := getattr(record, field, None)) is not None
        )
        record.service_context = "".join(
            f" {field}={json.dumps(value, ensure_ascii=False)}" for field, value in self._identity.items()
        )
        return True


_LOG_FAILURE_LOCK = Lock()
_LOG_FAILURES = 0


def log_record_failures() -> int:
    """Return observed process-local log records that could not be delivered."""

    with _LOG_FAILURE_LOCK:
        return _LOG_FAILURES


class _FailureIsolatedHandler(logging.Handler):
    """Report one bounded failure without Python logging's raw-record stderr dump."""

    _failure_reported = False

    @override
    def handle(self, record: logging.LogRecord) -> bool:
        try:
            return super().handle(record)
        except Exception:
            self.handleError(record)
            return False

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        self._record_failures()

    def _record_failures(self, count: int = 1) -> None:
        global _LOG_FAILURES
        with _LOG_FAILURE_LOCK:
            _LOG_FAILURES += count
        self._notify_failure()

    def _notify_failure(self) -> None:
        with _LOG_FAILURE_LOCK:
            if self._failure_reported:
                return
            self._failure_reported = True
        with suppress(Exception):
            sys.stderr.write('{"event":"observability.log_failed","outcome":"failure"}\n')


class _OperationalStreamHandler(_FailureIsolatedHandler, _StreamHandler):
    pass


class _OperationalRotatingFileHandler(_FailureIsolatedHandler, RotatingFileHandler):
    pass


def _gzip_archive(source: str, destination: str) -> None:
    """Publish complete archives before removing their uncompressed source."""

    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=Path(destination).parent, delete=False) as output:
            temporary = Path(output.name)
            with Path(source).open("rb") as input_stream, gzip.GzipFile(fileobj=output, mode="wb") as compressed:
                shutil.copyfileobj(input_stream, compressed)
        temporary.replace(destination)
        Path(source).unlink()
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class _OperationalSocketHandler(_FailureIsolatedHandler):
    def __init__(self, channel: socket.socket) -> None:
        super().__init__()
        self._sender = LogSender(channel)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        self._sender.send(self.format(record))

    @override
    def close(self) -> None:
        self.acquire()
        try:
            self._sender.close()
        finally:
            self.release()
            super().close()


class _DrainingQueueListener(QueueListener):
    queue: Queue[logging.LogRecord | None]
    _sentinel = None

    @override
    def enqueue_sentinel(self) -> None:
        # The default put_nowait can fail when a bounded queue is full at shutdown.
        self.queue.put(self._sentinel)


class _OperationalQueueHandler(_FailureIsolatedHandler, QueueHandler):
    """Own native file handlers and receive independent Worker log streams."""

    def __init__(self, config: ServerLoggingConfig, *, role: str) -> None:
        self.config = config
        self._resources = ExitStack()
        self._listener: QueueListener | None = None
        self._receiver: LogReceiver | None = None
        self._output_queue: Queue[logging.LogRecord | None] = Queue(maxsize=1024)
        try:
            config.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            filename = config.directory / f"{role}.log"
            lock = FileLock(str(filename) + ".lock", thread_local=False)
            lock.acquire(timeout=0)
            self._resources.callback(lock.release)
            file_handler = _OperationalRotatingFileHandler(
                filename, maxBytes=config.max_bytes, backupCount=config.backup_count, encoding="utf-8"
            )
            self._resources.callback(file_handler.close)
            if config.compression == "gzip":
                file_handler.namer = lambda name: name + ".gz"
                file_handler.rotator = _gzip_archive
            # logging.shutdown closes handlers in reverse registration order.
            super().__init__(self._output_queue)
            listener = _DrainingQueueListener(self._output_queue, file_handler)
            listener.start()
            self._listener = listener
            self._receiver = LogReceiver(self._receive, self._notify_failure, self._record_failures)
            atexit.register(self.close)
        except BaseException:
            if self._listener is None:
                self._resources.close()
            else:
                self.close()
            raise

    @contextmanager
    def worker_channel(self) -> Iterator[socket.socket]:
        if self._receiver is None:
            raise RuntimeError("Server log receiver is closed")  # noqa: TRY003
        with self._receiver.channel() as channel:
            yield channel

    @override
    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return logging.LogRecord(record.name, record.levelno, "", 0, self.format(record), (), None)

    def _receive(self, message: str) -> None:
        record = logging.LogRecord("powercontext.worker", logging.INFO, "", 0, message, (), None)
        try:
            self._output_queue.put_nowait(record)
        except Full:
            self.handleError(record)

    @override
    def close(self) -> None:
        if self._receiver is not None:
            if not self._receiver.close():
                return
            self._receiver = None
        if self._listener is not None:
            # Drain Worker streams before placing the writer's final sentinel.
            self._listener.stop()
            self._listener = None
        try:
            self._resources.close()
        except Exception:
            self._notify_failure()
        finally:
            atexit.unregister(self.close)
            super().close()


class _UvicornDisplayNameFilter(logging.Filter):
    """Rewrite uvicorn's logger name for downstream display.

    Uvicorn emits lifecycle and error records through ``uvicorn.error``.
    Logger selection, hierarchy, and effective-level checks use that logger.
    This filter then rewrites only ``record.name``, so later filters on the
    same logger and downstream handler filters and formatters observe
    ``uvicorn``. Parent logger filters are not applied during propagation.
    """

    @override
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.error":
            record.name = "uvicorn"
        return True


def configure_server_logging(
    config: ServerLoggingConfig, *, role: str = "server", channel: socket.socket | None = None
) -> None:
    """Configure process logging for the foreground Server command."""

    formatter: dict[str, Any]
    if config.format == "json":
        formatter = {"()": JsonFormatter}
    else:
        formatter = {
            "format": (
                "%(asctime)s %(levelname)s %(name)s %(message)s"
                "%(processing_context)s%(request_context)s%(trace_context)s"
                "%(service_context)s"
            ),
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }

    if role not in {"server", "all", "api", "background", "worker"}:
        raise ValueError("Unsupported logging process role")  # noqa: TRY003
    if config.file and role == "worker" and channel is None:
        raise ValueError("File logging workers require a Server-owned log receiver")  # noqa: TRY003
    handler: dict[str, Any] = (
        {"()": _OperationalQueueHandler, "config": config, "role": role}
        if config.file
        else {"()": _OperationalStreamHandler, "stream": "ext://sys.stdout"}
    )
    if channel is not None:
        handler = {"()": _OperationalSocketHandler, "channel": channel}
    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "operational": {"()": _HumanContextFilter, "role": role},
            "uvicorn_display_name": {"()": _UvicornDisplayNameFilter},
        },
        "formatters": {"server": formatter},
        "handlers": {
            "server": {
                **handler,
                "formatter": "server",
                "filters": ["operational"],
            }
        },
        "loggers": {
            "powercontext": {
                "handlers": ["server"],
                "level": config.level,
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["server"],
                "level": config.level,
                "propagate": False,
                "filters": ["uvicorn_display_name"],
            },
        },
    })


@dataclass(frozen=True)
class WorkerLogging:
    """Picklable Server-owned bootstrap; unconfigured SDK workers do not use it."""

    config: ServerLoggingConfig
    channel: socket.socket | None = None

    def __call__(self) -> None:
        configure_server_logging(self.config, role="worker", channel=self.channel)


@dataclass(frozen=True)
class _WorkerLoggingPreparation:
    """Keep the Server sink local and prepare a dedicated channel for each spawn."""

    config: ServerLoggingConfig
    sink: _OperationalQueueHandler

    @contextmanager
    def prepare(self) -> Iterator[WorkerLogging]:
        with ExitStack() as resources:
            try:
                channel = resources.enter_context(self.sink.worker_channel())
            except Exception:
                # Channel admission cannot change the task's business result.
                self.sink._notify_failure()
                channel = None
            yield WorkerLogging(self.config, channel)


@contextmanager
def worker_logging(config: ServerLoggingConfig, *, role: str) -> Iterator[WorkerLogging | _WorkerLoggingPreparation]:
    """Reuse the process sink, or own a worker-only sink in an embedded Server."""

    if not config.file:
        yield WorkerLogging(config)
        return
    for handler in logging.getLogger("powercontext").handlers:
        if isinstance(handler, _OperationalQueueHandler) and handler.config == config and handler._listener is not None:
            yield _WorkerLoggingPreparation(config, handler)
            return
    handler = _OperationalQueueHandler(config, role=role)
    try:
        yield _WorkerLoggingPreparation(config, handler)
    finally:
        handler.close()


def shutdown_server_logging() -> None:
    """Drain Server-owned file output after the Runtime and tracing shut down."""

    for name in ("powercontext", "uvicorn.error"):
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            if isinstance(handler, _OperationalQueueHandler):
                handler.close()
                logger.removeHandler(handler)


__all__ = ["JsonFormatter", "OperationalContextFilter", "configure_server_logging"]
