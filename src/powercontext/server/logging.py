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
import logging.handlers
import os
import shutil
import socket
import sys
import tempfile
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
from logging.handlers import QueueHandler, QueueListener, RotatingFileHandler
from pathlib import Path
from queue import Full, Queue
from threading import Lock
from typing import TYPE_CHECKING, Any, TextIO

from filelock import FileLock
from opentelemetry import trace
from opentelemetry._logs import LogRecord, SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
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
    "queue_wait_ms",
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
    "last_error_code",
    "last_error_stage",
    "last_error_at",
    "last_error_worker_id",
    "last_error_trace_id",
)
_OPERATIONAL_FIELDS = (
    "event",
    "operation",
    "outcome",
    "request_id",
    "receipt_id",
    "transport",
    "unit",
    "budget_unit",
    "budget_dimension",
    "budget_phase",
    "estimated_value",
    "limit_value",
    "output_reserve_tokens",
    "safety_margin_tokens",
    "estimator_id",
    "estimator_version",
    "duration_ms",
    "status_code",
    "provider",
    "model_capability",
    "provider_retry",
    "provider_error_code",
    "retrieval_family",
    "retrieval_outcome",
    "read_outcome",
    "result_count",
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
    "service_target",
    "service_owner_role",
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
_MISSING_OTLP_LOG_EXPORTER = (
    "OpenTelemetry log export requires the 'tracing-otlp' extra; "
    "install 'powercontext[server,tracing-otlp]' before enabling OTLP logs"
)
_SEVERITY_NUMBERS = {
    logging.DEBUG: SeverityNumber.DEBUG,
    logging.INFO: SeverityNumber.INFO,
    logging.WARNING: SeverityNumber.WARN,
    logging.ERROR: SeverityNumber.ERROR,
    logging.CRITICAL: SeverityNumber.FATAL,
}


def _operational_payload(record: logging.LogRecord) -> dict[str, Any]:
    """Return only the documented structured fields, never arbitrary record extras."""

    payload = {field: value for field in _OPERATIONAL_FIELDS if (value := getattr(record, field, None)) is not None}
    if str(getattr(record, "event", "")).startswith("code."):
        payload.update((field, value) for field in _CODE_FIELDS if (value := getattr(record, field, None)) is not None)
    return payload


class OperationalContextFilter(logging.Filter):
    def __init__(self, *, role: str = "server", target: str | None = None, owner_role: str | None = None) -> None:
        super().__init__()
        resource = service_resource(role, target=target, owner_role=owner_role).attributes
        self._identity = {
            "service_name": resource.get("service.name"),
            "service_version": resource.get("service.version"),
            "service_instance_id": resource.get("service.instance.id"),
            "service_role": resource.get("powercontext.service.role"),
            "service_target": resource.get("powercontext.service.target"),
            "service_owner_role": resource.get("powercontext.service.owner_role"),
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
        payload.update(_operational_payload(record))
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


class _OperationalFileHandler(logging.handlers.RotatingFileHandler):
    """Bound per-process JSON files; isolate writes and reopen after generation pruning."""

    def __init__(self, directory: str, role: str) -> None:
        root = Path(directory)
        with suppress(OSError):
            root.mkdir(parents=True, exist_ok=True)
        path = root / f"{role}-{os.getpid()}.jsonl"
        # Keep at most 64 process generations. A pruned live handler reopens on its next event.
        with suppress(OSError):
            generations = sorted(root.glob("*.jsonl"), key=lambda item: item.stat().st_mtime, reverse=True)
            for obsolete in generations[63:]:
                if obsolete != path:
                    for candidate in (obsolete, *root.glob(obsolete.name + ".*")):
                        with suppress(OSError):
                            candidate.unlink()
        super().__init__(path, maxBytes=10 * 1024 * 1024, backupCount=3, encoding="utf-8", delay=True)
        self._failure_reported = False

    @override
    def emit(self, record: logging.LogRecord) -> None:
        if self.stream is not None and not Path(self.baseFilename).exists():
            self.stream.close()
            self.stream = None
        super().emit(record)

    @override
    def handle(self, record: logging.LogRecord) -> bool:
        try:
            return super().handle(record)
        except Exception:
            self.handleError(record)
            return False

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        if not self._failure_reported:
            self._failure_reported = True
            with suppress(Exception):
                sys.stderr.write('{"event":"observability.log_failed","outcome":"failure"}\n')


class _OperationalOtelLogHandler(logging.Handler):
    """Export only the operational allowlist through OTLP and isolate exporter failures."""

    def __init__(self, provider: LoggerProvider) -> None:
        super().__init__()
        self._provider = provider
        self._failure_reported = False

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            payload = _operational_payload(record)
            event = payload.get("event")
            if not isinstance(event, str):
                event = "log.record"
                payload["event"] = event
            logger = self._provider.get_logger(record.name)
            logger.emit(
                LogRecord(
                    timestamp=int(record.created * 1_000_000_000),
                    severity_text=record.levelname,
                    severity_number=_SEVERITY_NUMBERS.get(record.levelno, SeverityNumber.INFO),
                    body=payload,
                    event_name=event,
                )
            )
        except Exception:
            self.handleError(record)

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        if not self._failure_reported:
            self._failure_reported = True
            with suppress(Exception):
                sys.stderr.write('{"event":"observability.log_failed","outcome":"failure"}\n')


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
    config: ServerLoggingConfig,
    *,
    role: str = "server",
    channel: socket.socket | None = None,
    target: str | None = None,
    owner_role: str | None = None,
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
    configuration: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "operational": {"()": _HumanContextFilter, "role": role, "target": target, "owner_role": owner_role},
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
    }
    _configure_file_output(configuration, config, role=role)
    if config.otlp_enabled:
        _configure_otlp_output(configuration, role=role, target=target, owner_role=owner_role)
    logging.config.dictConfig(configuration)


def _configure_file_output(configuration: dict[str, Any], config: ServerLoggingConfig, *, role: str) -> None:
    # The managed receiver owns file mode. An explicit directory can add JSON
    # copies to stdout mode; the default directory must not enable file output.
    if not config.file and "directory" in config.model_fields_set:
        configuration["formatters"]["json_file"] = {"()": JsonFormatter}
        configuration["handlers"]["json_file"] = {
            "()": _OperationalFileHandler,
            "directory": str(config.directory),
            "role": role,
            "formatter": "json_file",
            "filters": ["operational"],
        }
        for logger in configuration["loggers"].values():
            logger["handlers"].append("json_file")


def _configure_otlp_output(
    configuration: dict[str, Any], *, role: str, target: str | None, owner_role: str | None
) -> None:
    try:
        exporter_type = import_module("opentelemetry.exporter.otlp.proto.http._log_exporter").OTLPLogExporter
    except ImportError as error:
        raise RuntimeError(_MISSING_OTLP_LOG_EXPORTER) from error
    provider = LoggerProvider(
        resource=service_resource(role, target=target, owner_role=owner_role),
        shutdown_on_exit=False,
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter_type()))
    atexit.register(provider.shutdown)
    configuration["handlers"]["otlp"] = {
        "()": _OperationalOtelLogHandler,
        "provider": provider,
        "filters": ["operational"],
    }
    for logger in configuration["loggers"].values():
        logger["handlers"].append("otlp")


