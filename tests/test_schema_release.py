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

"""Cumulative migrations execute unchanged after freezing their release identity."""

import json
import shutil
import sqlite3
import zipfile
from contextlib import closing
from pathlib import Path

import pytest
import schema_release as schema
from sqlalchemy import create_engine

from powercontext.builtin.persistence.migrations import MigrationBundle, MigrationError, SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.bundle import sqlite_inventory
from powercontext.builtin.persistence.migrations.models import digest
from powercontext.builtin.persistence.migrations.mysql_schema import MySQLSchema, _parse_table
from powercontext.builtin.persistence.migrations.revision_names import describe_revision


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def fingerprint(statements):
    engine = create_engine("sqlite://")
    try:
        with engine.begin() as connection:
            for statement in statements:
                connection.exec_driver_sql(statement)
            return digest(sqlite_inventory(connection))
    finally:
        engine.dispose()


def set_version(root, version):
    write_json(
        root / "release/upgrade-support.json",
        {
            "target_version": version,
            "schema_review": {"persistence_sha256": "pending", "rationale": "Fixture compatibility review"},
        },
    )
    contract = root / "openapi/powercontext.yaml"
    contract.parent.mkdir(exist_ok=True)
    contract.write_text(f"info:\n  version: {version}\n")


@pytest.fixture
def draft(tmp_path):
    root = tmp_path / "repo"
    bundle = root / schema.SERVER
    (bundle / "versions").mkdir(parents=True)
    (bundle / "mysql/versions").mkdir(parents=True)
    env = Path(__file__).parent / "fixtures/database_migrations/env.py"
    shutil.copyfile(env, bundle / "env.py")
    shutil.copyfile(env, bundle / "mysql/env.py")
    baseline = "CREATE TABLE pc_items (id INTEGER PRIMARY KEY, label TEXT)"
    changes = [
        "ALTER TABLE pc_items ADD COLUMN note TEXT DEFAULT '000'",
        "ALTER TABLE pc_items ADD COLUMN enabled INTEGER DEFAULT 1",
    ]
    write_json(bundle / "schemas/sqlite-000.json", {"statements": changes, "description": "累计变更", "default": "000"})
    (bundle / "versions/v1_5_2.py").write_text(
        "from alembic import op\nrevision = 'v1_5_2'\ndown_revision = None\n"
        f"def upgrade():\n    op.get_bind().exec_driver_sql({baseline!r})\n"
    )
    (bundle / "versions/000.py").write_text(
        "import json\nfrom pathlib import Path\nfrom alembic import op\n"
        "revision = '000'\ndown_revision = 'v1_5_2'\nDEFAULT = '000'\n"
        "def upgrade():\n    data = Path(__file__).parents[1] / 'schemas/sqlite-000.json'\n"
        "    for statement in json.loads(data.read_text())['statements']:\n"
        "        op.get_bind().exec_driver_sql(statement)\n"
    )
    for revision, previous in [("v1_5_2", None), ("000", "v1_5_2")]:
        (bundle / f"mysql/versions/{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = {previous!r}\ndef upgrade():\n    pass\n"
        )
    write_json(
        bundle / "manifest.json",
        {
            "revision_resources": {"v1_5_2": [], "000": ["schemas/sqlite-000.json"]},
            "sqlite_baselines": {fingerprint([baseline]): "v1_5_2"},
            "sqlite_fingerprints": {"v1_5_2": [fingerprint([baseline])], "000": [fingerprint([baseline, *changes])]},
            "affected_objects": ["pc_items"],
        },
    )
    set_version(root, "1.5.3")
    return root, baseline


def migrate(root, path, baseline):
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(baseline)
        connection.execute("INSERT INTO pc_items VALUES (1, '保留历史')")
        connection.commit()
    runner = SQLiteMigrationRunner(path, MigrationBundle(root / schema.SERVER))
    plan = runner.plan()
    result = runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)
    assert runner.verify().state == "ready"
    assert not runner.apply().changed
    with closing(sqlite3.connect(path)) as connection:
        rows = connection.execute("SELECT * FROM pc_items").fetchall()
    return result.revision, rows


def test_cumulative_sql_preserves_data_before_and_after_freeze(draft, tmp_path):
    root, baseline = draft
    before = schema.bundles(root)
    assert migrate(root, tmp_path / "draft.db", baseline) == ("000", [(1, "保留历史", "000", 1)])
    sql = (root / schema.SERVER / "schemas/sqlite-000.json").read_bytes()
    assert schema.freeze(root, "1.5.3") == "v1_5_3"
    after = schema.check(root, release=True)
    schema.preserve_history(before, after)
    assert (root / schema.SERVER / "schemas/sqlite-v1_5_3.json").read_bytes() == sql
    assert not (root / schema.SERVER / "versions/000.py").exists()
    assert "DEFAULT = '000'" in (root / schema.SERVER / "versions/v1_5_3.py").read_text()
    assert migrate(root, tmp_path / "release.db", baseline) == ("v1_5_3", [(1, "保留历史", "000", 1)])
    released = describe_revision("v1_5_3")
    pending = describe_revision("000")
    assert released is not None and pending is not None
    assert released["software_version"] == "1.5.3"
    assert released["sequence"] is None
    assert pending["software_version"] is None
    assert schema.freeze(root, "1.5.3") is None


