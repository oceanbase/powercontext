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

"""Structured output from real spawned workers, including Server-owned composition."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import multiprocessing
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode
from prometheus_client import CollectorRegistry

from powercontext.builtin.code.telemetry import stage
from powercontext.builtin.inference import (
    InferenceConfigurationError,
    InferenceTimeoutError,
    InferenceUnavailableError,
    InvalidInferenceOutputError,
)
from powercontext.builtin.persistence.processing_intents import ArtifactProcessingIntentRepository
from powercontext.builtin.persistence.sqlite import SQLiteConfig, SQLiteProfile
from powercontext.builtin.persistence.supervision import ArtifactProcessingFence
from powercontext.builtin.persistence.tables import SHARED_TABLES
from powercontext.builtin.runtime.artifact_processing import (
    ArtifactProcessingBinding,
    ArtifactProcessingSupervisor,
    SpawnArtifactProcessingWorkerLauncher,
)
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingBlock,
    ArtifactProcessingBlockedError,
    ArtifactProcessingWorkAssignment,
    ArtifactProcessingWorkerCompletion,
    ArtifactProcessingWorkerFailure,
    WorkerInitializerPreparation,
)
from powercontext.server.context import bind_request_id, current_request_id, reset_request_id
from powercontext.server.factory import create_server_app
from powercontext.server.logging import JsonFormatter, OperationalContextFilter, WorkerLogging, worker_logging
from powercontext.server.operational_metrics import OperationalMetricsCollector
from powercontext.server.settings import McpConfig, ServerLoggingConfig, ServerSettings
from powercontext.server.telemetry import service_resource
from powercontext.server.tracing import ServerTracing

_PRIVATE = "private worker source/query/credential sentinel"
_MULTILINE_MESSAGE = "child record\ncontinued café"


def _file_logging_probe(initializer: WorkerLogging) -> None:
    initializer()
    logging.getLogger("powercontext.worker.probe").info(_MULTILINE_MESSAGE, extra={"source_body": lambda: None})


def _waiting_logging_worker(assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion:
    logging.getLogger("powercontext.worker.probe").info("Ready for termination", extra={"event": "probe.ready"})
    time.sleep(60)
    return ArtifactProcessingWorkerCompletion()


def _noisy_logging_worker(assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion:
    for index in range(2000):
        logging.getLogger("powercontext.worker.probe").info(
            "Worker observation %s %s", index, "x" * 2000, extra={"source_body": _PRIVATE}
        )
    return ArtifactProcessingWorkerCompletion()


def _closing_logging_probe(initializer: WorkerLogging, *, truncate: bool) -> None:
    initializer()
    logging.getLogger("powercontext.worker.probe").info("Delivered observation")
    if truncate:
        assert initializer.channel is not None
        # Leave one incomplete envelope before the process closes its socket.
        initializer.channel.sendall(b'"' + _PRIVATE.encode("utf-8"))


@pytest.mark.parametrize("truncate", [False, True])
def test_worker_stream_close_preserves_output_and_counts_discards(tmp_path, capfd, truncate) -> None:
    registry = CollectorRegistry()
    registry.register(OperationalMetricsCollector(role="background"))
    metric = "powercontext_server_telemetry_log_records_dropped_total"
    before = registry.get_sample_value(metric)
    assert before is not None
    config = ServerLoggingConfig(file=True, directory=tmp_path, format="json")
    with worker_logging(config, role="background") as initializer:
        assert isinstance(initializer, WorkerInitializerPreparation)
        with initializer.prepare() as child_initializer:
            assert isinstance(child_initializer, WorkerLogging)
            process = multiprocessing.get_context("spawn").Process(
                target=_closing_logging_probe, args=(child_initializer,), kwargs={"truncate": truncate}
            )
            process.start()
        try:
            process.join(timeout=30)
            assert process.exitcode == 0
        finally:
            if process.is_alive():
                process.kill()
                process.join(timeout=5)
            process.close()
    assert registry.get_sample_value(metric) == before + int(truncate)
    output = capfd.readouterr()
    assert ("observability.log_failed" in output.err) == truncate
    records = (tmp_path / "background.log").read_text(encoding="utf-8")
    assert [record["message"] for record in _json_records(records)] == ["Delivered observation"]
    assert output.out == ""
    assert _PRIVATE not in records + output.err


def test_worker_completion_does_not_wait_for_slow_log_consumer(tmp_path) -> None:
    script = """
