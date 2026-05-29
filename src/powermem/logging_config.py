"""
Configure the ``powermem`` logger tree from ``LOGGING_*`` environment variables.

- ``LOGGING_FILE`` (default ``./logs/powermem.log``): file for SDK/storage logs
- ``LOGGING_LEVEL`` / ``LOGGING_FORMAT``: level and text format for file output
- ``LOGGING_MAX_SIZE`` / ``LOGGING_BACKUP_COUNT``: rotating file handler
- ``LOGGING_COMPRESS_BACKUPS``: gzip rotated backup files when true
- ``LOGGING_CONSOLE_*``: optional stderr console output for the SDK tree
"""

from __future__ import annotations

import gzip
import logging
import os
import shutil
import sys
from logging.handlers import RotatingFileHandler
from typing import Optional

_powermem_logging_configured = False


def parse_log_max_bytes(size_str: Optional[str], default: int = 100 * 1024 * 1024) -> int:
    """Parse size strings such as ``100MB``, ``1GB``, or plain byte counts."""
    if not size_str:
        return default
    text = str(size_str).strip().upper()
    try:
        if text.endswith("GB"):
            return int(float(text[:-2].strip()) * 1024 * 1024 * 1024)
        if text.endswith("MB"):
            return int(float(text[:-2].strip()) * 1024 * 1024)
        if text.endswith("KB"):
            return int(float(text[:-2].strip()) * 1024)
        return int(text)
    except ValueError:
        return default


class CompressingRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that optionally gzip-compresses rolled backup files."""

    def __init__(self, *args, compress_backups: bool = False, **kwargs):
        self.compress_backups = compress_backups
        super().__init__(*args, **kwargs)

    def doRollover(self) -> None:
        super().doRollover()
        if not self.compress_backups or self.backupCount <= 0:
            return
        for index in range(1, self.backupCount + 1):
            rotated = f"{self.baseFilename}.{index}"
            if not os.path.exists(rotated) or rotated.endswith(".gz"):
                continue
            gz_path = f"{rotated}.gz"
            with open(rotated, "rb") as source, gzip.open(gz_path, "wb") as dest:
                shutil.copyfileobj(source, dest)
            os.remove(rotated)


def setup_powermem_logging(*, force: bool = False) -> bool:
    """
    Wire ``LOGGING_*`` settings to the ``powermem`` logger namespace.

    Returns True when file logging was configured, False otherwise.
    Safe to call multiple times; repeats are no-ops unless ``force=True``.
    """
    global _powermem_logging_configured

    if _powermem_logging_configured and not force:
        return False

    try:
        from powermem.config_loader import LoggingSettings
    except Exception as exc:
        print(f"Warning: powermem logging setup skipped: {exc}", file=sys.stderr)
        return False

    settings = LoggingSettings()
    if not settings.file:
        return False

    log_level = getattr(logging, (settings.level or "INFO").upper(), logging.INFO)
    file_formatter = logging.Formatter(
        settings.format or "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    log_file_path = os.path.abspath(settings.file)
    log_dir = os.path.dirname(log_file_path)
    if log_dir:
        os.makedirs(log_dir, exist_ok=True)

    handler_kwargs = {
        "mode": "a",
        "maxBytes": parse_log_max_bytes(settings.max_size),
        "backupCount": settings.backup_count or 5,
        "encoding": "utf-8",
    }
    if settings.compress_backups:
        file_handler = CompressingRotatingFileHandler(
            log_file_path, compress_backups=True, **handler_kwargs
        )
    else:
        file_handler = RotatingFileHandler(log_file_path, **handler_kwargs)
    file_handler.setLevel(log_level)
    file_handler.setFormatter(file_formatter)

    powermem_logger = logging.getLogger("powermem")
    powermem_logger.setLevel(log_level)

    # Replace prior file handlers targeting the same path (idempotent reconfigure).
    for existing in list(powermem_logger.handlers):
        if getattr(existing, "baseFilename", None) == log_file_path:
            powermem_logger.removeHandler(existing)
            existing.close()

    powermem_logger.addHandler(file_handler)

    if settings.console_enabled:
        console_level = getattr(
            logging, (settings.console_level or settings.level or "INFO").upper(), logging.INFO
        )
        console_handler = logging.StreamHandler(sys.stderr)
        console_handler.setLevel(console_level)
        console_handler.setFormatter(
            logging.Formatter(settings.console_format or "%(levelname)s - %(message)s")
        )
        has_console = any(
            isinstance(h, logging.StreamHandler)
            and not getattr(h, "baseFilename", None)
            and getattr(h, "stream", None) is sys.stderr
            for h in powermem_logger.handlers
        )
        if not has_console:
            powermem_logger.addHandler(console_handler)

    powermem_logger.propagate = False

    powermem_logger.debug("PowerMem SDK logging initialized (file=%s)", log_file_path)
    _powermem_logging_configured = True
    return True