@pytest.mark.parametrize("declaration", ["revision", "revision: str"])
def test_release_rejects_mutable_draft_in_source_and_wheel(draft, tmp_path, declaration):
    root, _ = draft
    with pytest.raises(ValueError, match="mutable 000"):
        schema.check(root, release=True)
    wheel = tmp_path / "release.whl"
    name = "powercontext/builtin/persistence/migrations/server_resources/versions/shared.py"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(name, f"{declaration} = '000'\n")
    with pytest.raises(ValueError, match="mutable 000"):
        schema.check_release_wheel(wheel)
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(name, f"{declaration} = 'v1_5_3'\n")
    schema.check_release_wheel(wheel)


def test_freeze_rejects_wrong_version_without_writing(draft):
    root, _ = draft
    path = root / schema.SERVER / "versions/000.py"
    original = path.read_bytes()
    with pytest.raises(ValueError, match="both release declarations"):
        schema.freeze(root, "1.5.4")
    assert path.read_bytes() == original


def test_historical_revision_cannot_be_rewritten(draft):
    root, _ = draft
    before = schema.bundles(root)
    path = root / schema.SERVER / "versions/v1_5_2.py"
    path.write_text(path.read_text() + "# forbidden historical edit\n")
    with pytest.raises(ValueError, match="Frozen migration resources changed"):
        schema.preserve_history(before, schema.bundles(root))


def test_freeze_cannot_overwrite_an_existing_resource(draft):
    root, _ = draft
    destination = root / schema.SERVER / "schemas/sqlite-v1_5_3.json"
    destination.write_text("{}")
    with pytest.raises(ValueError, match="destination already exists"):
        schema.freeze(root, "1.5.3")
    assert destination.read_text() == "{}"
    assert (root / schema.SERVER / "versions/000.py").exists()


def test_native_chain_must_match_sqlite(draft):
    root, _ = draft
    path = root / schema.SERVER / "mysql/versions/000.py"
    path.write_text("revision = 'other'\ndown_revision = 'v1_5_2'\n")
    with pytest.raises(ValueError, match="chains differ"):
        schema.check(root)


def test_next_cycle_reuses_000_without_modifying_frozen_release(draft):
    root, _ = draft
    schema.freeze(root, "1.5.3")
    before = schema.bundles(root)
    bundle = root / schema.SERVER
    previous = (bundle / "versions/v1_5_3.py").read_bytes()
    for directory in ["versions", "mysql/versions"]:
        (bundle / directory / "000.py").write_text(
            "revision = '000'\ndown_revision = 'v1_5_3'\ndef upgrade():\n    pass\n"
        )
    manifest = json.loads((bundle / "manifest.json").read_text())
    manifest["revision_resources"]["000"] = []
    manifest["sqlite_fingerprints"]["000"] = manifest["sqlite_fingerprints"]["v1_5_3"]
    write_json(bundle / "manifest.json", manifest)
    set_version(root, "1.5.4")
    schema.preserve_history(before, schema.bundles(root))
    assert schema.freeze(root, "1.5.4") == "v1_5_4"
    assert (bundle / "versions/v1_5_3.py").read_bytes() == previous
    assert schema.check(root, release=True)["server"].head == "v1_5_4"


def test_freeze_updates_registered_source_overlay(draft):
    root, _ = draft
    source = root / schema.SOURCES / "1.5.2"
    source.mkdir(parents=True)
    shutil.copyfile(root / schema.SERVER / "manifest.json", source / "manifest.json")
    write_json(source / "registration.json", {"remove_resources": []})
    assert schema.freeze(root, "1.5.3") == "v1_5_3"
    assert {bundle.head for bundle in schema.check(root, release=True).values()} == {"v1_5_3"}
    manifest = json.loads((source / "manifest.json").read_text())
    assert manifest["revision_resources"]["v1_5_3"] == ["schemas/sqlite-v1_5_3.json"]


def test_native_frozen_script_cannot_be_rewritten(draft):
    root, _ = draft
    before = schema.bundles(root)
    path = root / schema.SERVER / "mysql/versions/v1_5_2.py"
    path.write_text(path.read_text() + "# historical native mutation\n")
    with pytest.raises(ValueError, match="Frozen migration resources changed"):
        schema.preserve_history(before, schema.bundles(root))


