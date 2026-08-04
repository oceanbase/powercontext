"""Server-owned standard library logging configuration."""

from __future__ import annotations

import json
import logging
import logging.config
from datetime import UTC, datetime
from typing import Any

from opentelemetry import trace

from powercontext.server.settings import ServerLoggingConfig

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
    "source_count",
    "trace_id",
    "span_id",
)


class OperationalContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            if getattr(record, "trace_id", None) is None:
                record.trace_id = format(span_context.trace_id, "032x")
            if getattr(record, "span_id", None) is None:
                record.span_id = format(span_context.span_id, "016x")
        for field in _OPERATIONAL_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, None)
        return True


class JsonFormatter(logging.Formatter):
    """Render a stable operational record without serializing arbitrary extras."""

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
        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class _HumanContextFilter(OperationalContextFilter):
    def filter(self, record: logging.LogRecord) -> bool:
        super().filter(record)
        request_id = getattr(record, "request_id", None)
        trace_id = getattr(record, "trace_id", None)
        span_id = getattr(record, "span_id", None)
        record.request_context = "" if request_id is None else f" request_id={request_id}"
        record.trace_context = (
            "" if trace_id is None else f" trace_id={trace_id}" + ("" if span_id is None else f" span_id={span_id}")
        )
        return True


def configure_server_logging(config: ServerLoggingConfig) -> None:
    """Configure process logging for the foreground Server command."""

    formatter: dict[str, Any]
    if config.format == "json":
        formatter = {"()": JsonFormatter}
    else:
        formatter = {
            "format": "%(asctime)s %(levelname)s %(name)s %(message)s%(request_context)s%(trace_context)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }

    logging.config.dictConfig({
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {"operational": {"()": _HumanContextFilter}},
        "formatters": {"server": formatter},
        "handlers": {
            "server": {
                "class": "logging.StreamHandler",
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
            },
        },
    })


__all__ = ["JsonFormatter", "OperationalContextFilter", "configure_server_logging"]
