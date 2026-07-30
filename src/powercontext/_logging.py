"""Failure-isolated helpers for operational logging."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any


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


__all__ = ["log_safely"]
