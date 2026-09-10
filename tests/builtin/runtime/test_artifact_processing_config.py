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
from powercontext.builtin.runtime.composition import _topic_memory_processing_available
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
    with pytest.raises(ValidationError, match="max_tokens"):
        InferenceConfig(generation_model="test", generation_model_settings={"max_tokens": True})
    with pytest.raises(ValidationError, match="output_budget_exceeded"):
        InferenceConfig(
            generation_model="test",
            generation_model_context_window_tokens=5,
        )
    with pytest.raises(ValidationError, match="input_budget_exceeded"):
        InferenceConfig(
            generation_model="test",
            generation_model_context_window_tokens=1_000,
        )


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
        pytest.param(
            {"artifact_processing_role": "api", "profile_schedule_enabled": True},
            id="api-profile",
        ),
        pytest.param(
            {"artifact_processing_role": "background", "profile_schedule_enabled": True},
            id="background-profile",
        ),
    ],
)
def test_split_roles_accept_migrated_family_schedules(runtime_values: dict[str, object]) -> None:
    runtime = RuntimeConfig.model_validate(runtime_values)
    assert runtime.artifact_processing_role == runtime_values["artifact_processing_role"]


def test_all_role_preserves_schedule_alias_values() -> None:
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


def test_api_role_accepts_topic_flush_when_generation_declares_cross_role_processing() -> None:
    configured = BuiltinConfig(
        database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)),
        runtime=RuntimeConfig(artifact_processing_role="api"),
        inference=InferenceConfig(generation_model="test"),
    )
    unavailable = configured.model_copy(update={"inference": InferenceConfig()})

    assert _topic_memory_processing_available(configured, ()) is True
    assert _topic_memory_processing_available(unavailable, ()) is False


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
    assert "Inference capability notice" in result.output
    assert "可能影响部分制品功能" in result.output
    assert "https://powercontext.oceanbase.io/en/docs/reference/configuration/" in result.output
    run_server.assert_not_called()
    run_background.assert_called_once()
    assert run_background.call_args.args[0].runtime.artifact_processing_role == "background"
    tracing.shutdown.assert_called_once()


def test_server_cli_routes_split_role_with_a_migrated_schedule(monkeypatch) -> None:
    for name in tuple(os.environ):
        if name.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "oceanbase")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", OCEANBASE_URL)
    monkeypatch.setenv("POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS", "30")

    run_background = Mock()
    monkeypatch.setattr("powercontext.server.cli._run_background", run_background)
    result = CliRunner().invoke(create_cli([server_app]), ["server", "run", "--role", "background"])

    assert result.exit_code == 0, result.output
    assert run_background.call_args.args[0].runtime.memory_schedule_seconds == 30


@pytest.mark.parametrize(
    "old,new",
    [
        ("schedule_seconds", "memory_schedule_seconds"),
        ("profile_max_concurrency", "profile_max_workers"),
        ("artifact_processing_max_workers", "topic_memory_max_workers"),
        ("artifact_processing_worker_timeout_seconds", "topic_memory_worker_timeout_seconds"),
    ],
)
def test_processing_aliases_accept_equivalent_values_and_reject_conflicts(old, new):
    assert getattr(RuntimeConfig.model_validate({old: "12", new: 12.0}), new) == 12
    assert getattr(RuntimeConfig.model_validate({new: 12}), old) == 12
    with pytest.raises(ValidationError, match="conflicting artifact processing"):
        RuntimeConfig.model_validate({old: 12, new: 13})


@pytest.mark.parametrize(
    "field", ["memory_max_workers", "topic_memory_max_workers", "experience_max_workers", "profile_max_workers"]
)
@pytest.mark.parametrize("value", [0, -1, 1.5, True])
def test_each_family_requires_positive_integer_quota(field, value):
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({field: value})


def test_api_can_declare_topic_processing_without_inference_credentials():
    config = BuiltinConfig(
        database=OceanBaseConfig(url=SecretStr(OCEANBASE_URL)),
        runtime=RuntimeConfig(artifact_processing_role="api", artifact_processing_families=("topic-memory",)),
    )
    assert _topic_memory_processing_available(config, ())


def test_unknown_processing_mode_and_unregistered_schedule_fields_rejected():
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"artifact_processing_supervisor_mode": "custom"})
    with pytest.raises(ValidationError):
        RuntimeConfig.model_validate({"skill_schedule_seconds": 10})
