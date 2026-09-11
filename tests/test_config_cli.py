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
import shlex
from pathlib import Path
from unittest.mock import patch

import pytest
from typer.testing import CliRunner

import powercontext.cli.config as config_cli
from powercontext.server.configuration import server_settings_context


def test_init_creates_a_model_free_deployment_and_explains_capability_limits(tmp_path: Path) -> None:
    environment = tmp_path / ".env"

    result = CliRunner().invoke(
        config_cli.app,
        ["init", "--template", "--output", str(environment)],
        input="\n",
    )

    assert result.exit_code == 0
    assert "PowerContext configuration" in result.output
    assert "Generation API protocol" not in result.output
    assert "Generation API key" not in result.output
    assert "Embedding model" not in result.output
    assert "Inference capability notice" in result.output
    assert "可能影响部分制品功能" in result.output
    assert "https://powercontext.oceanbase.io/en/docs/reference/configuration/" in result.output
    assert f"Path          {environment.resolve()} (mode 0600)" in result.output
    assert "POWERCONTEXT_SERVER_AUTH_TOKEN" in result.output
    assert "the value is never printed" in result.output
    assert "Configuration" in result.output
    assert "Supported Coding Agents (choose one)" in result.output
    for host, (name, setup, launch) in config_cli.AGENTS.items():
        assert name in result.output
        if host != "dsh":
            assert setup in result.output
        assert launch in result.output
    values = config_cli.parse_environment(environment.read_text(encoding="utf-8"))
    assert "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL" not in values
    assert "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL" not in values
    assert "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS" not in values
    validated = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])
    shown = CliRunner().invoke(config_cli.app, ["show", "--env-file", str(environment)])
    assert validated.exit_code == 0
    assert shown.exit_code == 0


@pytest.mark.parametrize("installed", ["0.2.0", "1.0.0rc1", "1.0.0rc2", "0.2.1.dev1+g1234567"])
def test_init_matches_dsh_setup_to_installed_server(installed: str, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(config_cli, "version", lambda _name: installed)
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: _configuration())
    result = CliRunner().invoke(
        config_cli.app, ["init", "--template", "--output", str(tmp_path / "server.env")], input="\n"
    )
    assert result.exit_code == 0
    command = next(line.strip() for line in result.output.splitlines() if "powercontext setup dsh" in line)
    if installed in {"0.2.0", "1.0.0rc1", "1.0.0rc2"}:
        assert command.endswith(f"--ref powercontext-v{installed}")
    else:
        assert "--source /path/to/matching-powercontext-checkout" in command
    assert "--ref master" not in command


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
        ["init", "--template", "--output", str(environment)],
        input="\n",
    )

    assert generated.exit_code == 0
    if os.name != "nt":  # Windows does not implement POSIX owner/group permission bits.
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


def test_validate_accepts_minimal_server_environment_without_inference_models(tmp_path: Path) -> None:
    environment = tmp_path / "server.env"
    environment.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_DATABASE_KIND=seekdb",
            "POWERCONTEXT_SERVER_HTTP_HOST=127.0.0.1",
            "POWERCONTEXT_SERVER_HTTP_PORT=8888",
            "",
        )),
        encoding="utf-8",
    )

    result = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])

    assert result.exit_code == 0
    assert "Configuration is valid" in result.output
    with server_settings_context(env_file=environment) as settings:
        assert settings.database.kind == "seekdb"
        assert settings.http.host == "127.0.0.1"
        assert settings.http.port == 8888


@pytest.mark.parametrize("token", ["literal-${PR1525_ABSENT}", "token'with-apostrophe"])
def test_server_settings_context_preserves_strict_env_file_values(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    token: str,
) -> None:
    environment = tmp_path / "server.env"
    environment.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_ACCESS_MODE=enforced",
            f"POWERCONTEXT_SERVER_AUTH_TOKEN={shlex.quote(token)}",
            "",
        )),
        encoding="utf-8",
    )
    monkeypatch.delenv("POWERCONTEXT_SERVER_ACCESS_MODE", raising=False)
    monkeypatch.delenv("POWERCONTEXT_SERVER_AUTH_TOKEN", raising=False)

    with server_settings_context(env_file=environment, process_environment_overrides=True) as settings:
        assert settings.auth.token is not None
        assert settings.auth.token.get_secret_value() == token


