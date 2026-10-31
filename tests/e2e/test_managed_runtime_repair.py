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

"""Real SQLite/public Memory retention; the resource download and OS manager are simulated."""

from __future__ import annotations

import asyncio
import hashlib
import os
import socket
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.ops import Ops, resources
from powercontext.ops.installer import ManagedInstaller, ManagedLayout
from powercontext.ops.local_policy import inspect_existing_configuration
from powercontext.ops.resources import ResourceCatalog
from powercontext.paths import sqlite_url
from powercontext.service.controller import ServiceController
from powercontext.service.environment import protect_directory, read_protected_file
from powercontext.service.model import ManagerState, ServiceTarget
from tests.test_ops import _apply_request, _plan
from tests.test_ops_resources import _FILES, _archive, _spec, _transport
from tests.test_service import FakeAdapter, _manager_probe


def test_same_version_repair_preserves_public_memory_history_and_user_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in tuple(os.environ):
        if name.upper() == "POWERCONTEXT_HOME" or name.upper().startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(name)
    archive = _archive()
    spec = _spec(archive)
    monkeypatch.setattr(resources, "_current_platform", lambda: ("windows", "x86_64"))
    _transport(monkeypatch, lambda _request: httpx.Response(200, stream=httpx.ByteStream(archive)))
    layout = ManagedLayout(*(tmp_path / name for name in ("programs", "configuration", "state", "cache", "data")))
    installer = ManagedInstaller(ResourceCatalog(hashlib.sha256(b"fixture catalog").hexdigest(), (spec,)), spec, layout)
    target = installer.target()
    adapter = FakeAdapter(protect_directory(tmp_path / "manager", create=True))
    controller = ServiceController(
        adapter,
        target=ServiceTarget(target.python_executable, target.package_version, isolated_runtime=True),
        probe=_manager_probe(adapter),
        readiness_probe=_manager_probe(adapter),
    )
    ops = Ops(target, layout.state, controller=controller, installer=installer)
    with socket.socket() as available_port:
        available_port.bind(("127.0.0.1", 0))
        port = available_port.getsockname()[1]
    installed = ops.handle(_apply_request(_plan(ops, "install", http_port=port), "install"))
    assert installed["outcome"] == "succeeded", installed
    installation_id = installed["result"]["components"]["installation_id"]
    assert ops.handle(_apply_request(_plan(ops, "stop"), "stop"))["outcome"] == "succeeded"
    configured = ops.handle(_apply_request(_plan(ops, "config_apply", dashboard_enabled=False), "configure"))
    assert configured["outcome"] == "succeeded", configured
    definition = adapter.definition
    assert definition is not None and definition.env_file is not None
    selected = Path(definition.env_file.path)
    configuration_before = read_protected_file(selected)
    summary = inspect_existing_configuration(selected, data_dir=Path(definition.data_dir))
    database = BuiltinConfig(database=SQLiteConfig(url=sqlite_url(summary.database_path)))
    scope = "managed-repair-retention"

    async def seed_history():
        async with open_builtin_contexts(database) as contexts:
            await contexts.get(scope)
            created = await contexts.records.create_artifact(
                scope, "atomic-memory", ArtifactWrite(content={"kind": "fact", "text": "Original retained fact."})
            )
            memory = contexts.atomic_memory.for_scope(scope)
            original = await memory.get(created.artifact_id)
            await contexts.records.replace_artifact(
                scope,
                "atomic-memory",
                original.ref.artifact_id,
                f'"revision:{original.ref.revision}"',
                ArtifactWrite(content={"kind": "fact", "text": "Revised retained fact."}),
            )
            return original, await memory.get(original.ref.artifact_id)

    original, current = asyncio.run(seed_history())
    # Corrupt a file in the owned bundle. The actual resource verifier and
    # publisher repair the programs; no Server executable is run by this test.
    (installer.directory / "LICENSE").write_bytes(b"damaged program resource")
    repair_plan = _plan(ops, "repair")
    assert repair_plan["installation"]["database"]["classification"] == "existing_unknown"
    assert not repair_plan["effects"]["may_start"]
    repaired = ops.handle(_apply_request(repair_plan, "repair"))
    assert repaired["outcome"] == "succeeded", repaired
    assert repaired["result"]["components"]["installation_id"] == installation_id
    assert (installer.directory / "LICENSE").read_bytes() == _FILES["LICENSE"]
    assert controller.verify_stopped().manager is ManagerState.INACTIVE
    assert adapter.definition == definition
    assert read_protected_file(selected) == configuration_before
    assert inspect_existing_configuration(selected, data_dir=Path(definition.data_dir)) == summary

    async def read_history() -> None:
        # Reopening through the repository runtime verifies retained data. It
        # does not certify another packaged Server version's schema compatibility.
        async with open_builtin_contexts(database) as contexts:
            memory = contexts.atomic_memory.for_scope(scope)
            assert await memory.get(current.ref.artifact_id) == current
            assert await memory.get(original.ref.artifact_id, revision=original.ref.revision) == original
            for record in (original, current):
                stored = await contexts.records.get_artifact_revision(
                    scope, "atomic-memory", record.ref.artifact_id, record.ref.revision
                )
                assert stored.content["text"] == record.artifact.content.text

    asyncio.run(read_history())