import asyncio
import logging
import sys
from pathlib import Path
from tests.e2e.test_worker_logging import _noisy_logging_worker
from powercontext.builtin.persistence.supervision import ArtifactProcessingFence
from powercontext.builtin.runtime.artifact_processing import SpawnArtifactProcessingWorkerLauncher
from powercontext.builtin.runtime.processing_contracts import (
    ArtifactProcessingWorkAssignment, ArtifactProcessingWorkerCompletion,
)
from powercontext.server.logging import configure_server_logging, shutdown_server_logging, worker_logging
from powercontext.server.settings import ServerLoggingConfig

blocked = Path(sys.argv[3])
original_emit = logging.FileHandler.emit
def slow_emit(handler, record):
    if not blocked.exists():
        blocked.write_text("blocked", encoding="utf-8")
        sys.stdin.read(1)
    original_emit(handler, record)
logging.FileHandler.emit = slow_emit

config = ServerLoggingConfig(file=True, directory=Path(sys.argv[1]), format="json")
configure_server_logging(config, role="all")
async def run(initializer):
    assignment = ArtifactProcessingWorkAssignment(
        binding_name="memory-source-window", scope_id="success", artifact_family="memory",
        claimed_request_generation=1, worker_id="noisy-attempt",
        fence=ArtifactProcessingFence(
            supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process",
        ),
    )
    handle = await SpawnArtifactProcessingWorkerLauncher(_noisy_logging_worker, initializer=initializer).start(assignment)
    try:
        assert await asyncio.wait_for(handle.wait(), timeout=30) == ArtifactProcessingWorkerCompletion()
    finally:
        await handle.terminate()
with worker_logging(config, role="all") as initializer:
    asyncio.run(run(initializer))
Path(sys.argv[2]).write_text("completed", encoding="utf-8")
shutdown_server_logging()
"""
    completed = tmp_path / "completed"
    blocked = tmp_path / "blocked"
    log_directory = tmp_path / "logs"
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(log_directory), str(completed), str(blocked)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        deadline = time.monotonic() + 45
        # Hold file writes until the business result is available, then release the sink.
        while not completed.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        finished_without_draining = completed.exists() and blocked.exists()
        stdout, stderr = process.communicate(input="\n", timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
    assert process.returncode == 0, stderr
    assert finished_without_draining
    assert completed.read_text(encoding="utf-8") == "completed"
    output = (log_directory / "all.log").read_text(encoding="utf-8")
    assert "Worker observation" in output
    assert stdout == ""
    assert _PRIVATE not in output + stderr


def test_terminated_worker_does_not_interrupt_shared_log_output(tmp_path, capfd) -> None:
    config = ServerLoggingConfig(file=True, directory=tmp_path, format="json")

    async def run(initializer: Callable[[], None] | WorkerInitializerPreparation) -> None:
        assignment = ArtifactProcessingWorkAssignment(
            binding_name="memory-source-window",
            scope_id="success",
            artifact_family="memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process"
            ),
            worker_id="terminated-attempt",
        )
        handle = await SpawnArtifactProcessingWorkerLauncher(_waiting_logging_worker, initializer=initializer).start(
            assignment
        )
        try:
            async with asyncio.timeout(30):
                while True:
                    output = (tmp_path / "background.log").read_text(encoding="utf-8")
                    if any(record.get("event") == "probe.ready" for record in _json_records(output)):
                        break
                    await asyncio.sleep(0.01)
        finally:
            await handle.terminate()
        surviving = await SpawnArtifactProcessingWorkerLauncher(_logging_worker, initializer=initializer).start(
            ArtifactProcessingWorkAssignment(
                binding_name=assignment.binding_name,
                scope_id="success",
                artifact_family="memory",
                claimed_request_generation=1,
                fence=assignment.fence,
                worker_id="surviving-attempt",
            )
        )
        try:
            assert await asyncio.wait_for(surviving.wait(), timeout=30) == ArtifactProcessingWorkerCompletion()
        finally:
            await surviving.terminate()

    with worker_logging(config, role="background") as initializer:
        asyncio.run(run(initializer))
    output = capfd.readouterr()
    assert output.out == "" and output.err == ""
    records = _json_records((tmp_path / "background.log").read_text(encoding="utf-8"))
    assert any(
        record.get("event") == "probe.ready" and record["worker_id"] == "terminated-attempt" for record in records
    )
    assert any(
        record.get("event") == "artifact_processing.worker.returned" and record["worker_id"] == "surviving-attempt"
        for record in records
    )


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_configured_file_sink_remains_open_after_spawned_worker_cleanup(tmp_path, log_format) -> None:
    script = """
