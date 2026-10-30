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

from __future__ import annotations

import gzip
import json
import logging
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter, OperationalContextFilter
from powercontext.server.settings import McpConfig, ServerLoggingConfig, ServerSettings


@pytest.mark.parametrize("compression", ["none", "gzip"])
@pytest.mark.parametrize("log_format", ["console", "json"])
def test_file_logging_rotates_and_limits_readable_archives(tmp_path: Path, compression: str, log_format: str) -> None:
    script = """
import logging
import sys
from pathlib import Path
from powercontext.server.logging import configure_server_logging, shutdown_server_logging
from powercontext.server.settings import ServerLoggingConfig

config = ServerLoggingConfig(
    file=True, directory=Path(sys.argv[1]), format=sys.argv[3],
    max_bytes=2048, backup_count=2, compression=sys.argv[2],
)
configure_server_logging(config)
logger = logging.getLogger("powercontext.file")
for index in range(100):
    logger.info("record %s", index, extra={"source_body": lambda: None})
logger.info("private-message-sentinel", extra={"operation": object()})
logger.info("final record", extra={"event": "probe.completed", "outcome": "success"})
shutdown_server_logging()
configure_server_logging(config)
logging.getLogger("uvicorn.error").info("reopened record")
shutdown_server_logging()
"""
    directory = tmp_path / "nested" / "logs"
    result = subprocess.run(
        [sys.executable, "-c", script, str(directory), compression, log_format],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    archives = sorted(directory.glob("server.log.[0-9]*"))
    assert len(archives) == 2
    counts = {"final record": 0, "reopened record": 0}
    for path in [directory / "server.log", *archives]:
        assert (path.suffix == ".gz") == (compression == "gzip" and path in archives)
        open_log = gzip.open if path.suffix == ".gz" else open
        with open_log(path, "rt", encoding="utf-8") as stream:
            for line in stream:
                assert "private-message-sentinel" not in line and "source_body" not in line
                assert "record 0 " not in line
                for message in counts:
                    counts[message] += line.count(message)
                if log_format == "json":
                    record = json.loads(line)
                    if record["message"] == "reopened record":
                        assert record["logger"] == "uvicorn"
    assert counts == {"final record": 1, "reopened record": 1}
    assert result.stderr.splitlines() == ['{"event":"observability.log_failed","outcome":"failure"}']
    assert result.stdout == ""


def test_file_logging_flushes_on_normal_process_exit(tmp_path: Path) -> None:
    script = """
import logging
import sys
from pathlib import Path
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig
configure_server_logging(ServerLoggingConfig(file=True, directory=Path(sys.argv[1]), format="json"))
logging.getLogger("powercontext.file").info("last record")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], check=True, capture_output=True, text=True, timeout=60
    )
    assert result.stderr == ""
    assert result.stdout == ""
    assert json.loads((tmp_path / "server.log").read_text(encoding="utf-8"))["message"] == "last record"


@pytest.mark.skipif(not Path("/dev/full").exists(), reason="Requires the OS disk-full device")
def test_full_disk_does_not_dump_records_or_interrupt_processing(tmp_path: Path) -> None:
    (tmp_path / "server.log").symlink_to("/dev/full")
    script = """
import logging
import sys
from pathlib import Path
from powercontext.server.logging import configure_server_logging, shutdown_server_logging
from powercontext.server.settings import ServerLoggingConfig

configure_server_logging(ServerLoggingConfig(
    file=True, directory=Path(sys.argv[1]), format="json",
))
for index in range(10):
    logging.getLogger("powercontext.file").info("private-message-sentinel %s", index)
shutdown_server_logging()
print("processing continues")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.stdout == "processing continues\n"
    assert set(result.stderr.splitlines()) == {'{"event":"observability.log_failed","outcome":"failure"}'}


def test_file_logging_rejects_a_second_writer_until_the_owner_closes(tmp_path: Path) -> None:
    script = """
import logging
import subprocess
import sys
from pathlib import Path
from powercontext.server.logging import configure_server_logging, shutdown_server_logging
from powercontext.server.settings import ServerLoggingConfig

child = '''
import logging
import sys
from pathlib import Path
from powercontext.server.logging import configure_server_logging, shutdown_server_logging
from powercontext.server.settings import ServerLoggingConfig
try:
    configure_server_logging(ServerLoggingConfig(file=True, directory=Path(sys.argv[1]), format="json"))
except ValueError:
    sys.exit(7)
logging.getLogger("powercontext.file").info("second writer")
shutdown_server_logging()
'''
configure_server_logging(ServerLoggingConfig(file=True, directory=Path(sys.argv[1]), format="json"))
logging.getLogger("powercontext.file").info("first writer")
result = subprocess.run([sys.executable, "-c", child, sys.argv[1]], capture_output=True, text=True, timeout=30)
assert result.returncode == 7
assert result.stdout == result.stderr == ""
shutdown_server_logging()
subprocess.run([sys.executable, "-c", child, sys.argv[1]], check=True, timeout=30)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], check=True, capture_output=True, text=True, timeout=90
    )
    assert result.stdout == result.stderr == ""
    records = [json.loads(line) for line in (tmp_path / "server.log").read_text(encoding="utf-8").splitlines()]
    assert [record["message"] for record in records] == ["first writer", "second writer"]


def test_file_logging_does_not_start_auxiliary_processes(tmp_path) -> None:
    script = """
import logging
import multiprocessing
import sys
from pathlib import Path
from powercontext.server.logging import configure_server_logging, shutdown_server_logging
from powercontext.server.settings import ServerLoggingConfig

config = ServerLoggingConfig(file=True, directory=Path(sys.argv[1]), format="json")
configure_server_logging(config)
assert multiprocessing.active_children() == []
logging.getLogger("powercontext.file").info("first record")
shutdown_server_logging()
configure_server_logging(config)
logging.getLogger("powercontext.file").info("reopened record")
shutdown_server_logging()
assert multiprocessing.active_children() == []
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path)], check=True, capture_output=True, text=True, timeout=60
    )
    assert result.stdout == "" and result.stderr == ""
    records = [json.loads(line) for line in (tmp_path / "server.log").read_text(encoding="utf-8").splitlines()]
    assert [record["message"] for record in records] == ["first record", "reopened record"]


