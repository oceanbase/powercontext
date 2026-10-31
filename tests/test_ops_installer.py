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

"""Managed operations use real protected files and a simulated native manager."""

from __future__ import annotations

import os
import socket
import sys
from contextlib import closing
from pathlib import Path
from typing import Any

import pytest

from powercontext.ops import Ops
from powercontext.ops.installer import ManagedInstaller, ManagedLayout
from powercontext.ops.resources import PreparedResource, ResourceCatalog, validate_owned_tree, verify
from powercontext.service.controller import ServiceController
from powercontext.service.environment import protect_directory, write_protected_file
from powercontext.service.model import ManagerState, ServiceTarget
from tests.test_ops import _apply_request, _plan, _request
from tests.test_ops_resources import _FILES, _archive, _spec
from tests.test_service import FakeAdapter, _manager_probe


def make_managed_ops(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Ops, FakeAdapter, ManagedInstaller]:
    for key in tuple(os.environ):
        if key == "POWERCONTEXT_HOME" or key.startswith("POWERCONTEXT_SERVER_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr("powercontext.ops.resources._current_platform", lambda: ("windows", "x86_64"))
    root = protect_directory(tmp_path / "managed", create=True)
    layout = ManagedLayout(*(root / name for name in ("programs", "config", "state", "cache", "data")))
    spec = _spec(_archive())
    installer = ManagedInstaller(ResourceCatalog("a" * 64, (spec,)), spec, layout)
    prepared_count = 0

    def prepare(spec, staging_parent, *, cancelled):
        nonlocal prepared_count
        prepared_count += 1
        directory = protect_directory(staging_parent / f"resource-{prepared_count}", create=True)
        for name, content in _FILES.items():
            path = directory / name
            protect_directory(path.parent, create=True)
            path.write_bytes(content)
            if sys.platform != "win32":
                path.chmod(0o700 if name.endswith(".exe") else 0o600)
        verify(spec, directory)
        return PreparedResource(directory, spec, validate_owned_tree(directory))

    monkeypatch.setattr(installer.resources, "prepare", prepare)
    target = installer.target()
    adapter = FakeAdapter(root)
    controller = ServiceController(
        adapter,
        target=ServiceTarget(target.python_executable, target.package_version, isolated_runtime=True),
        probe=_manager_probe(adapter),
        readiness_probe=_manager_probe(adapter),
    )
    return Ops(target, layout.state, controller=controller, installer=installer), adapter, installer


@pytest.fixture
def managed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Ops, FakeAdapter, ManagedInstaller]:
    return make_managed_ops(tmp_path, monkeypatch)


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def install(ops: Ops, port: int | None = None) -> dict[str, Any]:
    result = ops.handle(_apply_request(_plan(ops, "install", http_port=port or _free_port()), "install"))
    assert result["outcome"] == "succeeded", result
    return result


def test_install_is_readonly_until_approval_and_repeated_install_does_not_restart(managed):
    ops, adapter, installer = managed
    port = _free_port()
    plan = _plan(ops, "install", http_port=port)
    assert plan["installation"]["database"]["classification"] == "new"
    assert not installer.layout.programs.exists()
    assert not installer.layout.configuration.exists()
    assert not installer.layout.state.exists()
    result = install(ops, port)
    assert adapter.manager == ManagerState.ACTIVE
    assert adapter.definition.isolated_runtime
    assert result["result"]["components"]["endpoint"] == f"http://127.0.0.1:{port}"
    before = list(adapter.events)
    again = ops.handle(_apply_request(_plan(ops, "install"), "again"))
    assert again["outcome"] == "succeeded", again
    assert again["result"]["components"]["installation_id"] == result["result"]["components"]["installation_id"]
    assert adapter.events == before


def test_repair_missing_python_keeps_manual_stop_and_configuration_revision(managed):
    ops, adapter, installer = managed
    first = install(ops)
    stopped = ops.handle(_apply_request(_plan(ops, "stop"), "stop"))
    assert stopped["outcome"] == "succeeded", stopped
    changed = ops.handle(
        _apply_request(_plan(ops, "config_apply", http_port=_free_port(), dashboard_enabled=False), "configure")
    )
    assert changed["outcome"] == "succeeded", changed
    definition = adapter.definition
    Path(ops.target.python_executable).unlink()
    result = ops.handle(_apply_request(_plan(ops, "repair"), "repair"))
    assert result["outcome"] == "succeeded", result
    assert adapter.manager == ManagerState.INACTIVE
    assert adapter.definition == definition
    assert result["result"]["components"]["installation_id"] == first["result"]["components"]["installation_id"]
    verify(installer.spec, installer.directory)