import logging
import multiprocessing
import sys
from pathlib import Path
from tests.e2e.test_worker_logging import _file_logging_probe
from powercontext.server.logging import configure_server_logging, shutdown_server_logging, worker_logging
from powercontext.server.settings import ServerLoggingConfig

config = ServerLoggingConfig(file=True, directory=Path(sys.argv[1]), format=sys.argv[2])
configure_server_logging(config, role="all")
logging.getLogger("powercontext.parent").info("parent before")
with worker_logging(config, role="all") as initializer:
    with initializer.prepare() as child_initializer:
        process = multiprocessing.get_context("spawn").Process(target=_file_logging_probe, args=(child_initializer,))
        process.start()
    try:
        process.join(timeout=30)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=5)
        process.close()
logging.getLogger("powercontext.parent").info("parent after")
shutdown_server_logging()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path), log_format],
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.stdout == "" and result.stderr == ""
    output = (tmp_path / "all.log").read_text(encoding="utf-8")
    assert "source_body" not in output
    if log_format == "console":
        assert _MULTILINE_MESSAGE in output
        assert output.count("parent before") == output.count("parent after") == 1
        assert 'service_role="worker"' in output and 'service_role="all"' in output
        return
    records = _json_records(output)
    assert len(records) == 3
    by_message = {record["message"]: record for record in records}
    assert set(by_message) == {"parent before", _MULTILINE_MESSAGE, "parent after"}
    assert by_message["parent before"]["service_instance_id"] == by_message["parent after"]["service_instance_id"]
    assert by_message["parent before"]["service_instance_id"] != by_message[_MULTILINE_MESSAGE]["service_instance_id"]
    assert by_message["parent before"]["service_role"] == by_message["parent after"]["service_role"] == "all"
    assert by_message[_MULTILINE_MESSAGE]["service_role"] == "worker"
    assert all("source_body" not in record for record in records)


class _WorkerCancelled(asyncio.CancelledError):
    pass


class CancelledError(RuntimeError):
    """An ordinary failure whose diagnostic name resembles asyncio cancellation."""


def _raise_inference_failure(scope_id: str) -> None:
    """Raise the inference failure a probe scope names; other scopes return normally."""

    if scope_id == "failure":
        raise InferenceTimeoutError(_PRIVATE, 1.0)
    if scope_id == "configuration":
        raise InferenceConfigurationError(_PRIVATE)
    if scope_id == "dependency":
        raise InferenceUnavailableError("generation", _PRIVATE)
    if scope_id == "invalid-output":
        raise InvalidInferenceOutputError("memory-extract", _PRIVATE)


