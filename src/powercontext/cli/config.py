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

"""Generate, edit, inspect, and validate PowerContext environment files."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Never
from urllib.parse import urlsplit

import typer
from pydantic import ValidationError

from powercontext.cli.env_file import EnvironmentFileError, parse_environment

HELP_OPTION_NAMES = ("-h", "--help")
MANAGED_BEGIN = "# >>> powercontext managed configuration >>>"
MANAGED_END = "# <<< powercontext managed configuration <<<"
CONFIG_VERSION = 1
MODEL_CONFIGURATION_URL = "https://pydantic.dev/docs/ai/models/overview/"


class ConfigError(ValueError):
    """Report an invalid or unsafe Config Generator operation."""


@dataclass(frozen=True, slots=True)
class ProviderVariable:
    """One provider-specific environment assignment."""

    name: str
    value: str


@dataclass(frozen=True, slots=True)
class ModelSelection:
    """One model identifier plus every environment value required by its provider."""

    model: str
    environment: tuple[ProviderVariable, ...]
    protocol_id: str | None = None


@dataclass(frozen=True, slots=True)
class GeneratedConfiguration:
    """Canonical state rendered into one managed environment block."""

    config_version: int
    scope_id: str
    display_name: str
    generation: ModelSelection
    embedding: ModelSelection
    embedding_profile_id: str
    embedding_dimension: int
    database_kind: str
    database_url: str | None
    database_path: str | None
    schedule_seconds: int
    credentials: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ApiProtocol:
    """Stable API contract used to derive provider-specific settings."""

    identifier: str
    label: str
    generation_adapter: str | None
    embedding_adapter: str | None
    credential_name: str
    base_url_name: str
    default_base_url: str
    default_generation_model: str | None
    default_embedding_model: str | None


API_PROTOCOLS = (
    ApiProtocol(
        identifier="openai-chat",
        label="OpenAI-compatible Chat Completions",
        generation_adapter="openai-chat",
        embedding_adapter="openai",
        credential_name="OPENAI_API_KEY",
        base_url_name="OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        default_generation_model="gpt-4.1-mini",
        default_embedding_model="text-embedding-3-small",
    ),
    ApiProtocol(
        identifier="openai-responses",
        label="OpenAI Responses API",
        generation_adapter="openai",
        embedding_adapter="openai",
        credential_name="OPENAI_API_KEY",
        base_url_name="OPENAI_BASE_URL",
        default_base_url="https://api.openai.com/v1",
        default_generation_model="gpt-4.1-mini",
        default_embedding_model="text-embedding-3-small",
    ),
    ApiProtocol(
        identifier="anthropic",
        label="Anthropic-compatible Messages API",
        generation_adapter="anthropic",
        embedding_adapter=None,
        credential_name="ANTHROPIC_API_KEY",
        base_url_name="ANTHROPIC_BASE_URL",
        default_base_url="https://api.anthropic.com",
        default_generation_model="claude-sonnet-4-5",
        default_embedding_model=None,
    ),
)
_PROTOCOL_BY_ID = {protocol.identifier: protocol for protocol in API_PROTOCOLS}


AGENTS: dict[str, tuple[str, str, str]] = {
    "codex": ("Codex", "powercontext setup codex --source oceanbase/powercontext --ref master", "codex"),
    "claude-code": (
        "Claude Code",
        "powercontext setup claude-code --source oceanbase/powercontext --ref master",
        "claude",
    ),
    "dsh": ("DeepSeek Harness", "powercontext setup dsh --source oceanbase/powercontext --ref master", "dsh web"),
    "opencode": (
        "OpenCode",
        "powercontext setup opencode --source oceanbase/powercontext --ref master",
        "opencode",
    ),
    "pi": ("Pi", "powercontext setup pi --source oceanbase/powercontext --ref master", "pi"),
}

# Input hints only, not a provider allowlist. Unknown prefixes can attach arbitrary variables.
_MODEL_ENVIRONMENT_HINTS: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "openai-chat": ("OPENAI_BASE_URL", "OPENAI_API_KEY"),
    "anthropic": ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}

_BASE_ENVIRONMENT: dict[str, str] = {
    "POWERCONTEXT_SERVER_HTTP_HOST": "127.0.0.1",
    "POWERCONTEXT_SERVER_HTTP_PORT": "8000",
    "POWERCONTEXT_SERVER_MCP_ENABLED": "true",
    "POWERCONTEXT_SERVER_MCP_PATH": "/mcp",
    "POWERCONTEXT_SERVER_AUTH_ENABLED": "false",
    "POWERCONTEXT_SERVER_DASHBOARD_ENABLED": "true",
    "POWERCONTEXT_SERVER_LOGGING_LEVEL": "INFO",
    "POWERCONTEXT_SERVER_LOGGING_FORMAT": "console",
    "POWERCONTEXT_SERVER_LOGGING_ACCESS": "true",
    "POWERCONTEXT_SERVER_METRICS_ENABLED": "true",
    "POWERCONTEXT_SERVER_TRACING_ENABLED": "false",
    "POWERCONTEXT_SERVER_RUNTIME_SOURCE_WINDOW_LIMIT": "100",
    "POWERCONTEXT_SERVER_RUNTIME_MEMORY_EXTRACTION_PROFILE": "coding",
    "POWERCONTEXT_SERVER_INFERENCE_GENERATION_TIMEOUT_SECONDS": "30",
    "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MAX_REQUESTS": "2",
    "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_NORMALIZATION": "unit",
    "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_BATCH_SIZE": "10",
    "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_TIMEOUT_SECONDS": "30",
    "POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED": "true",
    "POWERCONTEXT_CLIENT_SERVER_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_CLIENT_TIMEOUT": "10",
    "POWERCONTEXT_CLAUDE_SERVER_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_DSH_BASE_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_OPENCODE_BASE_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_PI_BASE_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_LANGGRAPH_BASE_URL": "http://127.0.0.1:8000",
    "POWERCONTEXT_CODEX_CAPTURE_PROMPTS": "true",
    "POWERCONTEXT_CLAUDE_CAPTURE_PROMPTS": "true",
    "POWERCONTEXT_DSH_CAPTURE_PROMPTS": "true",
    "POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS": "true",
    "POWERCONTEXT_PI_CAPTURE_PROMPTS": "true",
}
_SCOPE_NAMES = (
    "POWERCONTEXT_CODEX_SCOPE_ID",
    "POWERCONTEXT_CLAUDE_SCOPE_ID",
    "POWERCONTEXT_DSH_SCOPE_ID",
    "POWERCONTEXT_OPENCODE_SCOPE_ID",
    "POWERCONTEXT_PI_SCOPE_ID",
    "POWERCONTEXT_LANGGRAPH_SCOPE_ID",
)
_KNOWN_PROVIDER_NAMES = {name for names in _MODEL_ENVIRONMENT_HINTS.values() for name in names}
_OPTIONAL_MANAGED_NAMES = {
    "POWERCONTEXT_SERVER_DATABASE_URL",
    "POWERCONTEXT_SERVER_DATABASE_PATH",
}
_ALL_FIXED_MANAGED_NAMES = (
    set(_BASE_ENVIRONMENT)
    | set(_SCOPE_NAMES)
    | _KNOWN_PROVIDER_NAMES
    | _OPTIONAL_MANAGED_NAMES
    | {
        "POWERCONTEXT_SERVER_DASHBOARD_SCOPES",
        "POWERCONTEXT_SERVER_DATABASE_KIND",
        "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS",
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL",
        "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL",
        "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID",
        "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION",
    }
)
_SECRET_NAMES = {
    "POWERCONTEXT_SERVER_DATABASE_URL",
    "POWERCONTEXT_SERVER_AUTH_TOKEN",
    "POWERCONTEXT_CLIENT_API_TOKEN",
}
_CREDENTIAL_CONTAINER_SUFFIXES = ("_HEADERS", "_HEADER", "_COOKIES", "_COOKIE")
_ENVIRONMENT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ASSIGNMENT_NAME = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")

app = typer.Typer(
    name="config",
    context_settings={"help_option_names": HELP_OPTION_NAMES},
    help="Initialize, inspect, and validate PowerContext configuration.",
    no_args_is_help=True,
)


@app.callback()
def main() -> None:
    """Manage environment-file configuration."""


@app.command("init")
def init_command(
    output: Annotated[Path, typer.Option("--output", "-o", help="Environment file to create.")] = Path(".env"),
    force: Annotated[bool, typer.Option(help="Replace managed values in an existing file.")] = False,
    advanced: Annotated[bool, typer.Option(help="Configure storage and scheduling choices.")] = False,
) -> None:
    """Create a working configuration through a short guided setup."""

    if output.exists() and not force:
        _fail(f"{output} already exists; use --force")
    try:
        configuration = collect_configuration(advanced=advanced)
        _validate_operational_configuration(configuration)
        _print_summary(configuration)
        if not typer.confirm(f"Write {output}?", default=True):
            typer.echo("No changes written.")
            return
        existing = output.read_text(encoding="utf-8") if output.exists() else ""
        content = update_environment_document(existing, configuration)
        backup = write_environment(output, content, backup=output.exists())
    except (ConfigError, EnvironmentFileError, OSError, UnicodeError, ValidationError) as error:
        _fail(str(error))
    _report_written(output, backup)
    _print_next_steps(output)


@app.command("show")
def show_command(
    env_file: Annotated[Path, typer.Option(help="Environment file to inspect.")] = Path(".env"),
) -> None:
    """Print effective assignments with credentials redacted."""

    try:
        content = env_file.read_text(encoding="utf-8")
        values = parse_environment(content, source=str(env_file))
        recorded = _managed_metadata(content).get("credentials", "")
    except (ConfigError, EnvironmentFileError, OSError, UnicodeError) as error:
        _fail(str(error))
    recorded_credentials = {name for name in recorded.split(",") if name}
    for name in sorted(values):
        value = "<redacted>" if _is_secret_name(name) or name in recorded_credentials else values[name]
        typer.echo(f"{name}={value}")


@app.command("validate")
def validate_command(
    env_file: Annotated[Path, typer.Option(help="Environment file to validate.")] = Path(".env"),
) -> None:
    """Validate syntax, model configuration, and Server settings."""

    try:
        content = env_file.read_text(encoding="utf-8")
        values = parse_environment(content, source=str(env_file))
        configuration = configuration_from_document(content)
        _validate_operational_configuration(configuration, values=values)
    except (ConfigError, EnvironmentFileError, OSError, UnicodeError, ValidationError) as error:
        _fail(str(error))
    typer.echo(f"Configuration is valid: {env_file.resolve()}")


def _print_summary(configuration: GeneratedConfiguration) -> None:
    generation_url = next(
        (variable.value for variable in configuration.generation.environment if _is_base_url_name(variable.name)),
        "provider default",
    )
    embedding_url = next(
        (variable.value for variable in configuration.embedding.environment if _is_base_url_name(variable.name)),
        "provider default",
    )
    typer.secho("\nConfiguration", bold=True, fg=typer.colors.CYAN)
    typer.echo(f"  Scope       {configuration.scope_id}")
    typer.echo(f"  Generation  {configuration.generation.model.partition(':')[2]} ({generation_url})")
    typer.echo(f"  Embedding   {configuration.embedding.model.partition(':')[2]} ({embedding_url})")
    typer.echo(f"  Database    {configuration.database_kind}")


def collect_configuration(
    *,
    advanced: bool = False,
) -> GeneratedConfiguration:
    """Collect a short task-oriented configuration."""

    typer.secho("\nPowerContext configuration", bold=True, fg=typer.colors.CYAN)
    typer.echo("Press Enter to accept a default. Provider details are derived from the API protocol.\n")
    scope_id = typer.prompt("Scope ID", default="project:quickstart").strip()
    display_name = typer.prompt("Dashboard display name", default="Quick Start").strip()
    generation, generation_credentials = _collect_connection("generation")
    generation_protocol = _PROTOCOL_BY_ID.get(generation.protocol_id or "")
    can_reuse = generation_protocol is not None and generation_protocol.embedding_adapter is not None
    reuse = can_reuse and typer.confirm("Use this API connection for Embedding?", default=True)
    if reuse and generation_protocol is not None:
        embedding_model = typer.prompt(
            "Embedding model", default=generation_protocol.default_embedding_model or "text-embedding-3-small"
        ).strip()
        embedding = ModelSelection(
            model=f"{generation_protocol.embedding_adapter}:{embedding_model}",
            environment=generation.environment,
            protocol_id=generation_protocol.identifier,
        )
        embedding_credentials = generation_credentials
    else:
        embedding, embedding_credentials = _collect_connection("embedding")
    dimension = typer.prompt("Embedding dimension", default=1536, type=int)
    profile = _profile_id(embedding.model, dimension)
    if advanced:
        database_kind, database_url, database_path = _collect_database()
        schedule = typer.prompt("Source processing interval in seconds", default=60, type=int)
    else:
        database_kind, database_url, database_path = "sqlite", None, None
        schedule = 60
    return GeneratedConfiguration(
        config_version=CONFIG_VERSION,
        scope_id=scope_id,
        display_name=display_name,
        generation=generation,
        embedding=embedding,
        embedding_profile_id=profile,
        embedding_dimension=dimension,
        database_kind=database_kind,
        database_url=database_url,
        database_path=database_path,
        schedule_seconds=schedule,
        credentials=tuple(dict.fromkeys(generation_credentials + embedding_credentials)),
    )


def render_environment(configuration: GeneratedConfiguration) -> dict[str, str]:
    """Render canonical configuration into environment assignments."""

    values = dict(_BASE_ENVIRONMENT)
    values.update({
        "POWERCONTEXT_SERVER_DASHBOARD_SCOPES": json.dumps(
            [{"scope_id": configuration.scope_id, "display_name": configuration.display_name}],
            separators=(",", ":"),
            ensure_ascii=False,
        ),
        "POWERCONTEXT_SERVER_DATABASE_KIND": configuration.database_kind,
        "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS": str(configuration.schedule_seconds),
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL": configuration.generation.model,
        "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL": configuration.embedding.model,
        "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID": configuration.embedding_profile_id,
        "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION": str(configuration.embedding_dimension),
    })
    values.update(dict.fromkeys(_SCOPE_NAMES, configuration.scope_id))
    if configuration.database_url is not None:
        values["POWERCONTEXT_SERVER_DATABASE_URL"] = configuration.database_url
    if configuration.database_path is not None:
        values["POWERCONTEXT_SERVER_DATABASE_PATH"] = configuration.database_path
    _merge_provider_values(values, configuration.generation)
    _merge_provider_values(values, configuration.embedding)
    return values


def render_managed_block(configuration: GeneratedConfiguration) -> str:
    """Render one versioned managed block."""

    metadata = [
        f"# config-version={configuration.config_version}",
        f"# generation-environment={_environment_names(configuration.generation)}",
        f"# embedding-environment={_environment_names(configuration.embedding)}",
    ]
    if configuration.credentials:
        metadata.append(f"# credentials={','.join(configuration.credentials)}")
    assignments = tuple(f"{name}={shlex.quote(value)}" for name, value in render_environment(configuration).items())
    return "\n".join((MANAGED_BEGIN, *metadata, *assignments, MANAGED_END, ""))


def update_environment_document(content: str, configuration: GeneratedConfiguration) -> str:
    """Replace the managed block while preserving unknown assignments and comments."""

    begin_markers = _managed_marker_matches(content, MANAGED_BEGIN)
    end_markers = _managed_marker_matches(content, MANAGED_END)
    if len(begin_markers) != len(end_markers) or len(begin_markers) > 1:
        raise ConfigError(  # noqa: TRY003
            "environment contains mismatched or repeated PowerContext managed markers"
        )
    block = render_managed_block(configuration)
    if begin_markers:
        start = begin_markers[0].start()
        end = end_markers[0].end()
        if end < start:
            raise ConfigError("PowerContext managed markers are out of order")  # noqa: TRY003
        return _join_document_parts(content[:start].rstrip(), block.rstrip(), content[end:].strip("\n"))
    managed_names = _ALL_FIXED_MANAGED_NAMES | {
        variable.name
        for selection in (configuration.generation, configuration.embedding)
        for variable in selection.environment
    }
    retained = []
    for line in content.splitlines():
        match = _ASSIGNMENT_NAME.match(line.strip())
        if match is None or match.group(1) not in managed_names:
            retained.append(line)
    return _join_document_parts("\n".join(retained).rstrip(), block.rstrip())


def configuration_from_document(content: str) -> GeneratedConfiguration:
    """Load configuration state from an environment document for validation."""

    values = parse_environment(content)
    metadata = _managed_metadata(content)
    generation_model = _required(values, "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL")
    embedding_model = _required(values, "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL")
    scope_id, display_name = _scope(values)
    return GeneratedConfiguration(
        config_version=_parse_integer(str(metadata.get("config-version", CONFIG_VERSION)), "config-version"),
        scope_id=scope_id,
        display_name=display_name,
        generation=ModelSelection(
            generation_model, _provider_variables("generation", generation_model, metadata, values)
        ),
        embedding=ModelSelection(embedding_model, _provider_variables("embedding", embedding_model, metadata, values)),
        embedding_profile_id=_required(values, "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID"),
        embedding_dimension=_parse_integer(
            _required(values, "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION"),
            "POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION",
        ),
        database_kind=values.get("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite"),
        database_url=values.get("POWERCONTEXT_SERVER_DATABASE_URL"),
        database_path=values.get("POWERCONTEXT_SERVER_DATABASE_PATH"),
        schedule_seconds=_parse_integer(
            values.get("POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS", "60"),
            "POWERCONTEXT_SERVER_RUNTIME_SCHEDULE_SECONDS",
        ),
        credentials=tuple(name for name in metadata.get("credentials", "").split(",") if name),
    )


def validate_configuration(configuration: GeneratedConfiguration) -> None:
    """Validate model, environment, database, Scope, and runtime contracts."""

    if configuration.config_version != CONFIG_VERSION:
        raise ConfigError(f"unsupported config version: {configuration.config_version}")  # noqa: TRY003
    if not configuration.scope_id.strip() or not configuration.display_name.strip():
        raise ConfigError("Scope ID and Dashboard display name are required")  # noqa: TRY003
    if len(configuration.scope_id) > 255:
        raise ConfigError("Scope ID must contain at most 255 characters")  # noqa: TRY003
    if len(configuration.display_name) > 80:
        raise ConfigError("Dashboard display name must contain at most 80 characters")  # noqa: TRY003
    if configuration.embedding_dimension < 1 or configuration.schedule_seconds < 1:
        raise ConfigError("Embedding dimension and Source interval must be positive")  # noqa: TRY003
    _validate_model_selection("Generation", configuration.generation)
    _validate_model_selection("Embedding", configuration.embedding)
    if configuration.database_kind not in {"sqlite", "oceanbase", "seekdb"}:
        raise ConfigError(f"unsupported database: {configuration.database_kind}")  # noqa: TRY003
    if configuration.database_kind == "oceanbase" and not configuration.database_url:
        raise ConfigError("OceanBase requires POWERCONTEXT_SERVER_DATABASE_URL")  # noqa: TRY003
    _validate_storage_location(configuration)
    render_environment(configuration)


def _validate_storage_location(configuration: GeneratedConfiguration) -> None:
    if (
        configuration.database_kind == "seekdb"
        and configuration.database_path is not None
        and not Path(configuration.database_path).expanduser().is_absolute()
    ):
        raise ConfigError("seekDB path must be absolute")  # noqa: TRY003
    if configuration.database_kind != "sqlite" or configuration.database_url is None:
        return
    from sqlalchemy.engine import make_url
    from sqlalchemy.exc import ArgumentError

    try:
        database = make_url(configuration.database_url).database
    except (ArgumentError, ValueError) as error:
        raise ConfigError("SQLite URL is invalid") from error  # noqa: TRY003
    if database != ":memory:" and (database is None or not Path(database).expanduser().is_absolute()):
        raise ConfigError("SQLite URL must use an absolute database path")  # noqa: TRY003


def _validate_operational_configuration(
    configuration: GeneratedConfiguration,
    *,
    values: Mapping[str, str] | None = None,
) -> None:
    validate_configuration(configuration)
    rendered = render_environment(configuration) if values is None else dict(values)
    _validate_server_settings(rendered)
    _validate_provider_models(configuration, rendered)


def _validate_provider_models(configuration: GeneratedConfiguration, values: Mapping[str, str]) -> None:
    with _temporary_environment(values, clear=set()):
        try:
            asyncio.run(_construct_provider_models(configuration))
        except Exception as error:
            raise ConfigError(f"provider models cannot be configured: {error}") from error  # noqa: TRY003


async def _construct_provider_models(configuration: GeneratedConfiguration) -> None:
    from pydantic_ai.embeddings import infer_embedding_model
    from pydantic_ai.models import infer_model

    async with infer_model(configuration.generation.model):
        infer_embedding_model(configuration.embedding.model)


def write_environment(path: Path, content: str, *, backup: bool) -> Path | None:
    """Atomically write a private environment file and optionally retain a timestamped backup."""

    path.parent.mkdir(parents=True, exist_ok=True)
    backup_path: Path | None = None
    if backup and path.exists():
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        backup_path = path.with_name(f"{path.name}.bak-{timestamp}")
        shutil.copy2(path, backup_path)
        backup_path.chmod(0o600)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        temporary.chmod(0o600)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup_path


def _collect_connection(
    role: str,
) -> tuple[ModelSelection, tuple[str, ...]]:
    title = role.title()
    available = tuple(
        protocol
        for protocol in API_PROTOCOLS
        if (protocol.generation_adapter if role == "generation" else protocol.embedding_adapter) is not None
    )
    default = available[0].identifier
    protocol_id = _select_value(
        f"{title} API protocol",
        (*((protocol.identifier, protocol.label) for protocol in available), ("custom", "Advanced / custom adapter")),
        default,
    )
    if protocol_id == "custom":
        model, variables, credentials = _collect_custom_connection(title)
        return ModelSelection(model=model, environment=tuple(variables)), credentials

    protocol = _PROTOCOL_BY_ID[protocol_id]
    adapter = protocol.generation_adapter if role == "generation" else protocol.embedding_adapter
    if adapter is None:
        raise ConfigError(f"{protocol.label} does not support {role}")  # noqa: TRY003
    base_url = typer.prompt(
        f"{title} API Base URL",
        default=protocol.default_base_url,
    ).strip()
    api_key = ""
    while not api_key:
        api_key = typer.prompt(
            f"{title} API key",
            default="",
            hide_input=True,
            show_default=False,
        ).strip()
        if not api_key:
            typer.echo("Enter the provider credential (use a non-secret placeholder only when the service ignores it).")
    default_model = protocol.default_generation_model if role == "generation" else protocol.default_embedding_model
    model_name = typer.prompt(f"{title} model", default=default_model or "model-name").strip()
    environment = [
        ProviderVariable(protocol.base_url_name, base_url),
        ProviderVariable(protocol.credential_name, api_key),
    ]
    credentials = (protocol.credential_name,)
    return (
        ModelSelection(
            model=f"{adapter}:{model_name}",
            environment=tuple(environment),
            protocol_id=protocol.identifier,
        ),
        credentials,
    )


def _collect_custom_connection(role: str) -> tuple[str, list[ProviderVariable], tuple[str, ...]]:
    reference_model = "openai-chat:model-name"
    typer.echo("Advanced mode uses a complete Pydantic AI provider:model identifier.")
    typer.echo(f"Reference: {MODEL_CONFIGURATION_URL}")
    model = typer.prompt(f"{role} model identifier", default=reference_model).strip()
    shared: dict[str, str] = {}
    variables, credentials = _collect_initial_provider_variables(role, model, shared)
    additional_credentials = _collect_additional_provider_variables(role, shared, variables)
    return model, variables, credentials + additional_credentials


def _collect_additional_provider_variables(
    role: str,
    shared: dict[str, str],
    variables: list[ProviderVariable],
) -> tuple[str, ...]:
    """Collect extra assignments and the names the user marks as credentials."""

    credentials: list[str] = []
    while True:
        name = typer.prompt(
            f"Additional {role} provider environment variable name (empty to finish)",
            default="",
            show_default=False,
        ).strip()
        if not name:
            break
        if _ENVIRONMENT_NAME.fullmatch(name) is None:
            typer.echo(f"Invalid environment variable name: {name}", err=True)
            continue
        if name in {variable.name for variable in variables}:
            typer.echo(f"{name} is already configured.", err=True)
            continue
        is_credential = _is_secret_name(name) or typer.confirm(
            f"Treat {name} as a credential (hidden input and redaction)?",
            default=False,
        )
        variable = _collect_provider_variable(role, name, shared, is_credential=is_credential)
        if variable is not None:
            variables.append(variable)
            if is_credential:
                credentials.append(name)
    return tuple(credentials)


def _collect_initial_provider_variables(
    role: str,
    reference_model: str,
    shared: dict[str, str],
) -> tuple[list[ProviderVariable], tuple[str, ...]]:
    credential_default = _suggested_credential_name(reference_model)
    base_url_default = _suggested_base_url_name(reference_model)
    variables: list[ProviderVariable] = []
    credentials: list[str] = []
    credential_name = _prompt_provider_variable_name(role, "credential", credential_default)
    if credential_name is not None:
        variable = _collect_provider_variable(role, credential_name, shared, is_credential=True)
        if variable is not None:
            variables.append(variable)
            credentials.append(credential_name)
    base_url_name = _prompt_provider_variable_name(role, "Base URL", base_url_default)
    if base_url_name is not None:
        variable = _collect_provider_variable(role, base_url_name, shared)
        if variable is not None:
            variables.append(variable)
    return variables, tuple(credentials)


def _prompt_provider_variable_name(role: str, label: str, default: str | None) -> str | None:
    displayed_default = default or "-"
    while True:
        value = typer.prompt(
            f"{role} {label} environment variable name ('-' to skip)",
            default=displayed_default,
        ).strip()
        if value == "-":
            return None
        if _ENVIRONMENT_NAME.fullmatch(value) is not None:
            return value
        typer.echo(f"Invalid environment variable name: {value}", err=True)


def _collect_provider_variable(
    role: str,
    name: str,
    shared: dict[str, str],
    *,
    is_credential: bool = False,
) -> ProviderVariable | None:
    if name in shared:
        typer.echo(f"Reusing {name} for {role}.")
        return ProviderVariable(name, shared[name])
    value = typer.prompt(
        name,
        default="",
        hide_input=_is_secret_name(name) or is_credential,
        show_default=False,
    ).strip()
    if value is None:
        return None
    shared[name] = value
    return ProviderVariable(name, value)


def _suggested_environment_names(model: str) -> tuple[str, ...]:
    prefix, separator, _name = model.partition(":")
    return _MODEL_ENVIRONMENT_HINTS.get(prefix, ()) if separator else ()


def _suggested_credential_name(model: str) -> str | None:
    return next((name for name in _suggested_environment_names(model) if _is_secret_name(name)), None)


def _suggested_base_url_name(model: str) -> str | None:
    suggested = next((name for name in _suggested_environment_names(model) if _is_base_url_name(name)), None)
    if suggested is not None:
        return suggested
    prefix, separator, _name = model.partition(":")
    return "OPENAI_BASE_URL" if separator and prefix == "openai" else None


def _is_base_url_name(name: str) -> bool:
    return name.endswith(("_BASE_URL", "_ENDPOINT", "_ENDPOINT_URL"))


def _select_value(prompt: str, choices: Sequence[tuple[str, str]], default: str) -> str:
    if sys.stdin.isatty() and sys.stdout.isatty():
        from InquirerPy import inquirer

        ordered = sorted(choices, key=lambda choice: choice[0] != default)
        return str(
            inquirer.fuzzy(
                message=f"{prompt}:",
                choices=[{"name": label, "value": value} for value, label in ordered],
                instruction="(type to search)",
            ).execute()
        )
    labels = tuple(label for _value, label in choices)
    values = tuple(value for value, _label in choices)
    selected = _choose(prompt, labels, values.index(default) + 1)
    return values[selected - 1]


def _collect_database() -> tuple[str, str | None, str | None]:
    choices = ("sqlite", "oceanbase", "seekdb")
    labels = ("SQLite", "OceanBase", "embedded seekDB")
    kind = choices[_choose("Database", labels, 1) - 1]
    if kind == "oceanbase":
        value = typer.prompt(
            "OceanBase SQLAlchemy URL",
            default="",
            hide_input=True,
            show_default=False,
        ).strip()
        return kind, value or None, None
    if kind == "seekdb":
        value = typer.prompt("seekDB path (empty uses user data directory)", default="").strip()
        return kind, None, value or None
    value = typer.prompt("SQLite URL (empty uses user data database)", default="").strip()
    return kind, value or None, None


def _choose(prompt: str, labels: Sequence[str], default: int) -> int:
    typer.echo(f"{prompt}:")
    for index, label in enumerate(labels, start=1):
        typer.echo(f"  {index}. {label}")
    while True:
        selected = typer.prompt("Choose", default=default, type=int)
        if 1 <= selected <= len(labels):
            return selected
        typer.echo(f"Enter a number from 1 to {len(labels)}.", err=True)


def _validate_model_selection(role: str, selection: ModelSelection) -> None:
    prefix, separator, model_name = selection.model.partition(":")
    if not separator or not prefix or not model_name:
        raise ConfigError(f"{role} model must use a complete provider:model identifier")  # noqa: TRY003
    names: set[str] = set()
    for variable in selection.environment:
        if _ENVIRONMENT_NAME.fullmatch(variable.name) is None:
            raise ConfigError(f"invalid provider environment variable: {variable.name}")  # noqa: TRY003
        if variable.name in names:
            raise ConfigError(f"duplicate provider environment variable: {variable.name}")  # noqa: TRY003
        if _is_base_url_name(variable.name):
            parsed = urlsplit(variable.value)
            if parsed.username is not None or parsed.password is not None:
                raise ConfigError(f"provider Base URL must not contain credentials: {variable.name}")  # noqa: TRY003
        names.add(variable.name)


def _merge_provider_values(values: dict[str, str], selection: ModelSelection) -> None:
    for variable in selection.environment:
        existing = values.get(variable.name)
        if existing is not None and existing != variable.value:
            raise ConfigError(  # noqa: TRY003
                f"Generation and Embedding selected conflicting values for {variable.name}"
            )
        values[variable.name] = variable.value


def _provider_variables(
    role: str,
    model: str,
    metadata: Mapping[str, str],
    values: Mapping[str, str],
) -> tuple[ProviderVariable, ...]:
    key = f"{role}-environment"
    names = (
        tuple(name for name in metadata[key].split(",") if name)
        if key in metadata
        else tuple(name for name in _suggested_environment_names(model) if name in values)
    )
    return tuple(ProviderVariable(name, values[name]) for name in names if name in values)


def _environment_names(selection: ModelSelection) -> str:
    return ",".join(variable.name for variable in selection.environment)


def _scope(values: Mapping[str, str]) -> tuple[str, str]:
    raw = _required(values, "POWERCONTEXT_SERVER_DASHBOARD_SCOPES")
    try:
        first = json.loads(raw)[0]
        return str(first["scope_id"]), str(first["display_name"])
    except (IndexError, KeyError, TypeError, json.JSONDecodeError) as error:
        raise ConfigError(  # noqa: TRY003
            "Dashboard scopes must contain one scope_id and display_name"
        ) from error


def _required(values: Mapping[str, str], name: str) -> str:
    value = values.get(name)
    if value is None or not value.strip():
        raise ConfigError(f"missing required environment value: {name}")  # noqa: TRY003
    return value


def _is_secret_name(name: str) -> bool:
    return (
        name in _SECRET_NAMES
        or name.endswith(("_KEY", "_PASSWORD", "_SECRET", "_TOKEN"))
        or "_KEY_" in name
        or name.endswith(_CREDENTIAL_CONTAINER_SUFFIXES)
    )


def _parse_integer(value: str, name: str) -> int:
    try:
        return int(value)
    except ValueError as error:
        raise ConfigError(f"{name} must be an integer") from error  # noqa: TRY003


def _profile_id(model: str, dimension: int) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", model.casefold()).strip("-")
    return f"{normalized}-{dimension}-unit-v1"


def _managed_metadata(content: str) -> dict[str, str]:
    begin_markers = _managed_marker_matches(content, MANAGED_BEGIN)
    end_markers = _managed_marker_matches(content, MANAGED_END)
    if not begin_markers and not end_markers:
        return {}
    if len(begin_markers) != 1 or len(end_markers) != 1 or end_markers[0].start() < begin_markers[0].end():
        raise ConfigError(  # noqa: TRY003
            "environment contains mismatched or repeated PowerContext managed markers"
        )
    start = begin_markers[0].end()
    end = end_markers[0].start()
    metadata: dict[str, str] = {}
    for line in content[start:end].splitlines():
        stripped = line.strip()
        if stripped.startswith("# ") and "=" in stripped:
            name, value = stripped.removeprefix("# ").split("=", maxsplit=1)
            metadata[name] = value
    return metadata


def _managed_marker_matches(content: str, marker: str) -> tuple[re.Match[str], ...]:
    pattern = re.compile(rf"^[ \t]*{re.escape(marker)}[ \t\r]*$", re.MULTILINE)
    return tuple(pattern.finditer(content))


def _join_document_parts(*parts: str) -> str:
    return "\n\n".join(part for part in parts if part).rstrip() + "\n"


def _validate_server_settings(values: Mapping[str, str]) -> None:
    from powercontext.server.settings import ServerSettings

    managed = {name for name in os.environ if name.startswith("POWERCONTEXT_SERVER_")}
    with _temporary_environment(values, clear=managed):
        ServerSettings()


@contextmanager
def _temporary_environment(values: Mapping[str, str], *, clear: set[str]) -> Iterator[None]:
    original = {name: os.environ.get(name) for name in clear | set(values)}
    try:
        for name in clear:
            os.environ.pop(name, None)
        os.environ.update(values)
        yield
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _report_written(path: Path, backup: Path | None) -> None:
    typer.echo(f"Wrote {path.resolve()} with mode 0600.")
    if backup is not None:
        typer.echo(f"Backup: {backup.resolve()}")


def _print_next_steps(path: Path) -> None:
    quoted = shlex.quote(str(path.resolve()))
    typer.echo(f"\nStart Server:\n  powercontext server run --env-file {quoted}")
    typer.secho("\nSupported Coding Agents (choose one):", bold=True, fg=typer.colors.CYAN)
    for name, setup, launch in AGENTS.values():
        typer.echo(f"\n{name}:\n  {setup}\n  set -a; . {quoted}; set +a; {launch}")


def _fail(message: str) -> Never:
    typer.echo(f"Error: {message}", err=True)
    raise typer.Exit(code=2)


__all__ = [
    "MANAGED_BEGIN",
    "MANAGED_END",
    "ConfigError",
    "GeneratedConfiguration",
    "ModelSelection",
    "ProviderVariable",
    "app",
    "collect_configuration",
    "configuration_from_document",
    "parse_environment",
    "render_environment",
    "render_managed_block",
    "update_environment_document",
    "validate_configuration",
    "write_environment",
]
