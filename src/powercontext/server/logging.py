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

import json
import logging
import logging.config
import sys
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import Lock
from typing import TYPE_CHECKING, Any, TextIO

from opentelemetry import trace
from typing_extensions import override

from powercontext._logging import current_log_context, error_category
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
            fields += _CODE_FIELDS
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
    """Return process-local records the configured handler could not deliver."""

    with _LOG_FAILURE_LOCK:
        return _LOG_FAILURES


class _OperationalStreamHandler(_StreamHandler):
    """Report one bounded failure without Python logging's raw-record stderr dump."""

    def __init__(self, stream: TextIO | None = None) -> None:
        super().__init__(stream)
        self._failure_reported = False

    @override
    def handle(self, record: logging.LogRecord) -> bool:
        try:
            return super().handle(record)
        except Exception:
            self.handleError(record)
            return False

    @override
    def handleError(self, record: logging.LogRecord) -> None:
        global _LOG_FAILURES
        with _LOG_FAILURE_LOCK:
            _LOG_FAILURES += 1
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


def configure_server_logging(config: ServerLoggingConfig, *, role: str = "server") -> None:
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
                "()": _OperationalStreamHandler,
                "stream": "ext://sys.stdout",
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

    def __call__(self) -> None:
        configure_server_logging(self.config, role="worker")


__all__ = ["JsonFormatter", "OperationalContextFilter", "configure_server_logging"]