def _logging_worker(assignment: ArtifactProcessingWorkAssignment) -> ArtifactProcessingWorkerCompletion:
    logging.getLogger("powercontext.worker.domain").info(
        "Worker domain observation", extra={"event": "artifact_processing.domain", "source_body": _PRIVATE}
    )
    if assignment.scope_id == "code-stage":
        with stage("query") as fields:
            fields["selected_count"] = 1
    _raise_inference_failure(assignment.scope_id)
    if assignment.scope_id == "cancellation":
        raise asyncio.CancelledError(_PRIVATE)
    if assignment.scope_id == "cancellation-subclass":
        raise _WorkerCancelled(_PRIVATE)
    if assignment.scope_id == "cancellation-name-collision":
        raise CancelledError(_PRIVATE)
    if assignment.scope_id == "unexpected":
        raise RuntimeError(_PRIVATE)
    if assignment.scope_id == "blocked":
        raise ArtifactProcessingBlockedError(
            ArtifactProcessingBlock("frontier", "topic_memory", "window_attempt_limit", (("attempts", 3),))
        )
    return ArtifactProcessingWorkerCompletion()


@pytest.mark.parametrize(
    ("scope_id", "category", "outcome"),
    [
        ("failure", "timeout", "failure"),
        ("blocked", "blocked", "blocked"),
        ("configuration", "configuration", "failure"),
        ("dependency", "dependency", "failure"),
        ("invalid-output", "dependency", "failure"),
        ("cancellation", "cancelled", "cancelled"),
        ("cancellation-subclass", "cancelled", "cancelled"),
        ("cancellation-name-collision", "internal", "failure"),
        ("unexpected", "internal", "failure"),
    ],
)
def test_supervisor_traces_preserve_spawned_worker_failure_semantics(
    capfd, tmp_path, scope_id, category, outcome
) -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider(shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracing = ServerTracing(provider)
    intents = ArtifactProcessingIntentRepository()
    binding_name = "memory-source-window"

    async def run() -> None:
        config = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker-trace.db'}")
        async with SQLiteProfile.open(config, tables=SHARED_TABLES) as profile:
            async with profile.database.transaction() as connection:
                await intents.request(connection, scope_id, binding_name)
            binding = ArtifactProcessingBinding(
                binding_name,
                "memory",
                SpawnArtifactProcessingWorkerLauncher(
                    _logging_worker, initializer=WorkerLogging(ServerLoggingConfig(format="json"))
                ),
            )
            async with ArtifactProcessingSupervisor(
                database=profile.database,
                bindings=(binding,),
                lease_mode="single-process",
                tracing=tracing,
            ):
                async with asyncio.timeout(30):
                    while not any(  # noqa: ASYNC110 - bounded polling of exported observations
                        span.name == "artifact_processing.worker" for span in exporter.get_finished_spans()
                    ):
                        await asyncio.sleep(0.01)
            async with profile.database.transaction() as connection:
                intent = await intents.load(connection, scope_id, binding_name)
                assert intent is not None and intent.requested_generation > intent.handled_generation

    try:
        asyncio.run(run())
        spans = [
            span
            for span in exporter.get_finished_spans()
            if span.name in {"artifact_processing.worker", "artifact_processing.worker.wait"}
        ]
        assert {span.name for span in spans} == {"artifact_processing.worker", "artifact_processing.worker.wait"}
        for span in spans:
            assert (span.attributes or {})["powercontext.error.category"] == category
            assert (span.attributes or {})["powercontext.operation.outcome"] == outcome
            assert span.status.status_code == (StatusCode.ERROR if outcome == "failure" else StatusCode.UNSET)
        captured = capfd.readouterr()
        assert _PRIVATE not in captured.out + captured.err + repr([dict(span.attributes or {}) for span in spans])
        terminal = [record for record in _json_records(captured.out) if record.get("duration_ms") is not None]
        assert terminal and terminal[-1]["error_category"] == category
    finally:
        tracing.shutdown()


@pytest.mark.parametrize(
    ("scope_id", "category", "outcome"),
    [
        ("failure", "timeout", "failure"),
        ("cancellation", "cancelled", "cancelled"),
        ("cancellation-subclass", "cancelled", "cancelled"),
        ("cancellation-name-collision", "internal", "failure"),
    ],
)
def test_supervisor_logs_isolate_request_identity_and_preserve_child_cancellation(
    capfd, caplog, tmp_path, monkeypatch, scope_id, category, outcome
) -> None:
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(OperationalContextFilter())
    parent_logger = logging.getLogger("powercontext")
    monkeypatch.setattr(parent_logger, "handlers", [handler])
    monkeypatch.setattr(parent_logger, "propagate", False)

    async def run() -> None:
        async with SQLiteProfile.open(
            SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'supervisor-logs.db'}"), tables=SHARED_TABLES
        ) as profile:
            intents = ArtifactProcessingIntentRepository()
            async with profile.database.transaction() as connection:
                await intents.request(connection, scope_id, "memory-source-window")
            binding = ArtifactProcessingBinding(
                "memory-source-window",
                "memory",
                SpawnArtifactProcessingWorkerLauncher(
                    _logging_worker, initializer=WorkerLogging(ServerLoggingConfig(format="json"))
                ),
            )
            token = bind_request_id("unrelated-parent-request")
            try:
                async with ArtifactProcessingSupervisor(
                    database=profile.database, bindings=(binding,), lease_mode="single-process"
                ):
                    assert current_request_id() == "unrelated-parent-request"
                    parent_logger.info("Caller observation", extra={"event": "caller.observed"})
                    async with asyncio.timeout(30):
                        while not any(  # noqa: ASYNC110 - bounded polling of supervisor output
                            record.get("event") == "artifact_processing.failed"
                            for record in _json_records(output.getvalue())
                        ):
                            await asyncio.sleep(0.01)
            finally:
                reset_request_id(token)

    with caplog.at_level(logging.INFO, logger="powercontext"):
        asyncio.run(run())
    records = _json_records(output.getvalue())
    failure = next(record for record in records if record.get("event") == "artifact_processing.failed")
    child_output = capfd.readouterr()
    child_records = _json_records(child_output.out)
    child = next(record for record in child_records if record.get("event") == "artifact_processing.worker.failed")
    assert failure["worker_id"] == child["worker_id"]
    assert failure["outcome"] == child["outcome"] == outcome
    assert failure["error_category"] == child["error_category"] == category
    assert failure["level"] == ("INFO" if outcome == "cancelled" else "ERROR")
    if outcome == "cancelled":
        assert failure["error_code"] == "worker_cancelled"
    if scope_id == "cancellation-subclass":
        assert failure["exception_type"] == child["exception_type"] == "_WorkerCancelled"
    assert "request_id" not in failure
    caller = next(record for record in records if record.get("event") == "caller.observed")
    assert caller["request_id"] == "unrelated-parent-request"
    assert _PRIVATE not in output.getvalue() + child_output.out + child_output.err