@pytest.mark.parametrize("phase", ["resources_published", "configuration_prepared", "configured", "registering"])
def test_interrupted_install_resumes_existing_identity_and_configuration(managed, phase):
    ops, adapter, installer = managed
    port = _free_port()
    request = _apply_request(_plan(ops, "install", http_port=port), "install")

    def interrupted(message):
        if message.get("phase") == phase:
            raise SystemExit(99)

    with pytest.raises(SystemExit):
        ops.handle(request, emit=interrupted)
    previous = ops.store.operation("install")
    assert previous is not None
    identity = previous["attempts"][-1]["installation"]["installation_id"]
    plan = _plan(ops, "install", http_port=port)
    resumed = ops.handle({**_apply_request(plan, "install"), "action": "resume", "expected_attempt": 1})
    assert resumed["outcome"] == "succeeded", resumed
    assert resumed["result"]["components"]["installation_id"] == identity
    assert sum(event.startswith("start:") for event in adapter.events) == 1
    assert installer.environment.exists()


def test_cancel_after_download_resumes_without_replacing_prepared_tree(managed):
    ops, adapter, installer = managed
    port = _free_port()

    def cancel(message):
        if message.get("phase") == "prepared":
            response = ops.handle(_request("cancel", operation_id="install", attempt=1))
            assert response["cancel_requested"]

    first = ops.handle(_apply_request(_plan(ops, "install", http_port=port), "install"), emit=cancel)
    assert first["outcome"] == "cancelled", first
    assert adapter.definition is None
    record = ops.store.operation("install")
    assert record is not None
    prepared = record["attempts"][-1]["installation"]["prepared"]["identity"]
    result = ops.handle({
        **_apply_request(_plan(ops, "install", http_port=port), "install"),
        "action": "resume",
        "expected_attempt": 1,
    })
    assert result["outcome"] == "succeeded", result
    assert validate_owned_tree(installer.directory) == prepared


@pytest.mark.parametrize("failure", ["port_conflict", "dispatching_interruption"])
def test_first_registration_failure_can_resume(managed, monkeypatch, failure):
    from powercontext.service.probe import probe_server

    ops, adapter, _installer = managed
    port = _free_port()
    request = _apply_request(_plan(ops, "install", http_port=port), "install")
    with socket.socket() as listener, monkeypatch.context() as fault:
        if failure == "port_conflict":
            fault.setattr(ops.controller, "_probe", lambda endpoint: probe_server(endpoint, timeout=0.1))

            def occupy_port(message):
                if message.get("phase") == "registering":
                    listener.bind(("127.0.0.1", port))
                    listener.listen()

            failed = ops.handle(request, emit=occupy_port)
            assert failed["outcome"] == "uncertain", failed
        else:

            def interrupted(**kwargs):
                raise SystemExit(99)

            fault.setattr(ops.controller, "install", interrupted)
            with pytest.raises(SystemExit):
                ops.handle(request)
    assert adapter.definition is None
    assert adapter.events == []
    record = ops.store.operation("install")
    identity = record["attempts"][-1]["installation"]["installation_id"]
    result = ops.handle({
        **_apply_request(_plan(ops, "install", http_port=port), "install"),
        "action": "resume",
        "expected_attempt": 1,
    })
    assert result["outcome"] == "succeeded", result
    assert result["result"]["components"]["installation_id"] == identity
    assert adapter.manager == ManagerState.ACTIVE


@pytest.mark.parametrize("resume", [True, False])
def test_corrupt_owned_staging_can_be_prepared_again(managed, resume):
    ops, adapter, installer = managed
    port = _free_port()

    def cancel(message):
        if message.get("phase") == "prepared":
            ops.handle(_request("cancel", operation_id="install", attempt=1))

    first = ops.handle(_apply_request(_plan(ops, "install", http_port=port), "install"), emit=cancel)
    assert first["outcome"] == "cancelled", first
    record = ops.store.operation("install")
    state = record["attempts"][-1]["installation"]
    damaged = Path(state["prepared"]["directory"])
    (damaged / next(iter(_FILES))).write_bytes(b"corrupt")
    request = _apply_request(_plan(ops, "install", http_port=port), "install" if resume else "retry")
    if resume:
        request.update(action="resume", expected_attempt=1)
    result = ops.handle(request)
    assert result["outcome"] == "succeeded", result
    assert result["result"]["components"]["installation_id"] == state["installation_id"]
    assert adapter.manager == ManagerState.ACTIVE
    assert not damaged.exists()
    verify(installer.spec, installer.directory)