def test_server_settings_context_preserves_process_priority_for_nested_settings(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / "server.env"
    environment.write_text("POWERCONTEXT_SERVER_HTTP_PORT=8999\n", encoding="utf-8")
    monkeypatch.setenv("POWERCONTEXT_SERVER_HTTP", '{"port":8123}')
    monkeypatch.delenv("POWERCONTEXT_SERVER_HTTP_PORT", raising=False)

    with server_settings_context(env_file=environment, process_environment_overrides=True) as settings:
        assert settings.http.port == 8123


@pytest.mark.skipif(os.name == "nt", reason="Windows environment names are case-insensitive")
def test_server_settings_context_preserves_provider_environment_case(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / "server.env"
    environment.write_text("OPENAI_API_KEY=file-secret\n", encoding="utf-8")
    monkeypatch.setenv("openai_api_key", "process-lowercase-secret")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with server_settings_context(env_file=environment, process_environment_overrides=True):
        assert os.environ["OPENAI_API_KEY"] == "file-secret"
        assert os.environ["openai_api_key"] == "process-lowercase-secret"  # noqa: SIM112


def test_server_settings_context_does_not_implicitly_discover_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / ".env").write_text("POWERCONTEXT_SERVER_HTTP_PORT=8889\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("POWERCONTEXT_SERVER_HTTP_PORT", raising=False)

    with server_settings_context() as settings:
        assert settings.http.port == 8000


@pytest.mark.parametrize(
    "runtime_setting",
    (
        "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS=60",
        "POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=60",
        "POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED=true",
    ),
)
def test_validate_rejects_runtime_features_without_required_inference(
    runtime_setting: str,
    tmp_path: Path,
) -> None:
    environment = tmp_path / "server.env"
    environment.write_text(f"{runtime_setting}\n", encoding="utf-8")

    result = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])

    assert result.exit_code == 2
    assert "built-in runtime cannot be configured" in result.output


def test_validate_uses_runtime_provider_factory_for_custom_headers(tmp_path: Path) -> None:
    environment = tmp_path / "server.env"
    environment.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=openai-chat:test-model",
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL=https://provider.example/v1",
            'POWERCONTEXT_SERVER_INFERENCE_GENERATION_HEADERS=\'{"Authorization":"Bearer test"}\'',
            "",
        )),
        encoding="utf-8",
    )

    result = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])

    assert result.exit_code == 0


def test_validate_uses_runtime_provider_factory_for_active_reranking(tmp_path: Path) -> None:
    environment = tmp_path / "server.env"
    environment.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_RUNTIME_MEMORY_RERANK_ENABLED=true",
            "POWERCONTEXT_SERVER_INFERENCE_RERANK_MODEL=openai-chat:rerank-model",
            "POWERCONTEXT_SERVER_INFERENCE_RERANK_BASE_URL=https://provider.example/v1",
            'POWERCONTEXT_SERVER_INFERENCE_RERANK_HEADERS=\'{"Authorization":"Bearer test"}\'',
            "",
        )),
        encoding="utf-8",
    )

    result = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])

    assert result.exit_code == 0


def test_validate_rejects_unsupported_custom_endpoint_provider(tmp_path: Path) -> None:
    environment = tmp_path / "server.env"
    environment.write_text(
        "\n".join((
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=deepseek:deepseek-chat",
            "POWERCONTEXT_SERVER_INFERENCE_GENERATION_BASE_URL=https://provider.example/v1",
            "",
        )),
        encoding="utf-8",
    )

    result = CliRunner().invoke(config_cli.app, ["validate", "--env-file", str(environment)])

    assert result.exit_code == 2
    assert "custom inference endpoints require an OpenAI- or Anthropic-compatible model identifier" in result.output


def test_init_rejects_configuration_that_validation_rejects(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    invalid = _configuration(embedding_dimension=0)
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: invalid)

    result = CliRunner().invoke(config_cli.app, ["init", "--template", "--output", str(environment)])

    assert result.exit_code == 2
    assert "Embedding dimension must be positive" in result.output
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

    result = CliRunner().invoke(config_cli.app, ["init", "--template", "--output", str(environment)])

    assert result.exit_code == 2
    assert "built-in runtime cannot be configured" in result.output
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


