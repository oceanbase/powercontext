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

from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import powercontext.cli.config as config_cli


def test_init_asks_for_protocol_endpoint_key_and_plain_model_name(tmp_path: Path) -> None:
    environment = tmp_path / ".env"

    result = CliRunner().invoke(
        config_cli.app,
        ["init", "--output", str(environment)],
        input="\n\n\n\nshared-secret\n\n\n\n\n\n",
    )

    assert result.exit_code == 0
    assert "PowerContext configuration" in result.output
    assert "Generation API protocol" in result.output
    assert "Generation API Base URL" in result.output
    assert "Generation API key" in result.output
    assert "Generation model" in result.output
    assert "Generation model identifier" not in result.output
    assert "environment variable name" not in result.output
    assert "Alibaba Cloud" not in result.output
    assert "OpenRouter" not in result.output
    assert "Configuration" in result.output
    assert "Supported Coding Agents (choose one)" in result.output
    for name, setup, launch in config_cli.AGENTS.values():
        assert name in result.output
        assert setup in result.output
        assert launch in result.output
    values = config_cli.parse_environment(environment.read_text(encoding="utf-8"))
    assert values["OPENAI_API_KEY"] == "shared-secret"
    assert values["OPENAI_BASE_URL"] == "https://api.openai.com/v1"
    assert values["POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL"] == "openai-chat:gpt-4.1-mini"


def test_arbitrary_model_providers_and_environment_variables_are_not_rejected() -> None:
    configuration = _configuration(
        generation=config_cli.ModelSelection(
            model="bedrock:anthropic.claude-sonnet",
            environment=(
                config_cli.ProviderVariable("AWS_PROFILE", "development"),
                config_cli.ProviderVariable("AWS_REGION", "us-west-2"),
            ),
        ),
        embedding=config_cli.ModelSelection(
            model="voyage:voyage-3",
            environment=(config_cli.ProviderVariable("VOYAGE_API_KEY", "voyage-secret"),),
        ),
    )

    config_cli.validate_configuration(configuration)
    values = config_cli.render_environment(configuration)

    assert values["POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL"] == "bedrock:anthropic.claude-sonnet"
    assert values["AWS_PROFILE"] == "development"
    assert values["AWS_REGION"] == "us-west-2"
    assert values["VOYAGE_API_KEY"] == "voyage-secret"


def test_provider_base_url_rejects_embedded_credentials() -> None:
    configuration = _configuration(
        generation=config_cli.ModelSelection(
            "openai-chat:gpt-4.1-mini",
            (config_cli.ProviderVariable("OPENAI_BASE_URL", "https://user:password@example.com/v1"),),
        )
    )

    with pytest.raises(config_cli.ConfigError, match="must not contain credentials"):
        config_cli.validate_configuration(configuration)


def test_init_validate_and_show_round_trip_managed_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    initial = _configuration()
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: initial)
    runner = CliRunner()
    generated = runner.invoke(
        config_cli.app,
        ["init", "--output", str(environment)],
        input="\n",
    )

    assert generated.exit_code == 0
    assert environment.stat().st_mode & 0o777 == 0o600
    generated_text = environment.read_text(encoding="utf-8")
    assert config_cli.MANAGED_BEGIN in generated_text
    assert "# generation-environment=OPENAI_API_KEY" in generated_text
    assert "# credentials=OPENAI_API_KEY" in generated_text

    validated = runner.invoke(config_cli.app, ["validate", "--env-file", str(environment)])
    shown = runner.invoke(config_cli.app, ["show", "--env-file", str(environment)])
    assert validated.exit_code == 0
    assert "Configuration is valid" in validated.output
    assert shown.exit_code == 0
    assert "OPENAI_API_KEY=<redacted>" in shown.output
    assert "initial-secret" not in shown.output


def test_init_rejects_configuration_that_server_settings_reject(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    invalid = _configuration(scope_id="p" * 256)
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: invalid)

    result = CliRunner().invoke(config_cli.app, ["init", "--output", str(environment)])

    assert result.exit_code == 2
    assert "at most 255 characters" in result.output
    assert not environment.exists()