def test_replaced_staging_is_preserved_and_not_reacquired(managed):
    ops, adapter, _installer = managed
    port = _free_port()

    def cancel(message):
        if message.get("phase") == "prepared":
            ops.handle(_request("cancel", operation_id="install", attempt=1))

    first = ops.handle(_apply_request(_plan(ops, "install", http_port=port), "install"), emit=cancel)
    assert first["outcome"] == "cancelled", first
    record = ops.store.operation("install")
    directory = Path(record["attempts"][-1]["installation"]["prepared"]["directory"])
    directory.rename(directory.with_name("previous"))
    protect_directory(directory, create=True)
    foreign = directory / "user-owned"
    foreign.write_bytes(b"retain")
    result = ops.handle({
        **_apply_request(_plan(ops, "install", http_port=port), "install"),
        "action": "resume",
        "expected_attempt": 1,
    })
    assert result["outcome"] == "failed", result
    assert result["result"]["error"]["code"] == "resource_identity_changed", result
    assert foreign.read_bytes() == b"retain"
    assert adapter.definition is None


def test_existing_unknown_database_blocks_start_but_allows_repair(managed):
    ops, adapter, installer = managed
    install(ops)
    stopped = ops.handle(_apply_request(_plan(ops, "stop"), "stop"))
    assert stopped["outcome"] == "succeeded", stopped
    protect_directory(installer.layout.data, create=True)
    import sqlite3

    with sqlite3.connect(installer.layout.data / "powercontext.db") as database:
        database.execute("CREATE TABLE user_owned(value TEXT)")
        database.execute("INSERT INTO user_owned VALUES ('retained')")
    denied = ops.handle(_request("preflight", operation="start", options={}))
    assert denied["error"]["code"] == "database_compatibility_unknown", denied
    Path(ops.target.python_executable).unlink()
    result = ops.handle(_apply_request(_plan(ops, "repair"), "repair"))
    assert result["outcome"] == "succeeded", result
    assert adapter.manager == ManagerState.INACTIVE
    with sqlite3.connect(installer.layout.data / "powercontext.db") as database:
        assert database.execute("SELECT value FROM user_owned").fetchall() == [("retained",)]


def test_readiness_failure_then_stop_retains_installation_ownership(managed):
    from powercontext.service.model import ProbeResult, ProbeState

    ops, adapter, installer = managed
    port = _free_port()
    ops.controller = ServiceController(
        adapter,
        target=ServiceTarget(ops.target.python_executable, ops.target.package_version, isolated_runtime=True),
        probe=_manager_probe(adapter),
        readiness_probe=lambda _: ProbeResult(ProbeState.CONFLICT, "test readiness conflict"),
    )
    failed = ops.handle(_apply_request(_plan(ops, "install", http_port=port), "install"))
    assert failed["outcome"] in {"partial_failure", "uncertain"}, failed
    stopped = ops.handle(_apply_request(_plan(ops, "stop"), "stop"))
    assert stopped["outcome"] == "succeeded", stopped
    plan = _plan(ops, "install", http_port=port)
    assert not plan["effects"]["may_start"]
    events = list(adapter.events)
    result = ops.handle(_apply_request(plan, "settle"))
    assert result["outcome"] == "succeeded", result
    assert adapter.manager == ManagerState.INACTIVE
    assert adapter.events == events
    assert installer.environment.exists()


def test_missing_registered_configuration_is_not_reset_or_guessed(managed):
    ops, adapter, installer = managed
    install(ops)
    endpoint = adapter.definition.endpoint
    installer.environment.unlink()
    denied = ops.handle(_request("preflight", operation="repair", options={}))
    assert denied["error"]["code"] == "configuration_invalid", denied
    observed = ops.handle(_request("inspect"))
    assert observed["components"]["endpoint"] == endpoint
    assert observed["components"]["configuration"] == "missing_or_invalid"
    assert not installer.environment.exists()