def test_logging_file_settings_use_environment_and_user_data_directory(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path))
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_FILE", "true")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_DIRECTORY", str(tmp_path / "custom-logs"))
    assert ServerSettings().logging.directory == tmp_path / "custom-logs"
    monkeypatch.delenv("POWERCONTEXT_SERVER_LOGGING_DIRECTORY")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_MAX_BYTES", "4096")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_BACKUP_COUNT", "3")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_COMPRESSION", "none")
    config = ServerSettings().logging
    assert config.file
    assert config.directory == tmp_path / "logs"
    assert config.max_bytes == 4096 and config.backup_count == 3 and config.compression == "none"


@pytest.mark.parametrize("values", [{"max_bytes": 0}, {"backup_count": 0}, {"compression": "zip"}])
def test_logging_rejects_unbounded_rotation_and_unsupported_compression(values) -> None:
    with pytest.raises(ValueError):
        ServerLoggingConfig(**values)


def test_operational_filter_uses_bound_request_id_without_overwriting_an_explicit_id() -> None:
    from powercontext.server.context import bind_request_id, reset_request_id

    token = bind_request_id("ingress-request")
    try:
        record = logging.makeLogRecord({"msg": "nested runtime event"})
        explicit = logging.makeLogRecord({"msg": "explicit event", "request_id": "explicit-request"})
        OperationalContextFilter().filter(record)
        OperationalContextFilter().filter(explicit)
        assert json.loads(JsonFormatter().format(record))["request_id"] == "ingress-request"
        assert json.loads(JsonFormatter().format(explicit))["request_id"] == "explicit-request"
    finally:
        reset_request_id(token)
    background = logging.makeLogRecord({"msg": "scheduled work"})
    OperationalContextFilter().filter(background)
    assert "request_id" not in json.loads(JsonFormatter().format(background))


@pytest.mark.parametrize(
    ("code", "category"),
    [
        ("model_timeout", "timeout"),
        ("model_configuration_error", "configuration"),
        ("model_unavailable", "dependency"),
        # Docs: inference failures retain their timeout, configuration, or dependency classification.
        ("invalid_model_output", "dependency"),
        ("invalid_topic_placeholder", "dependency"),
        ("invalid_topic_coordination_content", "dependency"),
    ],
)
def test_inference_failure_codes_keep_their_documented_category(code, category) -> None:
    from powercontext._logging import error_category

    assert error_category(code, outcome="failure") == category


