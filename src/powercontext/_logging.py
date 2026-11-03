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

"""Failure-isolated helpers for operational logging."""

from __future__ import annotations

import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from typing import Any

_log_context: ContextVar[Mapping[str, str | int | None] | None] = ContextVar("powercontext_log_context", default=None)


@contextmanager
def log_context(fields: Mapping[str, str | int | None]) -> Iterator[None]:
    """Bind owner-supplied execution fields without configuring process logging."""

    token = _log_context.set({**current_log_context(), **fields})
    try:
        yield
    finally:
        _log_context.reset(token)


def current_log_context() -> Mapping[str, str | int | None]:
    return _log_context.get() or {}


def is_inference_failure_code(code: str) -> bool:
    """Report whether a diagnostic code names an inference failure observed at a boundary.

    Covers the registered ``model_*`` codes and invalid generated output codes,
    which name the same failure family without carrying a prefix.
    """

    value = code.lower()
    return value.startswith(("model_", "inference")) or value in {
        "invalid_model_output",
        "invalid_topic_placeholder",
        "invalid_topic_coordination_content",
    }


def error_category(code: str | None, *, status_code: int | None = None, outcome: str | None = None) -> str | None:
    """Classify diagnostic codes into a bounded, content-free operational vocabulary."""

    value = (code or "").lower()
    categories = (
        (outcome == "cancelled", "cancelled"),
        ("timeout" in value, "timeout"),
        (status_code == 401 or "authentication" in value, "authentication"),
        (status_code == 403 or "authorization" in value or "accessdenied" in value, "authorization"),
        # A diagnostic code that names a vocabulary term describes its boundary more precisely than a
        # catch-all status does, so the naming rules decide before the status-derived fallbacks.
        (
            "configuration" in value or "misconfigured" in value or "disabled" in value or "unconfigured" in value,
            "configuration",
        ),
        ("integrity_failed" in value or "build_failed" in value, "internal"),
        (status_code == 429 or "rate_limit" in value, "rate_limit"),
        (status_code == 404, "not_found"),
        (status_code == 409 or "conflict" in value, "conflict"),
        (status_code is not None and 400 <= status_code < 500, "invalid_request"),
        (
            status_code in (502, 503, 504) or "unavailable" in value or is_inference_failure_code(value),
            "dependency",
        ),
        (bool(outcome == "failure" or code or (status_code is not None and status_code >= 500)), "internal"),
    )
    return next((category for applies, category in categories if applies), None)


def log_safely(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    exc_info: BaseException | None = None,
    extra: Mapping[str, Any] | None = None,
) -> None:
    """Emit one operational record without changing authoritative behavior."""

    with suppress(Exception):
        logger.log(level, message, exc_info=exc_info, extra=None if extra is None else dict(extra))


__all__ = ["current_log_context", "error_category", "is_inference_failure_code", "log_context", "log_safely"]