@pytest.mark.parametrize("after_stop", [False, True])
def test_repair_resource_publication_interruption_preserves_identity(managed, monkeypatch, after_stop):
    import powercontext.ops.installer as module

    ops, adapter, installer = managed
    original = install(ops)
    Path(ops.target.python_executable).unlink()
    replace = module.os.replace
    crashed = False

    def interrupted(source, target):
        nonlocal crashed
        replace(source, target)
        if Path(target) == installer.directory and not crashed:
            crashed = True
            raise SystemExit(99)

    monkeypatch.setattr(module.os, "replace", interrupted)
    with pytest.raises(SystemExit):
        ops.handle(_apply_request(_plan(ops, "repair"), "repair"))
    assert adapter.manager == ManagerState.INACTIVE
    published = validate_owned_tree(installer.directory)
    if after_stop:
        stopped = ops.handle(_apply_request(_plan(ops, "stop"), "intervening-stop"))
        assert stopped["outcome"] == "succeeded", stopped
    plan = _plan(ops, "repair")
    assert plan["installation"]["repair_owner"] == "repair"
    if after_stop:
        superseded = ops.handle({**_apply_request(plan, "repair"), "action": "resume", "expected_attempt": 1})
        assert superseded["error"]["code"] == "operation_superseded", superseded
        result = ops.handle(_apply_request(plan, "continued-repair"))
    else:
        result = ops.handle({**_apply_request(plan, "repair"), "action": "resume", "expected_attempt": 1})
    assert result["outcome"] == "succeeded", result
    assert validate_owned_tree(installer.directory) == published
    assert result["result"]["components"]["installation_id"] == original["result"]["components"]["installation_id"]
    assert adapter.manager == ManagerState.INACTIVE


def test_explicit_registration_removal_does_not_reinstall(managed):
    ops, adapter, _installer = managed
    install(ops)
    removed = ops.handle(_apply_request(_plan(ops, "remove_registration"), "remove"))
    assert removed["outcome"] == "succeeded", removed
    denied = ops.handle(_request("preflight", operation="install", options={}))
    assert denied["error"]["code"] == "registration_missing", denied
    assert adapter.definition is None


def test_healthy_repair_stops_without_republishing_resources(managed):
    ops, adapter, installer = managed
    install(ops)
    identity = validate_owned_tree(installer.directory)
    result = ops.handle(_apply_request(_plan(ops, "repair"), "repair"))
    assert result["outcome"] == "succeeded", result
    assert adapter.manager == ManagerState.INACTIVE
    assert validate_owned_tree(installer.directory) == identity
    ops.controller.verify_stopped()


def test_cancel_at_registration_progress_never_starts(managed):
    ops, adapter, _installer = managed

    def cancel(message):
        if message.get("phase") == "registering":
            response = ops.handle(_request("cancel", operation_id="install", attempt=1))
            assert response["cancel_requested"]

    result = ops.handle(_apply_request(_plan(ops, "install", http_port=_free_port()), "install"), emit=cancel)
    assert result["outcome"] == "cancelled", result
    assert adapter.definition is None
    assert not any(event.startswith("start:") for event in adapter.events)


@pytest.mark.parametrize("replace_database", [False, True])
def test_repair_tolerates_committed_database_writes_but_rejects_replacement(managed, replace_database):
    import sqlite3

    ops, adapter, installer = managed
    install(ops)
    protect_directory(installer.layout.data, create=True)
    path = installer.layout.data / "powercontext.db"
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("CREATE TABLE user_owned(value TEXT)")
        database.execute("INSERT INTO user_owned VALUES ('before')")
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO user_owned VALUES ('in-wal')")
    writer.commit()
    Path(ops.target.python_executable).unlink()
    plan = _plan(ops, "repair")
    # A live Server can commit and checkpoint between approval and repair.
    writer.execute("INSERT INTO user_owned VALUES ('after-plan')")
    writer.commit()
    writer.close()
    if replace_database:
        replacement = path.with_name("replacement.db")
        with closing(sqlite3.connect(replacement)) as database, database:
            database.execute("CREATE TABLE replacement(value TEXT)")
        os.replace(replacement, path)
    events = list(adapter.events)
    result = ops.handle(_apply_request(plan, "repair"))
    if replace_database:
        assert result["outcome"] == "rejected", result
        assert result["error"]["code"] == "plan_stale", result
        assert adapter.events == events
        assert not Path(ops.target.python_executable).exists()
    else:
        assert result["outcome"] == "succeeded", result
        assert adapter.manager == ManagerState.INACTIVE
        with closing(sqlite3.connect(path)) as database:
            assert database.execute("SELECT value FROM user_owned ORDER BY rowid").fetchall() == [
                ("before",),
                ("in-wal",),
                ("after-plan",),
            ]
        verify(installer.spec, installer.directory)