@dataclass(frozen=True)
class _BrokenLogging:
    def __call__(self) -> None:
        WorkerLogging(ServerLoggingConfig(format="json"))()
        raise ValueError(_PRIVATE)


def _json_records(output: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in output.splitlines() if line.startswith('{"timestamp":')]


@pytest.mark.parametrize("outcome", ["success", "failure", "cancellation"])
def test_spawned_worker_logs_execution_identity_and_redacted_outcome(capfd, outcome) -> None:
    assignment = ArtifactProcessingWorkAssignment(
        binding_name="memory-source-window",
        scope_id=outcome,
        artifact_family="memory",
        claimed_request_generation=7,
        fence=ArtifactProcessingFence(
            supervisor_group="global", holder_id="owner", supervisor_generation=3, lease_mode="single-process"
        ),
        worker_id="worker-attempt-1",
    )

    async def run() -> None:
        token = bind_request_id("unrelated-parent-request")
        try:
            handle = await SpawnArtifactProcessingWorkerLauncher(
                _logging_worker, initializer=WorkerLogging(ServerLoggingConfig(format="json"))
            ).start(assignment)
            try:
                if outcome == "success":
                    assert await asyncio.wait_for(handle.wait(), timeout=30) == ArtifactProcessingWorkerCompletion()
                else:
                    with pytest.raises(RuntimeError):
                        await asyncio.wait_for(handle.wait(), timeout=30)
            finally:
                await handle.terminate()
        finally:
            reset_request_id(token)

    asyncio.run(run())
    captured = capfd.readouterr()
    assert _PRIVATE not in captured.out + captured.err
    records = _json_records(captured.out)
    started = next(record for record in records if record["event"] == "artifact_processing.worker.started")
    domain = next(record for record in records if record["event"] == "artifact_processing.domain")
    terminal = records[-1]
    for record in (started, domain, terminal):
        assert record["worker_id"] == assignment.worker_id
        assert record["family"] == "memory" and record["binding"] == assignment.binding_name
        assert record["request_generation"] == 7 and record["supervisor_generation"] == 3
        assert record["operation"] == "process_artifact_scope" and record["unit"] == "background"
        assert record["service_role"] == "worker"
        assert record["service_instance_id"] != service_resource().attributes["service.instance.id"]
        assert "request_id" not in record
    assert terminal["duration_ms"] >= 0
    assert terminal["outcome"] == ("cancelled" if outcome == "cancellation" else outcome)
    if outcome != "success":
        assert terminal["error_category"] == ("cancelled" if outcome == "cancellation" else "timeout")


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_spawned_worker_preserves_blocked_diagnostics_and_control_result(capfd, log_format) -> None:
    async def run() -> None:
        assignment = ArtifactProcessingWorkAssignment(
            binding_name="topic-memory-source-window",
            scope_id="blocked",
            artifact_family="topic-memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process"
            ),
            worker_id="blocked-attempt",
        )
        handle = await SpawnArtifactProcessingWorkerLauncher(
            _logging_worker, initializer=WorkerLogging(ServerLoggingConfig(format=log_format))
        ).start(assignment)
        try:
            with pytest.raises(RuntimeError) as raised:
                await asyncio.wait_for(handle.wait(), timeout=30)
            failure = getattr(raised.value, "failure", None)
            assert isinstance(failure, ArtifactProcessingWorkerFailure)
            assert failure.stage == "topic_memory" and failure.error_code == "window_attempt_limit"
            assert failure.block == ArtifactProcessingBlock(
                "frontier", "topic_memory", "window_attempt_limit", (("attempts", 3),)
            )
        finally:
            await handle.terminate()

    asyncio.run(run())
    captured = capfd.readouterr()
    assert _PRIVATE not in captured.out + captured.err
    assert "artifact_processing.worker.failed" not in captured.out
    if log_format == "json":
        terminal = _json_records(captured.out)[-1]
        assert terminal["event"] == "artifact_processing.worker.blocked"
        assert terminal["level"] == "WARNING" and terminal["outcome"] == "blocked"
        assert terminal["error_category"] == "blocked" and terminal["error_code"] == "window_attempt_limit"
        assert terminal["stage"] == "topic_memory" and terminal["worker_id"] == "blocked-attempt"
    else:
        terminal_line = captured.out.splitlines()[-1]
        assert " WARNING " in terminal_line
        for field in (
            'event="artifact_processing.worker.blocked"',
            'outcome="blocked"',
            'error_category="blocked"',
            'error_code="window_attempt_limit"',
            'stage="topic_memory"',
            'worker_id="blocked-attempt"',
        ):
            assert field in terminal_line


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_spawned_code_stage_preserves_worker_correlation(capfd, log_format) -> None:
    assignment = ArtifactProcessingWorkAssignment(
        binding_name="code-index",
        scope_id="code-stage",
        artifact_family="code",
        claimed_request_generation=7,
        fence=ArtifactProcessingFence(
            supervisor_group="global", holder_id="owner", supervisor_generation=3, lease_mode="single-process"
        ),
        worker_id="code-attempt",
    )

    async def run() -> None:
        handle = await SpawnArtifactProcessingWorkerLauncher(
            _logging_worker, initializer=WorkerLogging(ServerLoggingConfig(format=log_format, level="DEBUG"))
        ).start(assignment)
        try:
            assert await asyncio.wait_for(handle.wait(), timeout=30) == ArtifactProcessingWorkerCompletion()
        finally:
            await handle.terminate()

    asyncio.run(run())
    captured = capfd.readouterr()
    assert _PRIVATE not in captured.out + captured.err
    expected = {
        "worker_id": "code-attempt",
        "family": "code",
        "binding": "code-index",
        "scope": "code-stage",
        "request_generation": 7,
        "supervisor_generation": 3,
        "operation": "code.query",
        "unit": "stage",
        "selected_count": 1,
    }
    if log_format == "json":
        record = next(record for record in _json_records(captured.out) if record.get("event") == "code.query")
        assert expected.items() <= record.items()
        assert "request_id" not in record
    else:
        line = next(line for line in captured.out.splitlines() if 'event="code.query"' in line)
        for field, value in expected.items():
            assert f" {field}={json.dumps(value)}" in line
        assert "request_id=" not in line