def test_init_rejects_provider_models_that_cannot_be_constructed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    invalid = _configuration(
        generation=config_cli.ModelSelection("missing-provider:model", ()),
    )
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: invalid)

    result = CliRunner().invoke(config_cli.app, ["init", "--output", str(environment)])

    assert result.exit_code == 2
    assert "provider models cannot be configured" in result.output
    assert not environment.exists()


@pytest.mark.parametrize(
    ("database_kind", "database_url", "database_path"),
    [
        ("sqlite", "sqlite+aiosqlite:///relative.db", None),
        ("seekdb", None, "relative-seekdb"),
    ],
)
def test_configuration_rejects_relative_persistent_storage(
    database_kind: str,
    database_url: str | None,
    database_path: str | None,
) -> None:
    configuration = _configuration(
        database_kind=database_kind,
        database_url=database_url,
        database_path=database_path,
    )

    with pytest.raises(config_cli.ConfigError, match="absolute"):
        config_cli.validate_configuration(configuration)


def test_standard_provider_requires_a_credential() -> None:
    with (
        patch.object(config_cli, "_select_value", return_value="openai-chat"),
        patch.object(
            config_cli.typer,
            "prompt",
            side_effect=["https://api.openai.com/v1", "", "real-secret", "gpt-4.1-mini"],
        ),
    ):
        selection, credentials = config_cli._collect_connection("generation")

    assert selection.environment[-1] == config_cli.ProviderVariable("OPENAI_API_KEY", "real-secret")
    assert credentials == ("OPENAI_API_KEY",)


def test_custom_connection_is_not_reclassified_from_its_model_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    generation = config_cli.ModelSelection(
        model="openai:custom-generation",
        environment=(config_cli.ProviderVariable("CUSTOM_CREDENTIAL", "secret"),),
    )
    embedding = config_cli.ModelSelection(
        model="voyage:custom-embedding",
        environment=(config_cli.ProviderVariable("VOYAGE_API_KEY", "secret"),),
    )
    connections = iter(((generation, ("CUSTOM_CREDENTIAL",)), (embedding, ("VOYAGE_API_KEY",))))
    monkeypatch.setattr(config_cli, "_collect_connection", lambda _role: next(connections))
    prompts = iter(("project:test", "Test", 3))
    monkeypatch.setattr(config_cli.typer, "prompt", lambda *_args, **_kwargs: next(prompts))

    with patch.object(
        config_cli.typer,
        "confirm",
        side_effect=AssertionError("custom connection cannot be reused"),
    ):
        configuration = config_cli.collect_configuration()

    assert configuration.generation == generation
    assert configuration.embedding == embedding


def test_init_refuses_to_replace_an_existing_environment_without_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    environment.write_text("EXISTING=value\n", encoding="utf-8")
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: _configuration())

    result = CliRunner().invoke(config_cli.app, ["init", "--output", str(environment)])

    assert result.exit_code == 2
    assert "already exists" in result.output
    assert environment.read_text(encoding="utf-8") == "EXISTING=value\n"


def test_environment_parser_rejects_duplicate_assignments() -> None:
    with pytest.raises(config_cli.EnvironmentFileError, match="duplicate environment name"):
        config_cli.parse_environment("VALUE=one\nVALUE=two\n")


def test_validate_reports_invalid_numeric_values_without_a_traceback(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    content = config_cli.update_environment_document("", _configuration()).replace(
        "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60",
        "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=invalid",
    )
    environment.write_text(content, encoding="utf-8")

    result = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])

    assert result.exit_code == 2
    assert "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS must be an integer" in result.output
    assert "Traceback" not in result.output


def test_show_redacts_standard_credential_container_variables(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    environment.write_text(
        "OTEL_EXPORTER_OTLP_HEADERS=authorization=Bearer-demo-secret\nPLAIN=value\n",
        encoding="utf-8",
    )

    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])

    assert shown.exit_code == 0
    assert "OTEL_EXPORTER_OTLP_HEADERS=<redacted>" in shown.output
    assert "demo-secret" not in shown.output
    assert "PLAIN=value" in shown.output


