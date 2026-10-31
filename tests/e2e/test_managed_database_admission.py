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

"""Real target child, full SQLite Server and public Memory; simulated OS manager/resources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shlex
import sqlite3
import sys
from contextlib import closing
from importlib.metadata import version
from pathlib import Path

import pytest

from powercontext.builtin.persistence.migrations.server_readiness import prepare_server_storage
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.ops import Ops
from powercontext.ops.installer import ManagedInstaller, ManagedLayout
from powercontext.ops.local_policy import inspect_existing_configuration
from powercontext.ops.resources import PreparedResource, ResourceCatalog, validate_owned_tree, verify
from powercontext.server.configuration import server_settings_context
from powercontext.service.controller import ServiceController
from powercontext.service.environment import protect_directory, read_protected_file, write_protected_file
from powercontext.service.model import ManagerState, ServiceTarget
from tests.test_ops import _apply_request, _plan, _request
from tests.test_ops_installer import install
from tests.test_ops_resources import _archive, _spec
from tests.test_service import FakeAdapter, _manager_probe

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="POSIX test launcher delegates to the actual test interpreter"
)


@pytest.fixture
def managed_database(tmp_path, monkeypatch):
    for name in tuple(os.environ):
        if name.upper() == "POWERCONTEXT_HOME" or name.upper().startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name)
    monkeypatch.setattr("powercontext.ops.resources._current_platform", lambda: ("windows", "x86_64"))
    launcher = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$@"\n'.encode()
    files = {"python/pythonw.exe": launcher, "python/python.exe": launcher, "LICENSE": b"local test fixture"}
    spec = _spec(
        _archive(files),
        server_version=version("powercontext"),
        unpacked_size=sum(map(len, files.values())),
        files=[
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
                "executable": name.endswith(".exe"),
            }
            for name, content in files.items()
        ],
    )
    layout = ManagedLayout(*(tmp_path / name for name in ("programs", "config", "state", "cache", "data")))
    installer = ManagedInstaller(ResourceCatalog("b" * 64, (spec,)), spec, layout)

    def prepare(selected, staging_parent, *, cancelled):
        directory = protect_directory(staging_parent / "test-resource", create=True)
        for name, content in files.items():
            path = directory / name
            protect_directory(path.parent, create=True)
            path.write_bytes(content)
            path.chmod(0o700 if name.endswith(".exe") else 0o600)
        verify(selected, directory)
        return PreparedResource(directory, selected, validate_owned_tree(directory))

    monkeypatch.setattr(installer.resources, "prepare", prepare)
    target = installer.target()
    adapter = FakeAdapter(protect_directory(tmp_path / "manager", create=True))
    controller = ServiceController(
        adapter,
        target=ServiceTarget(target.python_executable, target.package_version, isolated_runtime=True),
        probe=_manager_probe(adapter),
        readiness_probe=_manager_probe(adapter),
    )
    ops = Ops(target, layout.state, controller=controller, installer=installer)
    install(ops)
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop"))["outcome"] == "succeeded"
    summary = inspect_existing_configuration(installer.environment, data_dir=layout.data)
    with server_settings_context(env_file=installer.environment, data_dir=layout.data) as settings:
        prepare_server_storage(settings)
    return ops, adapter, installer, summary


def _files(database):
    return {
        suffix: (path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ctime_ns)
        for suffix in ("", "-wal", "-shm", "-journal")
        if (path := Path(f"{database}{suffix}")).exists()
    }


def _runtime(installer):
    with server_settings_context(env_file=installer.environment, data_dir=installer.layout.data) as settings:
        return BuiltinConfig(database=settings.database)


async def _seed(config):
    async with open_builtin_contexts(config) as contexts:
        await contexts.get("admission-history")
        record = await contexts.records.create_artifact(
            "admission-history",
            "atomic-memory",
            ArtifactWrite(content={"kind": "fact", "text": "Original exact fact."}),
        )
        memory = contexts.atomic_memory.for_scope("admission-history")
        original = await memory.get(record.artifact_id)
        await contexts.records.replace_artifact(
            "admission-history",
            "atomic-memory",
            original.ref.artifact_id,
            f'"revision:{original.ref.revision}"',
            ArtifactWrite(content={"kind": "fact", "text": "Latest exact fact."}),
        )
        return original, await memory.get(record.artifact_id)


async def _read(config, original, latest):
    async with open_builtin_contexts(config) as contexts:
        memory = contexts.atomic_memory.for_scope("admission-history")
        assert await memory.get(latest.ref.artifact_id) == latest
        assert await memory.get(original.ref.artifact_id, revision=original.ref.revision) == original
        for item in (original, latest):
            stored = await contexts.records.get_artifact_revision(
                "admission-history", "atomic-memory", item.ref.artifact_id, item.ref.revision
            )
            assert stored.content["text"] == item.artifact.content.text


def test_start_restart_and_program_repair_retain_exact_memory_and_configuration(managed_database):
    ops, adapter, installer, summary = managed_database
    config = _runtime(installer)
    original, latest = asyncio.run(_seed(config))
    for operation in ("start", "restart", "config_apply"):
        before = _files(summary.database_path)
        options = {"restart": True, "dashboard_enabled": False} if operation == "config_apply" else {}
        plan = _plan(ops, operation, **options)
        assert plan["effects"]["may_start"]
        assert _files(summary.database_path) == before
        result = ops.handle(_apply_request(plan, operation))
        assert result["outcome"] == "succeeded", result
        assert _files(summary.database_path) == before
        asyncio.run(_read(config, original, latest))
    selected = Path(adapter.definition.env_file.path)
    configuration = read_protected_file(selected)
    Path(ops.target.python_executable).unlink()
    assert ops.handle(_request("inspect"))["outcome"] == "succeeded"
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop-damaged"))["outcome"] == "succeeded"
    repaired = ops.handle(_apply_request(_plan(ops, "repair"), "repair"))
    assert repaired["outcome"] == "succeeded", repaired
    assert adapter.manager == ManagerState.INACTIVE
    assert read_protected_file(selected) == configuration
    restarted = ops.handle(_apply_request(_plan(ops, "start"), "start-repaired"))
    assert restarted["outcome"] == "succeeded", restarted
    asyncio.run(_read(config, original, latest))


def test_writes_invalidate_restart_approval_and_stop_then_fresh_start_preserves_them(managed_database):
    ops, adapter, installer, _summary = managed_database
    assert ops.handle(_apply_request(_plan(ops, "start"), "initial-start"))["outcome"] == "succeeded"
    plan = _plan(ops, "restart")
    config = _runtime(installer)
    original, latest = asyncio.run(_seed(config))
    before = list(adapter.events)
    result = ops.handle(_apply_request(plan, "restart-after-write"))
    assert result["outcome"] == "rejected" and result["error"]["code"] == "plan_stale"
    assert adapter.events == before and adapter.manager == ManagerState.ACTIVE
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop-writer"))["outcome"] == "succeeded"
    assert adapter.manager == ManagerState.INACTIVE
    assert ops.handle(_apply_request(_plan(ops, "start"), "fresh-start"))["outcome"] == "succeeded"
    asyncio.run(_read(config, original, latest))


def test_wal_only_schema_is_admitted_without_touching_source_sidecars(managed_database, tmp_path):
    ops, _adapter, installer, summary = managed_database
    # Start with a fresh managed Server in WAL. Keep the first connection open
    # so committed schema and records have not been checkpointed into main.
    summary.database_path.unlink()
    with closing(sqlite3.connect(summary.database_path)) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("BEGIN")
        writer.execute("SELECT name FROM sqlite_master").fetchall()
        with server_settings_context(env_file=installer.environment, data_dir=installer.layout.data) as settings:
            # The previous fresh bootstrap's complete journal is not an adoption
            # authority for a different file; create this fixture independently.
            import shutil

            shutil.rmtree(Path(f"{summary.database_path}.pc-migration-state"))
            prepare_server_storage(settings)
        config = _runtime(installer)
        original, latest = asyncio.run(_seed(config))
        assert Path(f"{summary.database_path}-wal").stat().st_size > 0
        isolated_main = tmp_path / "only-main.db"
        isolated_main.write_bytes(summary.database_path.read_bytes())
        with closing(sqlite3.connect(isolated_main)) as main:
            assert main.execute("SELECT name FROM sqlite_master WHERE name='pc_schema_revision'").fetchone() is None
        before = _files(summary.database_path)
        plan = _plan(ops, "start")
        assert ops.handle(_apply_request(plan, "wal-start"))["outcome"] == "succeeded"
        assert _files(summary.database_path) == before
        asyncio.run(_read(config, original, latest))


@pytest.mark.parametrize("changed", ["database", "configuration", "evidence", "resource"])
def test_start_revalidates_database_configuration_evidence_and_target(managed_database, changed):
    ops, adapter, installer, summary = managed_database
    plan = _plan(ops, "start")
    if changed == "database":
        asyncio.run(_seed(_runtime(installer)))
    elif changed == "configuration":
        write_protected_file(installer.environment, installer.environment.read_bytes() + b"\n# revised configuration\n")
    elif changed == "evidence":
        path = Path(f"{summary.database_path}.pc-migration-state/maintenance.json")
        path.write_text(path.read_text() + "\n")
    else:
        (installer.directory / "LICENSE").write_bytes(b"changed")
    before = list(adapter.events)
    result = ops.handle(_apply_request(plan, "stale"))
    assert result["outcome"] == "rejected", result
    assert result["error"]["code"] in {"plan_stale", "runtime_damaged"}
    assert adapter.events == before


@pytest.mark.parametrize("damage", ["unknown", "future", "schema", "maintenance", "restore", "profile", "journal"])
def test_full_server_rejects_unsupported_or_unfinished_database(managed_database, damage):
    ops, adapter, installer, summary = managed_database
    if damage == "maintenance":
        path = Path(f"{summary.database_path}.pc-migration-state/maintenance.json")
        evidence = json.loads(path.read_text())
        evidence["state"] = "active"
        path.write_text(json.dumps(evidence))
    elif damage == "profile":
        write_protected_file(
            installer.environment,
            installer.environment.read_bytes()
            + (
                b"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_MODEL=openai:fake\n"
                b"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_PROFILE_ID=different\n"
                b"POWERCONTEXT_SERVER_INFERENCE_EMBEDDING_DIMENSION=3\n"
            ),
        )
    elif damage == "journal":
        Path(f"{summary.database_path}-journal").write_bytes(b"hot rollback journal")
    else:
        with closing(sqlite3.connect(summary.database_path)) as connection, connection:
            if damage == "unknown":
                connection.execute("DROP TABLE pc_schema_revision")
                connection.execute("CREATE TABLE unfamiliar (value TEXT)")
            elif damage == "future":
                connection.execute("UPDATE pc_schema_revision SET version_num='future_unknown'")
            elif damage == "schema":
                connection.execute("DROP INDEX ix_pc_processing_intent_binding_sequence")
            else:
                connection.execute(
                    "INSERT INTO pc_portable_restore_receipts "
                    "(bundle_id, format_version, total_digest, status, record_count, inserted, already_present, projections_ready, updated_at) "
                    "VALUES ('partial',1,'sha256:digest','authoritative_restored',0,0,0,0,'2026-01-01T00:00:00Z')"
                )
    before = _files(summary.database_path)
    events = list(adapter.events)
    result = ops.handle(_request("preflight", operation="start", options={}))
    assert result["outcome"] == "rejected", result
    assert (
        result["error"]["code"]
        == {
            "unknown": "database_compatibility_unknown",
            "future": "database_incompatible",
            "schema": "database_incompatible",
            "maintenance": "database_recovery_required",
            "restore": "database_recovery_required",
            "profile": "database_configuration_mismatch",
            "journal": "database_recovery_required",
        }[damage]
    )
    assert _files(summary.database_path) == before
    assert adapter.events == events
