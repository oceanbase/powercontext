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

"""Adopting generation models on an initialized model-free deployment is one offline switch."""

import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

import pytest
from pydantic import AnyHttpUrl
from typer.testing import CliRunner

from powercontext.builtin.persistence.migrations.models import MigrationError
from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime.config import InferenceConfig, RuntimeConfig
from powercontext.cli.app import create_cli
from powercontext.server.cli import app
from powercontext.server.settings import ServerSettings

GENERATION = InferenceConfig(
    generation_model="openai-chat:test-model", generation_base_url=AnyHttpUrl("http://127.0.0.1:9/v1")
)
# A deployment that stores vectors. The Processing switch has no registered path for it.
VECTORS = InferenceConfig(embedding_model="openai:fixture", embedding_profile_id="fixture", embedding_dimension=3)
COMMAND = ["server", "processing-migrate"]


@pytest.fixture(autouse=True)
def clean_server_environment(monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)


def _storage(tmp_path: Path) -> SQLiteConfig:
    return SQLiteConfig(url=f"sqlite+aiosqlite:///{(tmp_path / 'server.db').as_posix()}")


def _environment(path: Path, database: SQLiteConfig, **inference: object) -> Path:
    """The new configuration as an environment file, which every step of the switch loads."""
    lines = ["POWERCONTEXT_SERVER_DATABASE_KIND=sqlite", f"POWERCONTEXT_SERVER_DATABASE_URL={database.url}"]
    lines += [f"POWERCONTEXT_SERVER_INFERENCE_{name.upper()}={value}" for name, value in inference.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _switch(environment: Path, *arguments: str) -> dict[str, Any]:
    """One step of the switch as an operator runs it: through the CLI, without a terminal, loading the file."""
    result = CliRunner().invoke(create_cli([app]), [*COMMAND, "--env-file", str(environment), *arguments])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def _generation(inference: InferenceConfig) -> dict[str, Any]:
    return {"generation_model": inference.generation_model, "generation_base_url": inference.generation_base_url}


def test_a_first_generation_model_names_a_switch_the_managed_cli_completes(tmp_path):
    database = _storage(tmp_path)
    # The basic-memory start records a Processing declaration without capabilities, on managed storage.
    prepare_server_storage(ServerSettings(database=database))

    configured = ServerSettings(database=database, inference=GENERATION)
    with pytest.raises(MigrationError, match="incompatible_processing_configuration") as refused:
        prepare_server_storage(configured)
    # An operator follows what the message quotes: its fragments are the steps, in order.
    steps = [shlex.split(fragment) for fragment in re.findall(r"'([^']+)'", str(refused.value))]
    assert len(steps) == 3 and steps[0][:3] == ["powercontext", "server", "processing-migrate"]
    environment = _environment(tmp_path / "gen.env", database, **_generation(GENERATION))

    values = {"<new-id>": "add-generation-1"}
    plan = _switch(environment, *(values.get(argument, argument) for argument in steps[0][3:]))
    values["<plan_id>"] = plan["plan_id"]
    assert _switch(environment, *(values.get(argument, argument) for argument in steps[1]))["state"] == "ready"
    assert _switch(environment, *(values.get(argument, argument) for argument in steps[2]))["state"] == "ready"
    # The same data directory then starts with the models.
    prepare_server_storage(configured)


def test_a_deployment_that_stores_vectors_keeps_the_plain_refusal(tmp_path):
    database = _storage(tmp_path)
    prepare_server_storage(ServerSettings(database=database, inference=VECTORS))
    widened = VECTORS.model_copy(update=_generation(GENERATION))
    with pytest.raises(MigrationError, match="incompatible_processing_configuration") as refused:
        prepare_server_storage(ServerSettings(database=database, inference=widened))
    # The switch rejects vector storage, so there is no command the message could name.
    assert "processing-migrate" not in str(refused.value)
    environment = _environment(
        tmp_path / "gen.env",
        database,
        embedding_model=VECTORS.embedding_model,
        embedding_profile_id=VECTORS.embedding_profile_id,
        embedding_dimension=VECTORS.embedding_dimension,
        **_generation(GENERATION),
    )
    plan = CliRunner().invoke(create_cli([app]), [*COMMAND, "--env-file", str(environment), "--action", "plan"])
    assert json.loads(plan.output)["error"] == "unsupported_upgrade_configuration"


def test_a_configuration_that_reshapes_ownership_keeps_the_plain_refusal(tmp_path):
    database = _storage(tmp_path)
    prepare_server_storage(ServerSettings(database=database))

    reshaped = ServerSettings(
        database=database,
        runtime=RuntimeConfig(artifact_processing_supervisor_mode="dedicated"),
        inference=GENERATION,
    )
    with pytest.raises(MigrationError, match="incompatible_processing_configuration") as refused:
        prepare_server_storage(reshaped)
    assert "processing-migrate" not in str(refused.value)
    assert "differs from the migrated declaration." in str(refused.value)
