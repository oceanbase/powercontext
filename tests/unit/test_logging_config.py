"""Tests for powermem.logging_config — SDK file logging wiring."""

import gzip
import logging
import os

import pytest

from powermem.log_context import (
    TraceContextFilter,
    reset_log_context,
    set_log_context,
)
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

# ---------------------------------------------------------------------------
# TraceContextFilter
# ---------------------------------------------------------------------------

def test_trace_filter_injects_fields(tmp_path):
    log_file = str(tmp_path / "trace.log")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(message)s [%(request_id)s] [%(user_id)s] [%(agent_id)s]"
    ))
    handler.addFilter(TraceContextFilter())

    test_logger = logging.getLogger("powermem.trace_test")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    tokens = set_log_context(
        request_id="aaaa-bbbb",
        user_id="user-42",
        agent_id="agent-7",
    )
    try:
        test_logger.debug("hello")
        handler.flush()

        with open(log_file) as f:
            content = f.read()
        assert "[aaaa-bbbb]" in content
        assert "[user-42]" in content
        assert "[agent-7]" in content
    finally:
        reset_log_context(tokens)
        test_logger.removeHandler(handler)
        handler.close()


def test_trace_filter_zero_uuid_default(tmp_path):
    log_file = str(tmp_path / "default.log")
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(message)s [%(request_id)s] [%(user_id)s] [%(agent_id)s]"
    ))
    handler.addFilter(TraceContextFilter())

    test_logger = logging.getLogger("powermem.default_test")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        test_logger.debug("no context")
        handler.flush()

        with open(log_file) as f:
            content = f.read()
        zero = "00000000-0000-0000-0000-000000000000"
        assert f"[{zero}]" in content  # request_id defaults to zero UUID
        assert "[] []" in content  # user_id and agent_id default to empty
    finally:
        test_logger.removeHandler(handler)
        handler.close()


def test_reset_log_context_restores_previous():
    tokens_outer = set_log_context(request_id="outer-id")
    try:
        tokens_inner = set_log_context(request_id="inner-id")
        from powermem.log_context import _request_id_var
        assert _request_id_var.get() == "inner-id"
        reset_log_context(tokens_inner)
        assert _request_id_var.get() == "outer-id"
    finally:
        reset_log_context(tokens_outer)


def test_setup_powermem_logging_attaches_trace_filter(tmp_path, monkeypatch):
    log_file = str(tmp_path / "trace_setup.log")
    monkeypatch.setenv("LOGGING_FILE", log_file)
    monkeypatch.setenv("LOGGING_LEVEL", "DEBUG")
    monkeypatch.setenv("LOGGING_CONSOLE_ENABLED", "false")
    monkeypatch.setenv(
        "LOGGING_FORMAT",
        "%(asctime)s - %(name)s - %(levelname)s - [%(request_id)s] - %(message)s",
    )

    import powermem.logging_config as mod
    monkeypatch.setattr(mod, "_powermem_logging_configured", False)

    tokens = set_log_context(request_id="req-from-setup-test")
    try:
        setup_powermem_logging(force=True)

        test_logger = logging.getLogger("powermem.setup_trace")
        test_logger.debug("trace check")

        with open(log_file) as f:
            content = f.read()
        assert "req-from-setup-test" in content
    finally:
        reset_log_context(tokens)
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

        gz_files = sorted(f for f in os.listdir(tmp_path) if f.endswith(".gz"))
        assert gz_files, "Expected at least one .gz backup"
        assert all(name.startswith("rotate.log.") for name in gz_files)

        with gzip.open(tmp_path / gz_files[0], "rt") as f:
            assert "padding" in f.read()
    finally:
        test_logger.removeHandler(handler)
        handler.close()


def test_compressing_handler_respects_backup_count(tmp_path):
    log_file = str(tmp_path / "rotate.log")
    handler = CompressingRotatingFileHandler(
        log_file,
        compress_backups=True,
        maxBytes=40,
        backupCount=2,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter("%(message)s"))

    test_logger = logging.getLogger("powermem.compress_backup_count")
    test_logger.addHandler(handler)
    test_logger.setLevel(logging.DEBUG)

    try:
        for i in range(80):
            test_logger.debug("line %d xxxxxxxxxxxxxxxxxxxxxxxx", i)

        gz_files = [f for f in os.listdir(tmp_path) if f.endswith(".gz")]
        assert len(gz_files) <= 2, f"Expected at most 2 backups, got {gz_files}"
        plain_backups = [
            f
            for f in os.listdir(tmp_path)
            if f.startswith("rotate.log.") and not f.endswith(".gz")
        ]
        assert not plain_backups, f"Uncompressed backups should be migrated: {plain_backups}"
    finally:
        test_logger.removeHandler(handler)
        handler.close()
