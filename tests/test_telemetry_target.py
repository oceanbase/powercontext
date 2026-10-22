"""Correlate scrape targets with content-free log and trace resource identities."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from io import StringIO

import pytest

from powercontext.server.logging import JsonFormatter, OperationalContextFilter
from powercontext.server.telemetry import service_resource


@pytest.mark.parametrize(
    ("role", "expected"),
    [("all", "api.example:8000"), ("api", "api.example:8000"), ("background", "[::1]:8001")],
)
def test_target_identity_agrees_between_logs_and_trace_resources(monkeypatch, role, expected):
    monkeypatch.setenv(
        "OTEL_RESOURCE_ATTRIBUTES",
        "powercontext.service.api_target=api.example:8000,powercontext.service.background_target=[::1]:8001",
    )
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(OperationalContextFilter(role=role))
    logger = logging.getLogger("powercontext.test.target")
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        logger.info("Operation completed", extra={"event": "application.completed", "outcome": "success"})
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)
    record = json.loads(stream.getvalue())
    assert record["service_target"] == expected
    assert record["service_owner_role"] == role
    assert service_resource(role).attributes["powercontext.service.target"] == expected


def test_unknown_target_is_not_fabricated(monkeypatch):
    monkeypatch.delenv("OTEL_RESOURCE_ATTRIBUTES", raising=False)
    assert "powercontext.service.target" not in service_resource().attributes


@pytest.mark.parametrize(
    "target", ["https://user:secret@host", "secret\nvalue", "host:8000/path", "host?secret=value", "host`:8000"]
)
@pytest.mark.parametrize(
    ("attribute", "role"), [("target", "server"), ("api_target", "all"), ("background_target", "background")]
)
def test_invalid_target_identity_is_omitted(monkeypatch, target, attribute, role):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "powercontext.service." + attribute + "=" + target)
    resource = service_resource(role)
    assert "powercontext.service.target" not in resource.attributes
    assert target not in resource.attributes.values()


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_spawned_worker_retains_target_and_has_its_own_process_identity(tmp_path, monkeypatch, log_format):
    monkeypatch.setenv("OTEL_RESOURCE_ATTRIBUTES", "powercontext.service.background_target=worker-host:8001")
    script = tmp_path / "target_worker.py"
    script.write_text("""
import logging
import multiprocessing
import sys
from powercontext.server.logging import configure_server_logging, WorkerLogging
from powercontext.server.settings import ServerLoggingConfig
from powercontext.server.telemetry import service_resource

def child(initializer):
    initializer()
    logging.getLogger("powercontext.worker").info("Worker observation", extra={"event":"test.worker"})

if __name__ == "__main__":
    config = ServerLoggingConfig(format=sys.argv[1])
    configure_server_logging(config, role="background")
    logging.getLogger("powercontext.server").info("Parent observation", extra={"event":"test.parent"})
    target = service_resource("background").attributes["powercontext.service.target"]
    process = multiprocessing.get_context("spawn").Process(
        target=child, args=(WorkerLogging(config, target=target, owner_role="background"),)
    )
    process.start()
    process.join(15)
    assert process.exitcode == 0
""")
    result = subprocess.run(
        [sys.executable, str(script), log_format],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=25,
        check=True,
    )
    lines = [line for line in result.stdout.splitlines() if "test.parent" in line or "test.worker" in line]
    assert len(lines) == 2
    if log_format == "json":
        parent, child = (json.loads(line) for line in lines)
        assert parent["service_target"] == child["service_target"] == "worker-host:8001"
        assert parent["service_instance_id"] != child["service_instance_id"]
        assert child["service_role"] == "worker"
        assert parent["service_owner_role"] == child["service_owner_role"] == "background"
    else:
        assert all('service_target="worker-host:8001"' in line for line in lines)
        assert 'service_role="worker"' in lines[1]
        assert 'service_owner_role="background"' in lines[1]