def test_init_records_generated_credential_names_for_show_redaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    configuration = _configuration(
        generation=config_cli.ModelSelection(
            model="bedrock:anthropic.claude-sonnet",
            environment=(
                config_cli.ProviderVariable("AWS_PROFILE", "development"),
                config_cli.ProviderVariable("SERVICE_CREDENTIAL", "custom-secret"),
            ),
        ),
        embedding=config_cli.ModelSelection(
            model="voyage:voyage-3",
            environment=(config_cli.ProviderVariable("VOYAGE_API_KEY", "voyage-secret"),),
        ),
        credentials=("SERVICE_CREDENTIAL",),
    )
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: configuration)
    monkeypatch.setattr(config_cli, "_validate_provider_models", lambda *_args, **_kwargs: None)

    generated = CliRunner().invoke(config_cli.app, ["init", "--output", str(environment)], input="\n")
    text = environment.read_text(encoding="utf-8")
    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])

    assert generated.exit_code == 0
    assert "# credentials=SERVICE_CREDENTIAL" in text
    assert config_cli.configuration_from_document(text).credentials == ("SERVICE_CREDENTIAL",)
    assert shown.exit_code == 0
    assert "SERVICE_CREDENTIAL=<redacted>" in shown.output
    assert "custom-secret" not in shown.output
    assert "AWS_PROFILE=development" in shown.output


def test_managed_marker_text_inside_a_credential_is_not_treated_as_structure(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    marker_credential = (config_cli.ProviderVariable("OPENAI_API_KEY", config_cli.MANAGED_BEGIN),)
    configuration = _configuration(
        generation=config_cli.ModelSelection("openai:gpt-4.1-mini", marker_credential),
        embedding=config_cli.ModelSelection("openai:text-embedding-3-small", marker_credential),
    )
    content = config_cli.render_managed_block(configuration)
    environment.write_text(content, encoding="utf-8")

    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])
    updated = config_cli.update_environment_document(content, configuration)

    assert shown.exit_code == 0
    assert "OPENAI_API_KEY=<redacted>" in shown.output
    assert config_cli.parse_environment(updated)["OPENAI_API_KEY"] == config_cli.MANAGED_BEGIN


def test_show_reports_malformed_managed_markers_without_a_traceback(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    content = config_cli.render_managed_block(_configuration())
    environment.write_text(content + config_cli.MANAGED_BEGIN + "\n", encoding="utf-8")

    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])

    assert shown.exit_code == 2
    assert "mismatched or repeated PowerContext managed markers" in shown.output
    assert "Traceback" not in shown.output


def test_custom_connection_marks_prompted_credential_for_show_redaction(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    answers = iter([
        "openai-chat:model-name",  # generation model identifier
        "-",  # skip generation credential variable
        "-",  # skip generation Base URL variable
        "MYTOKEN",  # additional variable name
        "extra-secret",
        "",  # finish additional variables
        "voyage:voyage-3",  # embedding model identifier
        "-",  # skip embedding credential variable
        "-",  # skip embedding Base URL variable
        "",  # finish embedding additional variables
    ])
    monkeypatch.setattr(config_cli.typer, "prompt", lambda *_args, **_kwargs: next(answers))
    monkeypatch.setattr(config_cli.typer, "confirm", lambda *_args, **_kwargs: True)
    model, variables, credentials = config_cli._collect_custom_connection("generation")

    assert model == "openai-chat:model-name"
    assert credentials == ("MYTOKEN",)
    assert config_cli.ProviderVariable("MYTOKEN", "extra-secret") in variables
    configuration = _configuration(
        generation=config_cli.ModelSelection(model, tuple(variables)),
        embedding=config_cli.ModelSelection("voyage:voyage-3", ()),
        credentials=credentials,
    )
    content = config_cli.update_environment_document("", configuration)
    environment.write_text(content, encoding="utf-8")

    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])

    assert shown.exit_code == 0
    assert "# credentials=MYTOKEN" in content
    assert config_cli.configuration_from_document(content).credentials == ("MYTOKEN",)
    assert "MYTOKEN=<redacted>" in shown.output
    assert "extra-secret" not in shown.output


