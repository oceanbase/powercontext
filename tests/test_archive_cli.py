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

import asyncio
import json
import sqlite3
import struct
import zipfile
from pathlib import Path

import pytest
from click.utils import strip_ansi
from sqlalchemy import update
from typer.testing import CliRunner

from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.persistence.tables import SCOPES_TABLE
from powercontext.builtin.records import ArtifactWrite
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.cli import archive as archive_module
from powercontext.cli.app import create_cli
from powercontext.cli.archive import archive_app
from powercontext.paths import default_database_path, sqlite_url
from powercontext.sources import AdapterSourceDefinition, SourceDefinitionRegistry
from tests.builtin.persistence.contract import NoteAdapter, NoteInput


def test_archive_cli_dry_run_reports_legacy_target_without_migrating_it(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "revisions.pcb"
    target_file = tmp_path / "target.db"
    target_url = sqlite_url(target_file)
    target_config = BuiltinConfig(database=SQLiteConfig(url=target_url))

    async def seed() -> None:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Revision metadata", summary="Upgrade before validation", idempotency_key="metadata")
            )
            await source.records.create_artifact(
                scope.scope_id, "profile", ArtifactWrite(content={"content": "# Profile\n\nRetain revision metadata."})
            )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        async with open_builtin_contexts(target_config):
            pass

    asyncio.run(seed())
    with sqlite3.connect(target_file) as connection:
        for column in ("created_at", "created_by", "restored_from_revision", "rollback_reason"):
            connection.execute(f"ALTER TABLE pc_artifacts DROP COLUMN {column}")
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", target_url)
    runner = CliRunner()
    cli = create_cli([archive_app])
    before = target_file.read_bytes()
    dry_run = runner.invoke(cli, ["archive", "restore", str(archive), "--dry-run"])
    assert dry_run.exit_code == 3, dry_run.output
    assert "target schema lacks required archive fields" in dry_run.output
    assert target_file.read_bytes() == before
    restored = runner.invoke(cli, ["archive", "restore", str(archive), "--yes"])
    assert restored.exit_code == 0, restored.output
    compatible = runner.invoke(cli, ["archive", "restore", str(archive), "--dry-run"])
    assert compatible.exit_code == 0, compatible.output


def test_archive_cli_restore_reports_unsupported_native_sources_consistently(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "native.pcb"

    async def seed() -> None:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        registry = SourceDefinitionRegistry((AdapterSourceDefinition(NoteAdapter()),))
        async with open_builtin_contexts(BuiltinConfig(), source_registry=registry) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Native", summary="Missing target adapter", idempotency_key="native")
            )
            native = await registry.resolve(NoteInput(note_id="one", body="Native evidence"))
            async with source.database.transaction() as connection:
                await source.repositories.sources.add(connection, scope.scope_id, native)
            await source.portability.export([scope.scope_id], archive, authorize=authorize)

    asyncio.run(seed())
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "target"))
    runner = CliRunner()
    cli = create_cli([archive_app])
    for confirmation in ("--dry-run", "--yes"):
        result = runner.invoke(cli, ["archive", "restore", str(archive), confirmation])
        assert result.exit_code == 3, result.output


def test_archive_cli_exposes_a_safe_restore_confirmation_and_dry_run() -> None:
    cli = create_cli([archive_app])
    runner = CliRunner()

    help_result = runner.invoke(cli, ["archive", "--help"], terminal_width=160)
    restore_help = runner.invoke(cli, ["archive", "restore", "--help"], terminal_width=160)

    help_output = strip_ansi(help_result.output)
    restore_output = strip_ansi(restore_help.output)

    assert help_result.exit_code == 0
    assert all(command in help_output for command in ("export", "inspect", "restore"))
    assert restore_help.exit_code == 0
    assert "--dry-run" in restore_output
    assert "--yes" in restore_output


@pytest.mark.parametrize(
    ("field", "value"),
    [("record_type", []), ("record_type", {}), ("schema_version", True), ("format_version", True)],
)
def test_archive_cli_normalizes_invalid_wire_types(tmp_path, field, value) -> None:
    archive = tmp_path / "invalid.pcb"

    async def seed() -> None:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Wire types", summary="Reject malformed input", idempotency_key="wire-types")
            )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)

    asyncio.run(seed())
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
        records = [json.loads(line) for line in bundle.read("records.ndjson").splitlines()]
    if field == "format_version":
        manifest[field] = value
    else:
        records[0][field] = value
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("manifest.json", json.dumps(manifest))
        bundle.writestr("records.ndjson", "\n".join(json.dumps(record) for record in records))
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", "inspect", str(archive)])
    assert result.exit_code == 2, (result.output, result.exception)
    assert "archive validation failed" in result.output


@pytest.mark.parametrize("operation", [("inspect",), ("restore", "--dry-run"), ("restore", "--yes")])
def test_archive_cli_rejects_unsupported_zip_versions(tmp_path, monkeypatch, operation) -> None:
    archive = tmp_path / "unsupported.pcb"
    entry = zipfile.ZipInfo("manifest.json")
    entry.extract_version = 64
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(entry, b"{}")
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "target"))
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", *operation, str(archive)])
    assert result.exit_code == 2, (result.output, result.exception)
    assert "archive validation failed" in result.output