def test_malformed_database_url_returns_bounded_configuration_errors(managed):
    ops, adapter, installer = managed
    install(ops)
    write_protected_file(installer.environment, b"POWERCONTEXT_SERVER_DATABASE_URL=bad\n")
    for request in (
        _request("preflight", operation="repair", options={}),
        _request("preflight", operation="start", options={}),
        _request("config_validate"),
    ):
        rejected = ops.handle(request)
        assert rejected["outcome"] == "rejected", rejected
        assert rejected["error"]["code"] == "configuration_invalid", rejected
        assert "bad" not in str(rejected)
    observed = ops.handle(_request("inspect"))
    assert observed["outcome"] == "succeeded", observed
    assert observed["components"]["configuration"] == "missing_or_invalid"
    assert adapter.manager == ManagerState.ACTIVE


def test_repair_rejects_database_inside_managed_program_tree(managed):
    import shlex
    import sqlite3

    from powercontext.paths import sqlite_url

    ops, adapter, installer = managed
    install(ops)
    path = installer.directory / "user.db"
    with sqlite3.connect(path) as database:
        database.execute("CREATE TABLE user_owned(value TEXT)")
        database.execute("INSERT INTO user_owned VALUES ('retained')")
    lines = [
        line
        for line in installer.environment.read_text(encoding="utf-8").splitlines()
        if not line.startswith("POWERCONTEXT_SERVER_DATABASE_URL=")
    ]
    lines.append(f"POWERCONTEXT_SERVER_DATABASE_URL={shlex.quote(sqlite_url(path))}")
    write_protected_file(installer.environment, ("\n".join(lines) + "\n").encode())
    events = list(adapter.events)
    denied = ops.handle(_request("preflight", operation="repair", options={}))
    assert denied["error"]["code"] == "configuration_path_unsupported", denied
    assert adapter.events == events
    with sqlite3.connect(path) as database:
        assert database.execute("SELECT value FROM user_owned").fetchall() == [("retained",)]


@pytest.mark.parametrize("checkpoint", ["failed", "dispatched", "continued", "before_registration"])
def test_uninstalled_partial_install_cannot_be_revived_by_old_recovery(managed, monkeypatch, checkpoint):
    ops, adapter, _installer = managed
    request = _apply_request(_plan(ops, "install", http_port=_free_port()), "install")
    operation_id = "install"
    if checkpoint in {"dispatched", "before_registration"}:
        install_target = ops.controller.install

        def interrupted_install(**kwargs):
            if checkpoint == "dispatched":
                install_target(**kwargs)
            raise SystemExit(99)

        monkeypatch.setattr(ops.controller, "install", interrupted_install)
        with pytest.raises(SystemExit):
            ops.handle(request)
    else:
        adapter.fail_start = True
        failed = ops.handle(request)
        assert failed["outcome"] == "partial_failure", failed
        adapter.fail_start = False
        if checkpoint == "continued":
            request = _apply_request(_plan(ops, "install"), "continued")
            operation_id = "continued"

            def interrupted(message):
                if message.get("phase") == "registering":
                    raise SystemExit(99)

            with pytest.raises(SystemExit):
                ops.handle(request, emit=interrupted)
    # The shared CLI path does not update Ops' latest-operation pointer.
    ops.controller.uninstall()
    events = list(adapter.events)
    denied = ops.handle(_request("preflight", operation="install", options=request["plan"]["options"]))
    assert denied["error"]["code"] == "registration_missing", denied
    resumed = ops.handle({**request, "action": "resume", "expected_attempt": 1, "operation_id": operation_id})
    assert resumed["error"]["code"] == "registration_missing", resumed
    assert adapter.definition is None
    assert adapter.events == events


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ordinary-path budget")
def test_unsupported_managed_path_rejects_preflight_without_creating_resources(managed):
    from dataclasses import replace

    _ops, adapter, installer = managed
    layout = replace(installer.layout, programs=installer.layout.programs.parent / ("p" * 230))
    installer = ManagedInstaller(installer.catalog, installer.spec, layout)
    target = installer.target()
    controller = ServiceController(
        adapter,
        target=ServiceTarget(target.python_executable, target.package_version, isolated_runtime=True),
        probe=_manager_probe(adapter),
        readiness_probe=_manager_probe(adapter),
    )
    ops = Ops(target, layout.state, controller=controller, installer=installer)
    result = ops.handle(_request("preflight", operation="install", options={}))
    assert result["outcome"] == "rejected", result
    assert result["error"]["code"] == "resource_path_unsupported", result
    assert not any(path.exists() for path in (layout.programs, layout.configuration, layout.state, layout.cache))
    assert adapter.events == []
