"""Tests for powermem.logging_config — SDK file logging wiring."""

import gzip
import logging
import os

import pytest

from powermem.logging_config import (
    CompressingRotatingFileHandler,
    parse_log_max_bytes,
    setup_powermem_logging,
)


# ---------------------------------------------------------------------------
# parse_log_max_bytes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "input_str, expected",
    [
        ("100MB", 100 * 1024 * 1024),
        ("1GB", 1024 * 1024 * 1024),
        ("512KB", 512 * 1024),
        ("2048", 2048),
        ("  50mb  ", 50 * 1024 * 1024),
        ("1.5GB", int(1.5 * 1024 * 1024 * 1024)),
    ],
)
def test_parse_log_max_bytes_valid(input_str, expected):
    assert parse_log_max_bytes(input_str) == expected


def test_parse_log_max_bytes_none_returns_default():
    assert parse_log_max_bytes(None) == 100 * 1024 * 1024


def test_parse_log_max_bytes_empty_returns_default():
    assert parse_log_max_bytes("") == 100 * 1024 * 1024


def test_parse_log_max_bytes_invalid_returns_default():
    assert parse_log_max_bytes("not-a-number") == 100 * 1024 * 1024


def test_parse_log_max_bytes_custom_default():
    assert parse_log_max_bytes(None, default=42) == 42


# ---------------------------------------------------------------------------
# setup_powermem_logging
# ---------------------------------------------------------------------------

def test_setup_powermem_logging_writes_to_file(tmp_path, monkeypatch):
    log_file = str(tmp_path / "test.log")
    monkeypatch.setenv("LOGGING_FILE", log_file)
    monkeypatch.setenv("LOGGING_LEVEL", "DEBUG")
    monkeypatch.setenv("LOGGING_CONSOLE_ENABLED", "false")

    import powermem.logging_config as mod
    monkeypatch.setattr(mod, "_powermem_logging_configured", False)

    try:
        assert setup_powermem_logging(force=True) is True
        assert os.path.exists(log_file)

        test_logger = logging.getLogger("powermem.test_logging_config")
        test_logger.debug("hello from test")

        with open(log_file) as f:
            content = f.read()
        assert "PowerMem SDK logging initialized" in content
        assert "hello from test" in content
    finally:
        powermem_logger = logging.getLogger("powermem")
        for h in list(powermem_logger.handlers):
            powermem_logger.removeHandler(h)
            h.close()
        monkeypatch.setattr(mod, "_powermem_logging_configured", False)


def test_setup_powermem_logging_idempotent(tmp_path, monkeypatch):
    log_file = str(tmp_path / "test.log")
    monkeypatch.setenv("LOGGING_FILE", log_file)
    monkeypatch.setenv("LOGGING_CONSOLE_ENABLED", "false")

    import powermem.logging_config as mod
    monkeypatch.setattr(mod, "_powermem_logging_configured", False)

    try:
        assert setup_powermem_logging(force=True) is True
        assert setup_powermem_logging() is False  # no-op second call
    finally:
        powermem_logger = logging.getLogger("powermem")
        for h in list(powermem_logger.handlers):
            powermem_logger.removeHandler(h)
            h.close()
        monkeypatch.setattr(mod, "_powermem_logging_configured", False)


# ---------------------------------------------------------------------------
# CompressingRotatingFileHandler
# ---------------------------------------------------------------------------

def test_compressing_handler_creates_gz(tmp_path):
    log_file = str(tmp_path / "rotate.log")
    handler = CompressingRotatingFileHandler(
        log_file,
        compress_backups=True,
        maxBytes=50,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    test_logger = logging.getLogger("powermem.compress_test")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        for i in range(20):
            test_logger.debug("line %d padding padding padding", i)

        gz_files = [f for f in os.listdir(tmp_path) if f.endswith(".gz")]
        assert len(gz_files) > 0, "Expected at least one .gz backup"

        gz_path = str(tmp_path / gz_files[0])
        with gzip.open(gz_path, "rt") as f:
            content = f.read()
        assert "padding" in content
    finally:
        test_logger.removeHandler(handler)
        handler.close()