def test_worker_logging_bootstrap_failure_does_not_change_completion(capfd) -> None:
    async def run() -> None:
        assignment = ArtifactProcessingWorkAssignment(
            binding_name="memory-source-window",
            scope_id="success",
            artifact_family="memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process"
            ),
            worker_id="bootstrap-attempt",
        )
        handle = await SpawnArtifactProcessingWorkerLauncher(_logging_worker, initializer=_BrokenLogging()).start(
            assignment
        )
        try:
            assert await asyncio.wait_for(handle.wait(), timeout=30) == ArtifactProcessingWorkerCompletion()
        finally:
            await handle.terminate()

    asyncio.run(run())
    output = capfd.readouterr()
    assert _PRIVATE not in output.out + output.err
    records = _json_records(output.out)
    assert any(record["event"] == "observability.worker_logging_failed" for record in records)
    assert records[-1]["event"] == "artifact_processing.worker.returned"


def test_unconfigured_sdk_worker_does_not_enable_global_logging(capfd) -> None:
    async def run() -> None:
        assignment = ArtifactProcessingWorkAssignment(
            binding_name="memory-source-window",
            scope_id="success",
            artifact_family="memory",
            claimed_request_generation=1,
            fence=ArtifactProcessingFence(
                supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process"
            ),
            worker_id="sdk-attempt",
        )
        handle = await SpawnArtifactProcessingWorkerLauncher(_logging_worker).start(assignment)
        try:
            assert await asyncio.wait_for(handle.wait(), timeout=30) == ArtifactProcessingWorkerCompletion()
        finally:
            await handle.terminate()

    asyncio.run(run())
    output = capfd.readouterr()
    assert output.out == "" and output.err == ""