@pytest.mark.parametrize(
    ("code", "status", "category"),
    [
        ("code_timeout", 503, "timeout"),
        ("code_disabled", 503, "configuration"),
        ("code_repository_unconfigured", 503, "configuration"),
        ("code_build_failed", 503, "internal"),
        ("index_integrity_failed", 503, "internal"),
        ("baseline_unavailable", 409, "conflict"),
        ("workspace_busy", 409, "conflict"),
        ("budget_too_small", 422, "invalid_request"),
    ],
)
def test_code_stage_failure_preserves_log_and_span_classification(caplog, code, status, category) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from powercontext.builtin.code.errors import CodeError
    from powercontext.builtin.code.telemetry import stage, tracing_context
    from powercontext.server.tracing import ServerTracing

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider)
    error = CodeError(code, status=status)
    try:
        with (
            caplog.at_level(logging.DEBUG, logger="powercontext.builtin.code.telemetry"),
            tracing_context(tracing),
            pytest.raises(CodeError) as raised,
            stage("query"),
        ):
            raise error
        assert raised.value is error
        record = next(record for record in caplog.records if getattr(record, "event", None) == "code.query")
        OperationalContextFilter().filter(record)
        payload = json.loads(JsonFormatter().format(record))
        assert payload["error_category"] == category
        assert payload["error_code"] == code
        assert payload["status_code"] == status
        span = exporter.get_finished_spans()[0]
        assert (span.attributes or {})["powercontext.error.category"] == category
        assert (span.attributes or {})["powercontext.operation.outcome"] == "failure"
    finally:
        tracing.shutdown()


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_code_stage_logging_preserves_duration_and_resource_fields(log_format) -> None:
    script = f"""
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig
from powercontext.builtin.code.telemetry import stage
configure_server_logging(ServerLoggingConfig(format={log_format!r}, level="DEBUG"))
with stage("query") as fields:
    fields["selected_count"] = 3
    fields["operation"] = "symbols"
    fields["source_body"] = "private-source-sentinel"
"""
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=120)
    lines = result.stdout.splitlines()
    assert len(lines) == 1
    assert "private-source-sentinel" not in lines[0]
    if log_format == "json":
        payload = json.loads(lines[0])
        assert payload["seconds"] >= 0
        assert payload["duration_ms"] == pytest.approx(payload["seconds"] * 1000)
        assert payload["selected_count"] == 3
        assert payload["operation"] == "code.query" and payload["code_operation"] == "symbols"
        if sys.platform != "win32":
            assert payload["process_lifetime_max_rss_bytes"] > 0
            assert payload["children_lifetime_max_rss_bytes"] >= 0
    else:
        assert "duration_ms=" in lines[0] and "selected_count=3" in lines[0]
        if sys.platform != "win32":
            assert "process_lifetime_max_rss_bytes=" in lines[0]


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_configured_logging_preserves_application_failure_diagnostics(log_format) -> None:
    script = f"""
import logging
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig
configure_server_logging(ServerLoggingConfig(format={log_format!r}))
logging.getLogger("powercontext.server.app").warning(
    "PowerContext application operation failed",
    extra={{
        "event": "application.operation.completed", "operation": "search_memory",
        "unit": "application", "outcome": "failure", "status_code": 404,
        "error_code": "scope_not_found", "duration_ms": 2.5,
        "source_body": "private-source-sentinel",
    }},
)
"""
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=120)
    assert "private-source-sentinel" not in result.stdout + result.stderr
    assert len(result.stdout.splitlines()) == 1
    expected = {
        "event": "application.operation.completed",
        "operation": "search_memory",
        "unit": "application",
        "outcome": "failure",
        "status_code": 404,
        "error_code": "scope_not_found",
        "error_category": "not_found",
        "duration_ms": 2.5,
    }
    if log_format == "json":
        payload = json.loads(result.stdout)
        assert expected.items() <= payload.items()
    else:
        for field, value in expected.items():
            assert f" {field}={json.dumps(value)}" in result.stdout


def _run_configured_logging_probe(log_format: str, *, processing: bool = False, range_known: bool = True) -> list[str]:
    script = f"""
import logging

from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig

configure_server_logging(ServerLoggingConfig(format={log_format!r}))
logging.getLogger("uvicorn.error").info("Started server process")
logging.getLogger("uvicorn.error").error("Server startup failed")
logging.getLogger("powercontext.server.factory").info("PowerContext Server is ready")
"""
    if processing:
        script += """
logging.getLogger("powercontext.builtin.runtime.artifact_processing").warning(
    "Artifact processing blocked; operator remediation required",
    extra={
        "event": "artifact_processing.blocked", "stage": "topic_memory",
        "error_code": "window_attempt_limit", "binding": "topic-memory-source-window",
        "family": "topic-memory", "scope": "scope-a\\nforged log",
        "source_after": 0, "source_through": 1, "attempts": 3, "requests": 9, "tokens": 1000,
        "retry_delay_seconds": 300, "source_body": "private source sentinel",
        "traceback": "private traceback sentinel",
    },
)
"""
    if not range_known:
        script = script.replace('"source_through": 1, ', "")
    result = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return result.stdout.splitlines()