def test_init_hides_and_redacts_marked_additional_credentials(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    monkeypatch.setattr(config_cli, "_select_value", lambda *_args, **_kwargs: "custom")
    monkeypatch.setattr(config_cli, "_validate_provider_models", lambda *_args, **_kwargs: None)

    result = CliRunner().invoke(
        config_cli.app,
        ["init", "--output", str(environment)],
        input="\n\n\n-\n-\nMYTOKEN\ny\nextra-secret\n\n\n-\n-\n\n1536\ny\n",
    )
    text = environment.read_text(encoding="utf-8")

    assert result.exit_code == 0
    assert "# credentials=MYTOKEN" in text
    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])
    assert shown.exit_code == 0
    assert "MYTOKEN=<redacted>" in shown.output
    assert "extra-secret" not in shown.output


def test_collect_provider_variable_hides_input_for_credential_names() -> None:
    captured: dict[str, object] = {}

    def _capturing_prompt(text: str, **kwargs: object) -> str:  # type: ignore[override]
        captured.update(kwargs)
        return "secret-value"

    with patch("powercontext.cli.config.typer.prompt", _capturing_prompt):
        result = config_cli._collect_provider_variable("generation", "SERVICE_CREDENTIAL", {}, is_credential=True)

    assert result is not None
    assert result.value == "secret-value"
    assert captured.get("hide_input") is True


def test_collect_additional_provider_variables_hides_and_records_marked_credentials() -> None:
    captures: list[dict[str, object]] = []

    def _capturing_prompt(*_args: object, **kwargs: object) -> str:
        captures.append(kwargs)
        return next(answers)

    answers = iter(["MYTOKEN", "my-secret", ""])
    variables: list[config_cli.ProviderVariable] = []

    with (
        patch("powercontext.cli.config.typer.prompt", _capturing_prompt),
        patch("powercontext.cli.config.typer.confirm", return_value=True),
    ):
        credentials = config_cli._collect_additional_provider_variables("generation", {}, variables)

    assert credentials == ("MYTOKEN",)
    assert variables == [config_cli.ProviderVariable("MYTOKEN", "my-secret")]
    assert captures[1].get("hide_input") is True


def test_collect_additional_provider_variables_keeps_unmarked_values_visible() -> None:
    answers = iter(["AWS_REGION", "us-west-2", ""])
    variables: list[config_cli.ProviderVariable] = []

    with (
        patch("powercontext.cli.config.typer.prompt", side_effect=lambda *_args, **_kwargs: next(answers)),
        patch("powercontext.cli.config.typer.confirm", return_value=False),
    ):
        credentials = config_cli._collect_additional_provider_variables("generation", {}, variables)

    assert credentials == ()
    assert variables == [config_cli.ProviderVariable("AWS_REGION", "us-west-2")]


def _configuration(
    *,
    generation: config_cli.ModelSelection | None = None,
    embedding: config_cli.ModelSelection | None = None,
    credentials: tuple[str, ...] = ("OPENAI_API_KEY",),
    scope_id: str = "project:quickstart",
    database_kind: str = "sqlite",
    database_url: str | None = None,
    database_path: str | None = None,
) -> config_cli.GeneratedConfiguration:
    shared = (config_cli.ProviderVariable("OPENAI_API_KEY", "initial-secret"),)
    return config_cli.GeneratedConfiguration(
        config_version=1,
        scope_id=scope_id,
        display_name="Quick Start",
        generation=generation or config_cli.ModelSelection("openai:gpt-4.1-mini", shared),
        embedding=embedding or config_cli.ModelSelection("openai:text-embedding-3-small", shared),
        embedding_profile_id="openai-text-embedding-3-small-1536-unit-v1",
        embedding_dimension=1536,
        database_kind=database_kind,
        database_url=database_url,
        database_path=database_path,
        schedule_seconds=60,
        credentials=credentials,
    )