def test_archive_cli_can_export_inspect_and_dry_run_a_scope(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "data"))
    output = tmp_path / "scope.pcb"
    cli = create_cli([archive_app])
    runner = CliRunner()

    async def default_scope_id() -> str:
        config = BuiltinConfig(database=SQLiteConfig(url=sqlite_url(default_database_path())))
        async with open_builtin_runtime(config) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.default_scope()
            assert scope is not None
            return scope.scope_id

    scope_id = asyncio.run(default_scope_id())
    exported = runner.invoke(cli, ["archive", "export", "--scope-id", scope_id, "--output", str(output)])
    target_home = tmp_path / "unused-target"
    monkeypatch.setenv("POWERCONTEXT_HOME", str(target_home))
    inspected = runner.invoke(cli, ["archive", "inspect", str(output)])
    validated = runner.invoke(cli, ["archive", "restore", str(output), "--dry-run"])

    assert exported.exit_code == 0, exported.output
    assert output.is_file()
    assert not (target_home / "powercontext.db").exists()
    assert inspected.exit_code == 0, inspected.output
    assert validated.exit_code == 0, validated.output
    restored = runner.invoke(cli, ["archive", "restore", str(output), "--yes"])
    assert restored.exit_code == 0, restored.output
    repeated = runner.invoke(cli, ["archive", "restore", str(output), "--yes"])
    assert repeated.exit_code == 0, repeated.output
    assert json.loads(repeated.stdout)["inserted"] == 0
    assert asyncio.run(default_scope_id()) == scope_id


@pytest.mark.parametrize("entry", ["manifest.json", "records.ndjson"])
@pytest.mark.parametrize("operation", ["inspect", "dry-run", "restore"])
def test_archive_cli_rejects_invalid_deflate_streams(tmp_path, monkeypatch, entry, operation) -> None:
    archive = tmp_path / "damaged.pcb"

    async def seed() -> None:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(BuiltinConfig()) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Compression", summary="Corrupt stream", idempotency_key="compression")
            )
            await contexts.portability.export([scope.scope_id], archive, authorize=authorize)

    asyncio.run(seed())
    with zipfile.ZipFile(archive) as bundle:
        header_offset = bundle.getinfo(entry).header_offset
    data = bytearray(archive.read_bytes())
    filename_size, extra_size = struct.unpack_from("<HH", data, header_offset + 26)
    stream_start = header_offset + 30 + filename_size + extra_size
    data[stream_start] = (data[stream_start] & ~6) | 6
    archive.write_bytes(data)
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "target"))
    arguments = ["archive", "inspect", str(archive)]
    if operation != "inspect":
        arguments = ["archive", "restore", str(archive), "--dry-run" if operation == "dry-run" else "--yes"]
    result = CliRunner().invoke(create_cli([archive_app]), arguments)
    assert result.exit_code == 2, result.output
    assert "cannot read portable bundle" in result.output
    assert "Traceback" not in result.output


@pytest.mark.parametrize("failure", ["directory", "permission", "authority", "parent-file", "parent-file-as-missing"])
def test_archive_cli_dry_run_rejects_uninspectable_sqlite_target(tmp_path, monkeypatch, failure) -> None:
    archive = tmp_path / "scope.pcb"
    target = tmp_path / "target"
    target.mkdir()

    async def seed() -> None:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async with open_builtin_contexts(BuiltinConfig()) as contexts:
            scope = await contexts.scopes.create(
                ScopeDraft(title="Inspection", summary="Fail closed", idempotency_key="inspection")
            )
            await contexts.portability.export([scope.scope_id], archive, authorize=authorize)

    asyncio.run(seed())
    if failure.startswith("parent-file"):
        target.rmdir()
        target.write_text("not a directory", encoding="utf-8")
        target = target / "target.db"
    if failure in {"permission", "parent-file-as-missing"}:
        original_stat = Path.stat

        def inaccessible(path, *arguments, **keywords):
            if path == target:
                if failure == "permission":
                    raise PermissionError
                raise FileNotFoundError
            return original_stat(path, *arguments, **keywords)

        monkeypatch.setattr(Path, "stat", inaccessible)
    url = f"sqlite+aiosqlite:///{target}"
    if failure == "authority":
        url = f"sqlite+aiosqlite:///file://other-host/{target.as_posix().lstrip('/')}?uri=true"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", url)
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", "restore", str(archive), "--dry-run"])
    assert result.exit_code == 2, result.output
    assert "cannot inspect configured SQLite target" in result.output