@dataclass(frozen=True)
class WorkerLogging:
    """Picklable Server-owned bootstrap; unconfigured SDK workers do not use it."""

    config: ServerLoggingConfig
    channel: socket.socket | None = None
    target: str | None = None
    owner_role: str | None = None

    def __call__(self) -> None:
        configure_server_logging(
            self.config, role="worker", channel=self.channel, target=self.target, owner_role=self.owner_role
        )


@dataclass(frozen=True)
class _WorkerLoggingPreparation:
    """Keep the Server sink local and prepare a dedicated channel for each spawn."""

    config: ServerLoggingConfig
    sink: _OperationalQueueHandler
    target: str | None = None
    owner_role: str | None = None

    @contextmanager
    def prepare(self) -> Iterator[WorkerLogging]:
        with ExitStack() as resources:
            try:
                channel = resources.enter_context(self.sink.worker_channel())
            except Exception:
                # Channel admission cannot change the task's business result.
                self.sink._notify_failure()
                channel = None
            yield WorkerLogging(self.config, channel, target=self.target, owner_role=self.owner_role)


@contextmanager
def worker_logging(
    config: ServerLoggingConfig, *, role: str, target: str | None = None, owner_role: str | None = None
) -> Iterator[WorkerLogging | _WorkerLoggingPreparation]:
    """Reuse the process sink, or own a worker-only sink in an embedded Server."""

    if not config.file:
        yield WorkerLogging(config, target=target, owner_role=owner_role)
        return
    for handler in logging.getLogger("powercontext").handlers:
        if isinstance(handler, _OperationalQueueHandler) and handler.config == config and handler._listener is not None:
            yield _WorkerLoggingPreparation(config, handler, target=target, owner_role=owner_role)
            return
    handler = _OperationalQueueHandler(config, role=role)
    try:
        yield _WorkerLoggingPreparation(config, handler, target=target, owner_role=owner_role)
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