@pytest.mark.parametrize("file_logging", [False, True])
def test_server_composition_initializes_logging_in_the_real_family_worker(
    capfd, tmp_path, monkeypatch, file_logging
) -> None:
    completed = threading.Event()

    def observe_completion(record: logging.LogRecord) -> bool:
        if getattr(record, "event", None) in {"artifact_processing.worker.acknowledged", "artifact_processing.failed"}:
            completed.set()
        return True

    parent_logger = logging.getLogger("powercontext")
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(OperationalContextFilter(role="all"))
    handler.addFilter(observe_completion)
    monkeypatch.setattr(parent_logger, "handlers", [handler])
    monkeypatch.setattr(parent_logger, "level", logging.INFO)
    monkeypatch.setattr(parent_logger, "propagate", False)
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'worker-logging.db'}"),
            inference=InferenceConfig(generation_model="test"),
            runtime=RuntimeConfig(artifact_processing_families=("memory",), memory_schedule_seconds=0.05),
            logging=ServerLoggingConfig(format="json", file=file_logging, directory=tmp_path / "logs"),
            mcp=McpConfig(enabled=False),
        )
    )
    with TestClient(app) as client:
        scope_id = client.get("/v1/scopes/default").json()["scope_id"]
        response = client.post(
            "/v1/sources/content", json={"scope_id": scope_id, "source_id": "logging-source", "content": _PRIVATE}
        )
        assert response.status_code == 202
        # Resetting fd capture while the spawned child writes can discard records
        # between the capture's read and truncate. Consume only after worker cleanup.
        assert completed.wait(timeout=30), "The real family worker must report completion"
    records = _json_records(capfd.readouterr().out)
    worker_records = (
        _json_records((tmp_path / "logs" / "all.log").read_text(encoding="utf-8")) if file_logging else records
    )
    child = [record for record in worker_records if record.get("event") == "artifact_processing.worker.started"]
    assert child, "Server must configure logging in its actual built-in child, not a substituted entrypoint"
    assert child[0]["service_role"] == "worker" and child[0]["family"] == "memory"
    assert "request_id" not in child[0]
    controller = [
        record
        for record in records
        if record.get("event") in {"artifact_processing.worker.acknowledged", "artifact_processing.failed"}
    ]
    assert controller
    assert controller[0]["worker_id"] == child[0]["worker_id"]
    assert controller[0]["service_role"] == "all"
    assert controller[0]["service_instance_id"] != child[0]["service_instance_id"]
    assert _PRIVATE not in json.dumps([*records, *worker_records])