def test_collect_configuration_does_not_collect_model_connections(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config_cli,
        "_collect_connection",
        lambda _role: pytest.fail("deployment setup must not collect model connections"),
    )

    configuration = config_cli.collect_configuration()

    assert configuration.generation is None
    assert configuration.embedding is None
    assert configuration.embedding_profile_id is None
    assert configuration.embedding_dimension is None
    assert configuration.schedule_seconds is None


def test_init_refuses_to_replace_an_existing_environment_without_force(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    environment = tmp_path / ".env"
    environment.write_text("EXISTING=value\n", encoding="utf-8")
    monkeypatch.setattr(config_cli, "collect_configuration", lambda **_kwargs: _configuration())

    result = CliRunner().invoke(config_cli.app, ["init", "--template", "--output", str(environment)])

    assert result.exit_code == 2
    assert "already exists" in result.output
    assert environment.read_text(encoding="utf-8") == "EXISTING=value\n"


def test_init_force_defaults_to_preserving_existing_inference_configuration(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    original = config_cli.render_managed_block(_configuration())
    environment.write_text(original, encoding="utf-8")

    result = CliRunner().invoke(
        config_cli.app,
        ["init", "--template", "--output", str(environment), "--force"],
        input="\n",
    )

    assert result.exit_code == 0
    assert "will remove existing model, embedding, inference schedule, or provider credential settings" in result.output
    assert "A mode-0600 backup will be created" in result.output
    assert "Replace them with a model-free configuration? [y/N]" in result.output
    assert "initial-secret" not in result.output
    assert "No changes written." in result.output
    assert environment.read_text(encoding="utf-8") == original
    assert not tuple(tmp_path.glob(".env.bak-*"))


def test_init_force_replaces_inference_configuration_after_explicit_confirmation(tmp_path: Path) -> None:
    environment = tmp_path / ".env"
    original = config_cli.render_managed_block(_configuration())
    environment.write_text(original, encoding="utf-8")

    result = CliRunner().invoke(
        config_cli.app,
        ["init", "--template", "--output", str(environment), "--force"],
        input="y\n",
    )

    assert result.exit_code == 0
    values = config_cli.parse_environment(environment.read_text(encoding="utf-8"))
    assert "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL" not in values
    assert "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL" not in values
    assert "OPENAI_API_KEY" not in values
    backups = tuple(tmp_path.glob(".env.bak-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == original


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
    monkeypatch.setattr(config_cli, "_validate_builtin_runtime", lambda *_args, **_kwargs: None)

    generated = CliRunner().invoke(config_cli.app, ["init", "--template", "--output", str(environment)], input="\n")
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


def test_init_does_not_prompt_for_or_record_provider_credentials(tmp_path: Path) -> None:
    environment = tmp_path / ".env"

    result = CliRunner().invoke(config_cli.app, ["init", "--template", "--output", str(environment)], input="\n")

    assert result.exit_code == 0
    text = environment.read_text(encoding="utf-8")
    assert "# credentials=" not in text
    assert "OPENAI_API_KEY" not in text
    assert "ANTHROPIC_API_KEY" not in text


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
    embedding_dimension: int = 1536,
    database_kind: str = "sqlite",
    database_url: str | None = None,
    database_path: str | None = None,
) -> config_cli.GeneratedConfiguration:
    shared = (config_cli.ProviderVariable("OPENAI_API_KEY", "initial-secret"),)
    return config_cli.GeneratedConfiguration(
        config_version=1,
        generation=generation or config_cli.ModelSelection("openai:gpt-4.1-mini", shared),
        embedding=embedding or config_cli.ModelSelection("openai:text-embedding-3-small", shared),
        embedding_profile_id="openai-text-embedding-3-small-1536-unit-v1",
        embedding_dimension=embedding_dimension,
        database_kind=database_kind,
        database_url=database_url,
        database_path=database_path,
        schedule_seconds=60,
        credentials=credentials,
    )