@pytest.mark.parametrize("source", ["empty", "1.5.1", "1.5.2"])
@pytest.mark.parametrize("stale_sql", [True, False], ids=["stale-A-plus-B", "correct-B-only"])
def test_delayed_pr_replays_released_sql(source, stale_sql, draft, tmp_path):
    root, baseline = draft
    resources = root / schema.SERVER
    # Model A's draft before the 1.5.2 release, and B's copy of that draft.
    for path in resources.rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json"}:
            path.write_text(path.read_text().replace("v1_5_2", "v1_5_1"))
            if path.name == "v1_5_2.py":
                path.rename(path.with_name("v1_5_1.py"))
    manifest_path = resources / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["readiness_scope"] = "server"
    write_json(manifest_path, manifest)
    old_draft = (resources / "versions/000.py").read_text()
    a_sql = json.loads((resources / "schemas/sqlite-000.json").read_text())["statements"]
    set_version(root, "1.5.2")
    assert schema.freeze(root, "1.5.2") == "v1_5_2"
    released = schema.bundles(root)
    database = tmp_path / "delayed-pr.db"
    if source == "1.5.2":
        migrate(root, database, baseline)
    elif source == "1.5.1":
        with closing(sqlite3.connect(database)) as connection:
            connection.execute(baseline)
            connection.execute("INSERT INTO pc_items VALUES (1, '保留历史')")
            connection.commit()

    # B merges after the release: its dependency is correct, but it may still
    # contain A's already-published non-idempotent ADD COLUMN statements.
    b_sql = "ALTER TABLE pc_items ADD COLUMN b_value TEXT"
    (resources / "versions/000.py").write_text(old_draft.replace("v1_5_1", "v1_5_2"))
    (resources / "mysql/versions/000.py").write_text(
        "revision = '000'\ndown_revision = 'v1_5_2'\ndef upgrade():\n    pass\n"
    )
    write_json(resources / "schemas/sqlite-000.json", {"statements": [*a_sql, b_sql] if stale_sql else [b_sql]})
    manifest = json.loads(manifest_path.read_text())
    manifest["revision_resources"]["000"] = ["schemas/sqlite-000.json"]
    # The intended final fingerprint is correct in both cases. Structural
    # registration alone cannot establish that the SQL reaches this state.
    manifest["sqlite_fingerprints"]["000"] = [fingerprint([baseline, *a_sql, b_sql])]
    write_json(manifest_path, manifest)
    set_version(root, "1.6.0")
    schema.preserve_history(released, schema.check(root))
    runner = SQLiteMigrationRunner(database, MigrationBundle(resources))

    def upgrade():
        plan = runner.plan(backup_policy="skip")
        return runner.apply(
            plan_id=plan.plan_id,
            backup_policy="skip",
            accepted=True,
            maintenance_confirmed=True,
            accept_no_backup=True,
        )

    if stale_sql:
        with pytest.raises(MigrationError) as failure:
            upgrade()
        assert failure.value.code == "invalid_database"
        assert "duplicate column name: note" in str(failure.value.__cause__)
        print(f"{source}: structure check PASSED; SQL replay FAILED: duplicate column name: note")
        with closing(sqlite3.connect(database)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(pc_items)")]
            assert "b_value" not in columns
            if source != "empty":
                assert connection.execute("SELECT label FROM pc_items").fetchall() == [("保留历史",)]
    else:
        assert upgrade().revision == "000"
        assert runner.verify().state == "ready"
        assert not runner.apply().changed
        with closing(sqlite3.connect(database)) as connection:
            columns = [row[1] for row in connection.execute("PRAGMA table_info(pc_items)")]
            assert {"note", "enabled", "b_value"} <= set(columns)
            if source != "empty":
                assert connection.execute("SELECT label FROM pc_items").fetchall() == [("保留历史",)]
        print(f"{source}: structure check PASSED; corrected B-only SQL replay PASSED")


def test_freeze_preserves_historical_f_strings(draft):
    root, _ = draft
    path = root / schema.SERVER / "versions/v1_5_2.py"
    content = path.read_text() + '\ndef message(name):\n    return f"existing {name}"\n'
    path.write_text(content)
    assert schema.freeze(root, "1.5.3") == "v1_5_3"
    assert path.read_text() == content


@pytest.mark.parametrize("source", ["server", "1.4.0"])
def test_vector_profile_history_cannot_be_rewritten(tmp_path, source):
    for directory in (schema.SERVER, schema.SOURCES):
        shutil.copytree(schema.ROOT / directory, tmp_path / directory)
    before = schema.bundles(tmp_path)
    bundle = before[source]
    directory = bundle.source_directory or bundle.directory
    path = directory / "profiles/vector.json"
    profile = json.loads((bundle.snapshot_directory / "profiles/vector.json").read_text())
    profile["source_capture_sha256"]["review_probe"] = "changed provenance"
    write_json(path, profile)
    with pytest.raises(ValueError, match="Frozen migration resources changed"):
        schema.preserve_history(before, schema.bundles(tmp_path))


@pytest.mark.parametrize("source", ["server", "1.4.0"])
def test_native_environment_history_cannot_be_rewritten(tmp_path, source):
    for directory in (schema.SERVER, schema.SOURCES):
        shutil.copytree(schema.ROOT / directory, tmp_path / directory)
    before = schema.bundles(tmp_path)
    bundle = before[source]
    directory = bundle.source_directory or bundle.directory
    path = directory / "mysql/env.py"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((bundle.snapshot_directory / "mysql/env.py").read_text() + "\n# changed native environment\n")
    with pytest.raises(ValueError, match="Frozen migration resources changed"):
        schema.preserve_history(before, schema.bundles(tmp_path))


@pytest.mark.parametrize("missing_module", [False, True])
def test_freeze_preserves_conditional_native_operation(draft, missing_module):
    root, baseline = draft
    directory = root / schema.SERVER
    module = directory / "domain_000.py"
    module.write_text(
        "def execute(connection, operation, configuration):\n"
        '    connection.exec_driver_sql("INSERT INTO pc_items VALUES (7, ?)", (operation,))\n'
    )
    table, inventory = _parse_table(baseline)
    write_json(
        directory / "schemas/mysql-000.json",
        {
            "format": "powercontext.mysql-state-layouts.v1",
            "revisions": {
                "v1_5_2": {"statements": [baseline], "changes": [{table: inventory}]},
                "000": {"statements": ["@domain_000.py:000"], "changes": [{}]},
            },
        },
    )
    path = directory / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["mysql_state_layouts"] = ["schemas/mysql-000.json"]
    manifest["revision_resources"]["000"].append("schemas/mysql-000.json")
    write_json(path, manifest)
    if missing_module:
        module.unlink()
        with pytest.raises(ValueError, match="Invalid conditional MySQL operation"):
            schema.freeze(root, "1.5.3")
        assert (directory / "versions/000.py").exists()
        assert not (directory / "versions/v1_5_3.py").exists()
        return

    for revision in ("000", "v1_5_3"):
        if revision != "000":
            assert schema.freeze(root, "1.5.3") == revision
            assert not module.exists()
        bundle = schema.check(root)["server"]
        native = MySQLSchema(bundle)
        layout = json.loads((directory / f"schemas/mysql-{revision}.json").read_text())
        statement = layout["revisions"][revision]["statements"][0]
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                connection.exec_driver_sql(baseline)
                native.execute(connection, statement, {}, revision=revision)
                assert connection.exec_driver_sql("SELECT * FROM pc_items").all() == [(7, "@000")]
        finally:
            engine.dispose()


@pytest.mark.parametrize("source", ["server", "1.4.0"])
@pytest.mark.parametrize("change", ["schema", "remove-registration", "redirect-registration"])
def test_frozen_code_profile_and_registration_cannot_change(tmp_path, source, change):
    for directory in (schema.SERVER, schema.SOURCES):
        shutil.copytree(schema.ROOT / directory, tmp_path / directory)
    before = schema.bundles(tmp_path)
    bundle = before[source]
    directory = bundle.source_directory or bundle.directory
    manifest = json.loads((bundle.snapshot_directory / "manifest.json").read_text())
    resource = manifest["mysql_schema_extensions"]["seekdb_code"]
    original = (bundle.snapshot_directory / resource).read_bytes()
    if change == "schema":
        profile = json.loads(original)
        profile["statements"] = [sql.replace("`payload_bytes`", "`payload_size`") for sql in profile["statements"]]
        profile["tables"] = dict(_parse_table(sql) for sql in profile["statements"])
        write_json(directory / resource, profile)
    elif change == "remove-registration":
        del manifest["mysql_schema_extensions"]["seekdb_code"]
        write_json(directory / "manifest.json", manifest)
    else:
        replacement = "profiles/seekdb-code-replacement.json"
        path = directory / replacement
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(original)
        manifest["mysql_schema_extensions"]["seekdb_code"] = replacement
        write_json(directory / "manifest.json", manifest)
    after = schema.bundles(tmp_path)
    if change == "schema":
        old_schema = MySQLSchema(bundle, embedded_code=True)
        new_schema = MySQLSchema(after[source], embedded_code=True)
        with pytest.raises(MigrationError, match="incompatible_schema"):
            new_schema.verify(old_schema.required(bundle.head), bundle.head)
    with pytest.raises(ValueError, match="Frozen migration resources changed"):
        schema.preserve_history(before, after)
