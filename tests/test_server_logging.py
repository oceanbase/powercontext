from __future__ import annotations

import json
import logging

from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter, OperationalContextFilter
from powercontext.server.settings import McpConfig, ServerLoggingConfig, ServerSettings


def test_json_formatter_emits_stable_operational_fields() -> None:
    record = logging.makeLogRecord({
        "name": "powercontext.server.access",
        "levelno": logging.INFO,
        "levelname": "INFO",
        "msg": "request completed",
        "event": "transport.request.completed",
        "operation": "search_memory",
        "outcome": "success",
        "request_id": "request-123",
        "transport": "http",
        "ignored": "not serialized",
    })
    OperationalContextFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "request completed"
    assert payload["event"] == "transport.request.completed"
    assert payload["operation"] == "search_memory"
    assert payload["request_id"] == "request-123"
    assert payload["transport"] == "http"
    assert "ignored" not in payload


def test_server_access_log_uses_operation_id_and_skips_health(caplog, tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}"),
            mcp=McpConfig(enabled=False),
        )
    )

    with caplog.at_level(logging.INFO, logger="powercontext.server.access"), TestClient(app) as client:
        capabilities = client.get("/v1/capabilities")
        health = client.get("/health/live")

    records = [record for record in caplog.records if getattr(record, "event", None) == "transport.request.completed"]
    assert capabilities.status_code == 200
    assert health.status_code == 200
    assert len(records) == 1
    assert records[0].operation == "get_capabilities"
    assert records[0].outcome == "success"
    assert records[0].request_id == capabilities.headers["X-PowerContext-Request-ID"]
    assert records[0].transport == "http"


def test_server_logging_settings_normalize_the_level(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_LEVEL", "warning")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_FORMAT", "json")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_ACCESS", "false")

    settings = ServerSettings()

    assert settings.logging == ServerLoggingConfig(level="WARNING", format="json", access=False)