def test_archive_cli_requires_projection_recovery_before_export(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "target"))
    archive = tmp_path / "recovery.pcb"
    output = tmp_path / "exported.pcb"

    async def seed() -> str:
        async def authorize(_scopes: tuple[str, ...]) -> None:
            pass

        async def interrupted(_scopes: tuple[str, ...]) -> None:
            raise RuntimeError("interrupted")

        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="Recovery", summary="Incomplete projections", idempotency_key="incomplete")
            )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        config = BuiltinConfig(database=SQLiteConfig(url=sqlite_url(default_database_path())))
        async with open_builtin_contexts(config) as target:
            with pytest.raises(RuntimeError, match="interrupted"):
                await archive_module.PortableBundleService(target.database, projection_rebuilder=interrupted).restore(
                    archive
                )
        return scope.scope_id

    scope_id = asyncio.run(seed())
    cli = create_cli([archive_app])
    runner = CliRunner()
    arguments = ["archive", "export", "--scope-id", scope_id, "--output", str(output)]
    blocked = runner.invoke(cli, arguments)
    assert blocked.exit_code == 3, blocked.output
    assert "archive recovery required" in blocked.output
    assert not output.exists()
    recovered = runner.invoke(cli, ["archive", "restore", str(archive), "--yes"])
    assert recovered.exit_code == 0, recovered.output
    exported = runner.invoke(cli, arguments)
    assert exported.exit_code == 0, exported.output


def test_archive_cli_reports_corrupt_or_missing_bundles_without_a_traceback() -> None:
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", "inspect", "missing.pcb"])

    assert result.exit_code == 2
    assert isinstance(result.exception, SystemExit)
    assert "error: archive validation failed: cannot read portable bundle" in result.output
    assert "Traceback" not in result.output


def test_archive_cli_dry_run_exits_nonzero_when_target_is_incompatible(tmp_path, monkeypatch) -> None:
    async def incompatible(_source, /, *, env_file):
        del env_file
        return archive_module.BundleValidation(
            bundle_id="00000000-0000-0000-0000-000000000000",
            scopes=("project:one",),
            record_count=1,
            records_by_type={"scope": 1},
            total_digest="sha256:" + "0" * 64,
            format_version=1,
            producer_version="test",
            already_present=0,
            conflicts=0,
            projections_supported=True,
            compatible=False,
            required_source_types=(),
            unsupported_source_types=(),
            required_artifact_families=("future-family",),
            unsupported_artifact_families=("future-family",),
            reader_version="test",
        )

    monkeypatch.setattr(archive_module, "_validate_only", incompatible)
    result = CliRunner().invoke(
        create_cli([archive_app]),
        ["archive", "restore", str(tmp_path / "bundle.pcb"), "--dry-run"],
    )

    assert result.exit_code == 3
    assert '"compatible": false' in result.output


@pytest.mark.parametrize("uri_kind", ["absolute", "relative", "escaped"])
def test_archive_cli_dry_run_checks_existing_sqlite_uri_target(tmp_path, monkeypatch, uri_kind) -> None:
    monkeypatch.chdir(tmp_path)
    target_path = tmp_path / ("target #1.db" if uri_kind == "escaped" else "target.db")
    archive = tmp_path / "scope.pcb"

    async def authorize(_scopes: tuple[str, ...]) -> None:
        pass

    async def prepare() -> None:
        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(ScopeDraft(title="Original", summary="Archive", idempotency_key="uri"))
            await source.portability.export([scope.scope_id], archive, authorize=authorize)
        async with open_builtin_contexts(
            BuiltinConfig(database=SQLiteConfig(url=f"sqlite+aiosqlite:///{target_path}"))
        ) as target:
            await target.portability.restore(archive)
            async with target.database.transaction() as connection:
                await connection.execute(
                    update(SCOPES_TABLE)
                    .where(SCOPES_TABLE.c.scope_id == scope.scope_id)
                    .values(title="Target differs", title_search="Target differs", version=2)
                )

    asyncio.run(prepare())
    filename = "file:target.db" if uri_kind == "relative" else target_path.as_uri()
    uri_flag = "on" if uri_kind == "relative" else "true"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{filename}?uri={uri_flag}")
    before = target_path.read_bytes()
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", "restore", str(archive), "--dry-run"])

    assert result.exit_code == 3, result.output
    report = json.loads(result.stdout)
    assert not report["compatible"]
    assert report["conflicts"] == 1
    assert target_path.read_bytes() == before


def test_archive_cli_dry_run_does_not_create_missing_sqlite_uri_target(tmp_path, monkeypatch) -> None:
    archive = tmp_path / "scope.pcb"

    async def authorize(_scopes: tuple[str, ...]) -> None:
        pass

    async def prepare() -> None:
        async with open_builtin_contexts(BuiltinConfig()) as source:
            scope = await source.scopes.create(
                ScopeDraft(title="New", summary="Archive", idempotency_key="missing-uri")
            )
            await source.portability.export([scope.scope_id], archive, authorize=authorize)

    asyncio.run(prepare())
    target_path = tmp_path / "missing" / "target.db"
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_KIND", "sqlite")
    monkeypatch.setenv("POWERCONTEXT_SERVER_DATABASE_URL", f"sqlite+aiosqlite:///{target_path.as_uri()}?uri=true")
    result = CliRunner().invoke(create_cli([archive_app]), ["archive", "restore", str(archive), "--dry-run"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["compatible"]
    assert not target_path.parent.exists()