def test_configured_console_logging_uses_uvicorn_display_name() -> None:
    lines = _run_configured_logging_probe("console")

    assert len(lines) == 3
    messages = [line.split(" ", 1)[1].split(" service_name=", 1)[0] for line in lines]
    assert messages == [
        "INFO uvicorn Started server process",
        "ERROR uvicorn Server startup failed",
        "INFO powercontext.server.factory PowerContext Server is ready",
    ]
    assert all('service_role="server"' in line and 'service_instance_id="' in line for line in lines)


def test_configured_json_logging_uses_uvicorn_display_name() -> None:
    payloads = [json.loads(line) for line in _run_configured_logging_probe("json")]

    assert [(payload["level"], payload["logger"], payload["message"]) for payload in payloads] == [
        ("INFO", "uvicorn", "Started server process"),
        ("ERROR", "uvicorn", "Server startup failed"),
        ("INFO", "powercontext.server.factory", "PowerContext Server is ready"),
    ]


@pytest.mark.parametrize("log_format", ["console", "json"])
@pytest.mark.parametrize("range_known", [True, False])
def test_configured_logging_preserves_processing_diagnostics_without_private_extras(log_format, range_known) -> None:
    lines = _run_configured_logging_probe(log_format, processing=True, range_known=range_known)
    assert len(lines) == 4
    rendered = lines[-1]
    assert "private source sentinel" not in rendered and "private traceback sentinel" not in rendered
    if log_format == "json":
        payload = json.loads(rendered)
        assert payload["error_code"] == "window_attempt_limit"
        assert payload["stage"] == "topic_memory" and payload["family"] == "topic-memory"
        assert payload["scope"] == "scope-a\nforged log"
        assert (payload["source_after"], payload["attempts"]) == (0, 3)
        if range_known:
            assert payload["source_through"] == 1
        else:
            assert "source_through" not in payload
        assert (payload["requests"], payload["tokens"], payload["retry_delay_seconds"]) == (9, 1000, 300)
    else:
        assert 'error_code="window_attempt_limit"' in rendered
        assert 'stage="topic_memory"' in rendered and 'family="topic-memory"' in rendered
        assert 'scope="scope-a\\nforged log"' in rendered
        assert "source_after=0" in rendered and "attempts=3" in rendered
        assert ("source_through=1" in rendered) is range_known


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
        "mode": "fts",
        "code": "insufficient_coverage",
        "held_count": 1,
        "hold_codes": ("insufficient_coverage",),
        "consumer": "memory_write_gate",
        "policy_id": "memory.write.evidence_sufficiency.v1",
        "policy_version": "1",
        "privacy_boundary": "local_only",
        "privacy_outcome": "local_model_called",
        "coverage": "adjudicated",
        "verdict": "deny",
        "final_action": "memory_write_hold",
        "fallback_reason": "decision_model_fallback",
        "candidate_count": 1,
        "evidence_count": 2,
        "ignored": "not serialized",
    })
    OperationalContextFilter().filter(record)

    payload = json.loads(JsonFormatter().format(record))

    assert payload["message"] == "request completed"
    assert payload["event"] == "transport.request.completed"
    assert payload["operation"] == "search_memory"
    assert payload["request_id"] == "request-123"
    assert payload["transport"] == "http"
    assert payload["mode"] == "fts"
    assert payload["code"] == "insufficient_coverage"
    assert payload["held_count"] == 1
    assert payload["hold_codes"] == ["insufficient_coverage"]
    assert payload["consumer"] == "memory_write_gate"
    assert payload["policy_id"] == "memory.write.evidence_sufficiency.v1"
    assert payload["policy_version"] == "1"
    assert payload["privacy_boundary"] == "local_only"
    assert payload["privacy_outcome"] == "local_model_called"
    assert payload["coverage"] == "adjudicated"
    assert payload["verdict"] == "deny"
    assert payload["final_action"] == "memory_write_hold"
    assert payload["fallback_reason"] == "decision_model_fallback"
    assert (payload["candidate_count"], payload["evidence_count"]) == (1, 2)
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


def test_server_warns_when_using_an_in_memory_database(caplog, tmp_path) -> None:
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(),
            mcp=McpConfig(enabled=False),
        ),
    )

    with caplog.at_level(logging.WARNING, logger="powercontext.server.factory"), TestClient(app) as client:
        response = client.get("/health/live")

    assert response.status_code == 200
    assert any(
        record.levelno == logging.WARNING
        and "all main database data will be lost when the process stops" in record.getMessage()
        for record in caplog.records
    )


