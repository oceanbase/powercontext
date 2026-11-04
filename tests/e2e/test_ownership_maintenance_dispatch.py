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

"""Offline ownership maintenance must leave scheduled processing untouched."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pytest
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.supervision import (
    ArtifactProcessingBindingStateRepository,
    ArtifactProcessingLeaseRepository,
)
from powercontext.builtin.runtime import BuiltinConfig, InferenceConfig, RuntimeConfig, open_builtin_contexts
from powercontext.builtin.runtime.family_processing import FAMILY_BINDINGS
from powercontext.builtin.scope import ScopeDraft
from powercontext.cli.app import create_cli
from powercontext.server.authz import PrincipalRef, ResourceRef
from powercontext.server.authz.composition import open_builtin_access_control
from powercontext.server.cli import app as server_cli


def _business_snapshot(path: Path) -> dict[str, list[tuple[Any, ...]]]:
    with sqlite3.connect(path) as connection:
        tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        return {
            name: sorted(connection.execute(f'SELECT * FROM "{name}"').fetchall(), key=repr)  # noqa: S608
            for (name,) in tables
            if name.startswith("pc_") and not name.startswith("pc_access_")
        }


@pytest.mark.parametrize("action", ["plan", "apply"])
def test_ownership_cli_preserves_pending_scheduled_work(tmp_path: Path, action: Literal["plan", "apply"]) -> None:
    database_path = tmp_path / "maintenance.db"
    database = SQLiteConfig(url=f"sqlite+aiosqlite:///{database_path.as_posix()}")
    config = BuiltinConfig(
        database=database,
        inference=InferenceConfig(generation_model="test"),
        runtime=RuntimeConfig(artifact_processing_families=("memory",), memory_schedule_seconds=60),
    )

    async def seed() -> tuple[str, str]:
        async with open_builtin_contexts(config) as contexts:
            scope_ids = []
            for title in ("Migration target", "Unrelated scope"):
                scope = await contexts.scopes.create(ScopeDraft(title=title, summary=title, idempotency_key=title))
                scope_ids.append(scope.scope_id)
                await contexts.records.capture_source(
                    scope.scope_id, "content", "pending-source", "Keep this pending.", {}
                )
            (artifact,) = await contexts.records.create_atomic_memories(
                scope_ids[0], ({"kind": "fact", "text": "Legacy fact."},)
            )
            async with contexts.database.transaction() as connection:
                binding = FAMILY_BINDINGS["memory"]
                states = ArtifactProcessingBindingStateRepository()
                await states.start_scan(connection, binding, datetime(2020, 1, 1, tzinfo=UTC))
                await states.finish_scan(connection, binding)
                await ArtifactProcessingLeaseRepository().start_single_process_term(connection, "stopped-server")
            return scope_ids[0], artifact.artifact_id

    scope_id, artifact_id = asyncio.run(seed())
    before = _business_snapshot(database_path)
    environment = tmp_path / "server.env"
    environment.write_text(
        f"POWERCONTEXT_SERVER_DATABASE_KIND=sqlite\nPOWERCONTEXT_SERVER_DATABASE_URL={database.url}\n"
        "POWERCONTEXT_SERVER_ACCESS_MODE=disabled\n"
        "POWERCONTEXT_SERVER_ACCESS_DEPLOYMENT_ID=ownership-maintenance-dispatch\n"
        "POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=test\n"
        "POWERCONTEXT_SERVER_RUNTIME='" + config.runtime.model_dump_json() + "'\n",
        encoding="utf-8",
    )
    arguments = [
        "server",
        "access-ownership-migrate",
        "--env-file",
        str(environment),
        "--action",
        action,
        "--scope-id",
        scope_id,
    ]
    if action == "apply":
        arguments.extend(["--owner", "service:operator", "--maintenance-confirmed"])
    result = CliRunner().invoke(create_cli([server_cli]), arguments)
    assert result.exit_code == 0, result.output or repr(result.exception)
    report = json.loads(result.output)
    if action == "plan":
        assert len(report["scopes"][0]["artifact_gaps"]) == 1
    else:
        assert len(report["applied"]) == 1
        assert report["applied"][0]["gap"]["family"] == "atomic-memory"
        assert report["conflicts"] == []

    after = _business_snapshot(database_path)
    assert after["pc_artifact_processing_intents"] == before["pc_artifact_processing_intents"]
    assert after["pc_artifact_processing_binding_states"] == before["pc_artifact_processing_binding_states"]
    assert after["pc_artifact_processing_leases"] == before["pc_artifact_processing_leases"]
    assert after == before

    if action == "apply":

        async def check_audit() -> None:
            async with open_builtin_access_control(database, deployment_id="ownership-maintenance-dispatch") as access:
                assert access.audit is not None
                events = await access.audit.list_audit(
                    resource=ResourceRef.artifact(scope_id, family="atomic-memory", artifact_id=artifact_id)
                )
                owners = [event for event in events if event.reason_code == "artifact-owner-established"]
                assert len(owners) == 1
                assert owners[0].principal == PrincipalRef(type="service", id="operator")
                assert owners[0].request_id is None
                assert owners[0].actor is None
                assert owners[0].transport == "background"
                assert owners[0].operation == "access.ownership.apply"

        asyncio.run(check_audit())