@pytest.mark.parametrize("log_format", ["json", "console"])
def test_concurrent_spawned_workers_share_file_output(tmp_path, capfd, log_format) -> None:
    config = ServerLoggingConfig(file=True, directory=tmp_path, format=log_format)

    async def run(initializer: Callable[[], None] | WorkerInitializerPreparation) -> None:
        launcher = SpawnArtifactProcessingWorkerLauncher(_logging_worker, initializer=initializer)
        assignments = [
            ArtifactProcessingWorkAssignment(
                binding_name="memory-source-window",
                scope_id="success",
                artifact_family="memory",
                claimed_request_generation=1,
                fence=ArtifactProcessingFence(
                    supervisor_group="global", holder_id="owner", supervisor_generation=1, lease_mode="single-process"
                ),
                worker_id=f"file-attempt-{index}",
            )
            for index in range(3)
        ]
        handles = await asyncio.gather(*(launcher.start(assignment) for assignment in assignments))
        try:
            results = await asyncio.wait_for(asyncio.gather(*(handle.wait() for handle in handles)), timeout=30)
            assert results == [ArtifactProcessingWorkerCompletion()] * 3
        finally:
            await asyncio.gather(*(handle.terminate() for handle in handles))

    with worker_logging(config, role="background") as initializer:
        asyncio.run(run(initializer))
    output = (tmp_path / "background.log").read_text(encoding="utf-8")
    assert _PRIVATE not in output
    captured = capfd.readouterr()
    assert captured.out == "" and captured.err == ""
    if log_format == "json":
        records = _json_records(output)
        assert len(records) == 9
        assert len({record["service_instance_id"] for record in records}) == 3
        for index in range(3):
            attempt = [record for record in records if record["worker_id"] == f"file-attempt-{index}"]
            assert {record["event"] for record in attempt} == {
                "artifact_processing.worker.started",
                "artifact_processing.domain",
                "artifact_processing.worker.returned",
            }
        assert all(record["service_role"] == "worker" and "request_id" not in record for record in records)
    else:
        assert len(output.splitlines()) == 9
        for index in range(3):
            assert output.count(f'worker_id="file-attempt-{index}"') == 3