def test_server_logging_settings_normalize_the_level(monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_LEVEL", "warning")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_FORMAT", "json")
    monkeypatch.setenv("POWERCONTEXT_SERVER_LOGGING_ACCESS", "false")

    settings = ServerSettings()

    assert settings.logging == ServerLoggingConfig(level="WARNING", format="json", access=False)


def test_logging_format_failure_reports_no_raw_record_or_private_exception() -> None:
    script = """
import logging
from powercontext.server.logging import configure_server_logging
from powercontext.server.settings import ServerLoggingConfig
configure_server_logging(ServerLoggingConfig(format="json"))
logger = logging.getLogger("powercontext.failure")
for _ in range(2):
    logger.info("private-message-sentinel", extra={"operation": object()})
logger.info("private-filter-sentinel", extra={"error_code": object()})
logger.info("processing continues", extra={"event": "probe.completed", "outcome": "success"})
"""
    result = subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True, timeout=120)
    assert "private-message-sentinel" not in result.stdout + result.stderr
    assert "private-filter-sentinel" not in result.stdout + result.stderr
    assert json.loads(result.stdout)["message"] == "processing continues"
    assert result.stderr.splitlines() == ['{"event":"observability.log_failed","outcome":"failure"}']


def test_log_and_trace_resources_share_identity(monkeypatch) -> None:
    from powercontext.server.tracing import ServerTracing

    monkeypatch.setenv("OTEL_SERVICE_NAME", "configured-service")
    filter_ = OperationalContextFilter(role="api")
    record = logging.makeLogRecord({"msg": "identity"})
    filter_.filter(record)
    tracing = ServerTracing.context_only(role="api")
    try:
        payload = json.loads(JsonFormatter().format(record))
        resource = tracing.provider.resource.attributes
        assert payload["service_name"] == resource["service.name"] == "configured-service"
        assert payload["service_version"] == resource["service.version"]
        assert payload["service_instance_id"] == resource["service.instance.id"]
        assert payload["service_role"] == resource["powercontext.service.role"] == "api"
    finally:
        tracing.shutdown()


def test_configured_identity_survives_missing_distribution_metadata(monkeypatch) -> None:
    from importlib.metadata import PackageNotFoundError

    from powercontext.server import telemetry
    from powercontext.server.tracing import ServerTracing

    def missing(name: str) -> str:
        raise PackageNotFoundError(name)

    monkeypatch.delenv("OTEL_SERVICE_NAME", raising=False)
    monkeypatch.setattr(telemetry, "version", missing)
    filter_ = OperationalContextFilter(role="worker")
    record = logging.makeLogRecord({"msg": "identity"})
    filter_.filter(record)
    tracing = ServerTracing.context_only(role="worker")
    try:
        payload = json.loads(JsonFormatter().format(record))
        resource = tracing.provider.resource.attributes
        assert payload["service_name"] == resource["service.name"] == "powercontext-server"
        assert payload["service_instance_id"] == resource["service.instance.id"]
        assert isinstance(payload["service_instance_id"], str) and payload["service_instance_id"]
        assert payload["service_role"] == resource["powercontext.service.role"] == "worker"
        assert "service_version" not in payload
        assert "service.version" not in resource
    finally:
        tracing.shutdown()


def test_application_failure_category_matches_transport_and_trace(caplog, tmp_path) -> None:
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from powercontext.server.tracing import ServerTracing

    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'category.db'}"),
            mcp=McpConfig(enabled=False),
        ),
        tracing=ServerTracing(provider),
    )
    filter_ = OperationalContextFilter()
    caplog.handler.addFilter(filter_)
    try:
        with caplog.at_level(logging.INFO, logger="powercontext"), TestClient(app) as client:
            response = client.post(
                "/v1/memory/search",
                json={"scope_id": "missing-scope", "query": "private-query-sentinel", "mode": "fts"},
            )
        assert response.status_code == 404
        records = [record for record in caplog.records if getattr(record, "operation", None) == "search_memory"]
        assert {record.unit for record in records} == {"application", "transport"}
        assert all(record.error_category == "not_found" for record in records)
        assert all(record.request_id == response.headers["X-PowerContext-Request-ID"] for record in records)
        spans = [
            span
            for span in exporter.get_finished_spans()
            if (span.attributes or {}).get("powercontext.operation.name") == "search_memory"
        ]
        assert len(spans) == 2
        assert all((span.attributes or {})["powercontext.error.category"] == "not_found" for span in spans)
        assert "private-query-sentinel" not in "\n".join(JsonFormatter().format(record) for record in records)
    finally:
        caplog.handler.removeFilter(filter_)
        provider.shutdown()
