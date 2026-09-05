# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import SecretStr, ValidationError
from typer.testing import CliRunner

from powercontext.builtin.persistence.oceanbase import OceanBaseConfig
from powercontext.builtin.persistence.seekdb import SeekDBConfig
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig
from powercontext.cli.app import create_cli
from powercontext.server.cli import app as server_app
from powercontext.server.factory import BackgroundRoleRequiresBackgroundRunnerError, create_server_app
from powercontext.server.settings import ServerSettings

OCEANBASE_URL = "mysql+aoceanbase://root%40tenant:secret@127.0.0.1:2881/powercontext?charset=utf8mb4"


def test_artifact_processing_configuration_defaults_match_the_rfc() -> None:
    runtime = RuntimeConfig()

    assert runtime.topic_memory_schedule_seconds is None
    assert runtime.topic_memory_source_window_limit == 10
    assert runtime.topic_memory_history_max_candidates == 20
    assert runtime.topic_memory_history_rrf_threshold == 70
    assert runtime.topic_memory_history_min_candidates == 5
    assert runtime.artifact_processing_max_workers == 10
    assert runtime.artifact_processing_worker_timeout_seconds == 600
    assert runtime.artifact_processing_role == "all"
    assert InferenceConfig().generation_model_context_window_tokens == 125_000


def test_artifact_processing_configuration_rejects_invalid_bounds() -> None:
    with pytest.raises(ValidationError, match="topic_memory_history_min_candidates"):
        RuntimeConfig(
            topic_memory_history_min_candidates=6,
            topic_memory_history_max_candidates=5,
        )
    with pytest.raises(ValidationError):
        RuntimeConfig(topic_memory_schedule_seconds=0)
    with pytest.raises(ValidationError):
        RuntimeConfig(artifact_processing_worker_timeout_seconds=0)
    with pytest.raises(ValidationError):
        RuntimeConfig(topic_memory_history_max_candidates=21)


@pytest.mark.parametrize(
    "runtime_values",
    [
        pytest.param(
            {"artifact_processing_role": "api", "schedule_seconds": 30},
            id="api-memory",
        ),
        pytest.param(
            {"artifact_processing_role": "api", "experience_schedule_seconds": 30},
            id="api-experience",
        ),
        pytest.param(
            {"artifact_processing_role": "background", "schedule_seconds": 30},
            id="background-memory",
        ),
        pytest.param(
            {"artifact_processing_role": "background", "experience_schedule_seconds": 30},
            id="background-experience",
        ),
    ],
)
def test_split_roles_reject_legacy_scheduler_intervals(runtime_values: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="require artifact_processing_role='all'"):
        RuntimeConfig.model_validate(runtime_values)


def test_all_role_remains_the_legacy_scheduler_owner() -> None:
    runtime = RuntimeConfig(
        artifact_processing_role="all",
        schedule_seconds=30,
        experience_schedule_seconds=45,
    )

    assert runtime.schedule_seconds == 30
    assert runtime.experience_schedule_seconds == 45


@pytest.mark.parametrize(
    "database",
    [
        SQLiteConfig(),
        SeekDBConfig(path=Path("seekdb-data")),
    ],
)
def test_embedded_databases_reject_split_process_roles(database) -> None:
    with pytest.raises(ValidationError, match="must be 'all'"):
        BuiltinConfig(
            database=database,
            runtime=RuntimeConfig(artifact_processing_role="api"),
        )


@pytest.mark.parametrize("role", ["all", "api", "background"])
def test_oceanbase_accepts_every_artifact_processing_role(role) -> None:
    config = BuiltinConfig(
        database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)),
        runtime=RuntimeConfig(artifact_processing_role=role),
    )

    assert config.runtime.artifact_processing_role == role


def test_background_role_cannot_be_mounted_as_an_http_application() -> None:
    settings = ServerSettings(
        database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)),
        runtime=RuntimeConfig(artifact_processing_role="background"),
    )

    with pytest.raises(BackgroundRoleRequiresBackgroundRunnerError):
        create_server_app(settings=settings)


def test_server_cli_routes_background_role_without_starting_http(monkeypatch) -> None:
    run_background = Mock()
    run_server = Mock()
    tracing = Mock()
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", OCEANBASE_URL)
    monkeypatch.setattr("powercontext.server.cli._run_background", run_background)
    monkeypatch.setattr("powercontext.server.cli._run_server", run_server)
    monkeypatch.setattr("powercontext.server.cli.configure_server_logging", lambda _config: None)
    monkeypatch.setattr("powercontext.server.cli.configure_server_tracing", lambda _config: tracing)

    result = CliRunner().invoke(create_cli([server_app]), ["server", "run", "--role", "background"])

    assert result.exit_code == 0, result.output
    run_server.assert_not_called()
    run_background.assert_called_once()
    assert run_background.call_args.args[0].runtime.artifact_processing_role == "background"
    tracing.shutdown.assert_called_once()


def test_server_cli_rejects_split_role_with_a_legacy_scheduler(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", OCEANBASE_URL)
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS", "30")

    result = CliRunner().invoke(create_cli([server_app]), ["server", "run", "--role", "background"])

    assert result.exit_code == 2
    assert "schedule_seconds" in result.output
    assert "artifact_processing_role" in result.output
