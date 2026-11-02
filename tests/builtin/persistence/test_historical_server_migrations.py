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

"""Real released SQLite layouts must remain usable through the unified upgrade."""

import json
import shutil
import sqlite3
from contextlib import asynccontextmanager, closing
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, event

from powercontext.builtin.artifacts.memory import EmbeddingProfile
from powercontext.builtin.inference.models import EmbeddingResult
from powercontext.builtin.persistence.migrations import historical
from powercontext.builtin.persistence.migrations.bundle import MigrationBundle
from powercontext.builtin.persistence.migrations.deployment import server_bundle
from powercontext.builtin.persistence.migrations.models import MigrationError, digest
from powercontext.builtin.persistence.migrations.server_readiness import check_server_schema
from powercontext.builtin.persistence.migrations.sqlite import SQLiteMigrationRunner
from powercontext.builtin.persistence.migrations.sqlite_compatibility import layout
from powercontext.builtin.runtime.config import BuiltinConfig
from powercontext.builtin.runtime.processing_registry import canonical_processing_manifest

FIXTURES = Path(__file__).parent / "fixtures/historical"
FACTS = [
    "Upgrade100Alpha user name is Chen Chen.",
    "Upgrade100Bravo pet cat is named Tangyuan.",
    "Upgrade100Charlie favorite dish is West Lake vinegar fish.",
    "Upgrade100Delta occupation is database engineer.",
]
ADDED = [
    "Upgrade130Alpha hobby is skiing.",
    "Upgrade130Bravo parking space is B-130.",
    "Upgrade130Charlie allergy is mango.",
]


def source(tmp_path, name):
    capture = json.loads((FIXTURES / f"{name}.json").read_text())
    database = tmp_path / "historical.db"
    with closing(sqlite3.connect(database)) as connection, connection:
        for kind in ("table", "index", "view", "trigger"):
            for row_kind, object_name, _, sql in capture["schema"]:
                if (
                    row_kind == kind
                    and sql
                    and not connection.execute("SELECT 1 FROM sqlite_schema WHERE name=?", (object_name,)).fetchone()
                ):
                    connection.execute(sql)
        for table, rows in capture["rows"].items():
            for row in rows:
                values = tuple(bytes.fromhex(value["hex"]) if isinstance(value, dict) else value for value in row)
                connection.execute(
                    'INSERT INTO "' + table + '" VALUES (' + ",".join("?" for _ in values) + ")",  # noqa: S608 -- checked-in fixture identifiers
                    values,
                )
    return SQLiteMigrationRunner(
        database,
        server_bundle(),
        source_bundles=(server_bundle(source_release="1.4.0"),),
        configuration={
            "processing_manifest": canonical_processing_manifest(BuiltinConfig()),
            "embedding_enabled": False,
        },
    )


def apply(runner):
    plan = runner.plan()
    return runner.apply(plan_id=plan.plan_id, accepted=True, maintenance_confirmed=True)


def dump(database):
    with closing(sqlite3.connect(database)) as connection:
        return tuple(connection.iterdump())


@pytest.mark.parametrize("name", ["first-release", "memory-release", "evolved-atomic"])
def test_released_and_evolved_databases_upgrade_without_manual_repairs(tmp_path, name):
    runner = source(tmp_path, name)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("CREATE TABLE unrelated_extension (value TEXT)")
        connection.execute("INSERT INTO unrelated_extension VALUES ('keep')")
        connection.execute("CREATE INDEX extension_artifact_family ON pc_artifacts(family)")
        connection.execute("ALTER TABLE pc_artifacts ADD COLUMN extension_note TEXT")
        connection.execute("ALTER TABLE pc_source_journal_heads ADD COLUMN extension_note TEXT DEFAULT 'keep-column'")
        connection.execute("INSERT INTO pc_source_journal_heads(scope_id, position) VALUES ('extension-scope', 0)")
    before = dump(runner.database)
    plan = runner.plan()
    assert plan.compatibility_steps
    assert plan.compatibility_checksum
    assert dump(runner.database) == before
    result = apply(runner)
    assert result.backup_ref and dump(result.backup_ref) == before
    assert runner.verify().state == "ready"
    expected = FACTS + (ADDED if name == "evolved-atomic" else [])
    with closing(sqlite3.connect(runner.database)) as connection:
        assert sorted(row[0] for row in connection.execute("SELECT text FROM pc_atomic_memory_current")) == sorted(
            expected
        )
        assert connection.execute("SELECT value FROM unrelated_extension").fetchall() == [("keep",)]
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='extension_artifact_family'").fetchone()
        assert connection.execute("SELECT DISTINCT extension_note FROM pc_artifacts").fetchall() in ([], [(None,)])
        assert connection.execute(
            "SELECT extension_note FROM pc_source_journal_heads WHERE scope_id='extension-scope'"
        ).fetchone() == ("keep-column",)
        assert connection.execute("SELECT COUNT(*) FROM pc_memory_artifact_archive").fetchone() == (4,)
        for fact in expected:
            assert connection.execute(
                "SELECT count(*) FROM pc_atomic_memory_current_fts WHERE pc_atomic_memory_current_fts MATCH ?",
                (fact.split()[0],),
            ).fetchone() == (1,)
    engine = create_engine(f"sqlite:///{runner.database}")
    try:
        with engine.connect() as connection:
            check_server_schema(connection)
    finally:
        engine.dispose()
    after = dump(runner.database)
    assert not apply(runner).changed
    assert dump(runner.database) == after


def test_memory_extension_values_cannot_be_silently_lost(tmp_path):
    runner = source(tmp_path, "memory-release")
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("ALTER TABLE pc_artifacts ADD COLUMN extension_note TEXT DEFAULT 'preserve-me'")
    before = dump(runner.database)
    with pytest.raises(MigrationError, match="Memory extension data needs a mapping"):
        apply(runner)
    assert dump(runner.database) == before


def test_failed_historical_conversion_rolls_back_and_reuses_backup(tmp_path, monkeypatch):
    runner = source(tmp_path, "memory-release")
    before = dump(runner.database)
    original = historical.apply

    def fail_after_conversion(*args, **kwargs):
        original(*args, **kwargs)
        raise MigrationError("interrupted", "Injected interruption before adoption")

    with monkeypatch.context() as patch:
        patch.setattr(historical, "apply", fail_after_conversion)
        with pytest.raises(MigrationError, match="interrupted"):
            apply(runner)
    assert dump(runner.database) == before
    backup = json.loads(runner.evidence_path.read_text())["backup"]["location"]
    assert apply(runner).backup_ref == backup
    assert runner.verify().state == "ready"


def test_unknown_constraint_cannot_be_discarded_by_historical_adoption(tmp_path):
    runner = source(tmp_path, "first-release")
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("CREATE UNIQUE INDEX custom_artifact_content ON pc_artifacts(content)")
    before = dump(runner.database)
    with pytest.raises(MigrationError, match="data_conversion_required"):
        apply(runner)
    assert dump(runner.database) == before


def test_early_memory_conversion_reembeds_for_atomic_input_and_keeps_vector_search(tmp_path):
    old = source(tmp_path, "first-release")
    profile = EmbeddingProfile(profile_id="historical-test", model="controlled", dimension=3)
    calls = []

    class Embedding:
        profile: EmbeddingProfile

        async def embed(self, texts):
            calls.extend(texts)
            return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))

    model = Embedding()
    model.profile = profile

    @asynccontextmanager
    async def opened():
        yield model

    runner = SQLiteMigrationRunner(
        old.database,
        server_bundle(embedding_profile=profile),
        configuration={
            **old.configuration,
            "embedding_enabled": True,
            "embedding_profile": profile.model_dump(mode="json"),
        },
        embedding_factory=opened,
    )
    assert runner.plan().compatibility_steps
    assert calls == [], "Planning must not call an embedding provider"
    apply(runner)
    assert sorted(calls) == sorted("fact\n" + fact for fact in FACTS)
    assert runner.verify().state == "ready"
    import sqlite_vec

    with closing(sqlite3.connect(runner.database)) as connection:
        connection.enable_load_extension(True)
        sqlite_vec.load(connection)
        connection.enable_load_extension(False)
        assert connection.execute("SELECT count(*) FROM pc_atomic_memory_current_vec").fetchone() == (4,)
    assert not apply(runner).changed
    assert len(calls) == 4


@pytest.mark.parametrize("already_managed", [False, True])
@pytest.mark.parametrize("vector", [False, True])
def test_future_feature_revisions_extend_the_same_upgrade_path(tmp_path, already_managed, vector):
    old = source(tmp_path, "first-release")
    profile = EmbeddingProfile(profile_id="future-test", model="controlled", dimension=3) if vector else None

    class Embedding:
        profile: EmbeddingProfile | None

        async def embed(self, texts):
            return EmbeddingResult(vectors=tuple((1.0, 0.0, 0.0) for _ in texts))

    @asynccontextmanager
    async def embedding():
        model = Embedding()
        model.profile = profile
        yield model

    old = SQLiteMigrationRunner(
        old.database,
        server_bundle(embedding_profile=profile),
        configuration={
            **old.configuration,
            "embedding_enabled": vector,
            "embedding_profile": profile.model_dump() if profile else None,
        },
        embedding_factory=embedding if vector else None,
    )
    if already_managed:
        apply(old)
    root = tmp_path / "future-bundle"
    shutil.copytree(old.bundle.snapshot_directory, root)
    manifest = json.loads((root / "manifest.json").read_text())
    previous = old.bundle.head
    rows = list(layout(server_bundle(), previous))
    for release in ("9_8_0", "9_9_0"):
        revision = f"v{release}_001"
        table = f"pc_future_{release}"
        ddl = f"CREATE TABLE {table} (value INTEGER NOT NULL)"
        (root / "versions" / f"{revision}.py").write_text(
            "from alembic import op\n"  # noqa: S608 -- generated test revision, fixed identifiers
            f"revision = {revision!r}\ndown_revision = {previous!r}\n"
            f"def upgrade():\n    op.get_bind().exec_driver_sql({ddl!r})\n"
            f"    op.get_bind().exec_driver_sql('INSERT INTO {table} SELECT count(*) FROM pc_atomic_memory_current')\n"
        )
        rows.append(["table", table, table, ddl])
        rows.sort(key=lambda row: (row[0], row[1]))
        manifest["sqlite_fingerprints"][revision] = [digest(rows)]
        manifest["revision_resources"][revision] = []
        manifest["domain_readiness"][revision] = manifest["domain_readiness"][previous]
        previous = revision
    (root / "manifest.json").write_text(json.dumps(manifest))
    future = SQLiteMigrationRunner(
        old.database,
        MigrationBundle(root, embedding_profile=profile),
        configuration=old.configuration,
        embedding_factory=embedding if vector else None,
    )
    assert future.plan().target_revision == "v9_9_0_001"
    apply(future)
    assert future.verify().state == "ready"
    with closing(sqlite3.connect(old.database)) as connection:
        assert connection.execute("SELECT value FROM pc_future_9_8_0").fetchall() == [(4,)]
        assert connection.execute("SELECT value FROM pc_future_9_9_0").fetchall() == [(4,)]
    assert not apply(future).changed


@pytest.mark.parametrize("managed", [False, True])
@pytest.mark.parametrize("collation", ["BINARY", "NOCASE"])
def test_primary_key_collation_is_validated_during_upgrade_and_admission(tmp_path, managed, collation):
    runner = source(tmp_path, "evolved-atomic")
    if managed:
        apply(runner)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        ddl = connection.execute("SELECT sql FROM sqlite_schema WHERE name='pc_source_journal_heads'").fetchone()[0]
        rows = connection.execute("SELECT scope_id,position FROM pc_source_journal_heads").fetchall()
        connection.execute("DROP TABLE pc_source_journal_heads")
        connection.execute(ddl.replace("PRIMARY KEY (scope_id)", f"PRIMARY KEY (scope_id COLLATE {collation})"))
        connection.executemany("INSERT INTO pc_source_journal_heads(scope_id,position) VALUES (?,?)", rows)
    before = dump(runner.database)
    if collation == "BINARY":
        apply(runner)
        assert runner.verify().state == "ready"
        with closing(sqlite3.connect(runner.database)) as connection, connection:
            connection.executemany(
                "INSERT INTO pc_source_journal_heads(scope_id,position) VALUES (?,0)",
                [("CaseProbe",), ("caseprobe",)],
            )
    else:
        with pytest.raises(MigrationError, match=r"primary.key"):
            apply(runner)
        assert dump(runner.database) == before
        if managed:
            with pytest.raises(MigrationError, match=r"primary.key"):
                runner.verify()


@pytest.mark.parametrize("storage", ["VIRTUAL", "STORED"])
def test_generated_extension_columns_survive_historical_and_revision_rebuilds(tmp_path, storage):
    runner = source(tmp_path, "evolved-atomic")
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        # SQLite can add a STORED column only while a table is empty.
        heads = connection.execute("SELECT scope_id,position FROM pc_source_journal_heads").fetchall()
        connection.execute("DELETE FROM pc_source_journal_heads")
        connection.execute(
            "ALTER TABLE pc_source_journal_heads ADD COLUMN extension_value TEXT COLLATE NOCASE "
            f"GENERATED ALWAYS AS (scope_id || ':' || position) {storage}"
        )
        connection.executemany("INSERT INTO pc_source_journal_heads(scope_id,position) VALUES (?,?)", heads)
        artifacts = connection.execute("SELECT * FROM pc_artifacts").fetchall()
        connection.execute("DELETE FROM pc_artifacts")
        connection.execute(
            "ALTER TABLE pc_artifacts ADD COLUMN extension_value TEXT COLLATE NOCASE "
            f"GENERATED ALWAYS AS (artifact_id || ':' || revision) {storage}"
        )
        connection.executemany(
            "INSERT INTO pc_artifacts VALUES (" + ",".join("?" for _ in artifacts[0]) + ")",  # noqa: S608 -- fixed table and generated placeholders
            artifacts,
        )
        connection.execute(
            "INSERT INTO pc_artifacts(scope_id,family,artifact_id,revision,content) VALUES (?,'custom','kept',1,?)",
            (heads[0][0], b'{"preserved":true}'),
        )
        connection.execute("CREATE INDEX extension_generated_value ON pc_artifacts(extension_value)")
    before = dump(runner.database)
    result = apply(runner)
    assert result.backup_ref and dump(result.backup_ref) == before
    assert runner.verify().state == "ready"
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        assert connection.execute(
            "SELECT content,extension_value FROM pc_artifacts WHERE artifact_id='kept'"
        ).fetchall() == [(b'{"preserved":true}', "kept:1")]
        assert connection.execute(
            "SELECT scope_id,position,extension_value FROM pc_source_journal_heads ORDER BY scope_id"
        ).fetchall() == sorted((scope, position, f"{scope}:{position}") for scope, position in heads)
        assert connection.execute("SELECT 1 FROM sqlite_schema WHERE name='extension_generated_value'").fetchone()
        assert connection.execute(
            "SELECT count(*) FROM pc_artifacts INDEXED BY extension_generated_value WHERE extension_value='KEPT:1'"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT count(*) FROM pc_source_journal_heads WHERE extension_value != upper(extension_value)"
        ).fetchone() == (0,)
        connection.execute("UPDATE pc_source_journal_heads SET position=position+1")
        assert connection.execute(
            "SELECT scope_id,position,extension_value FROM pc_source_journal_heads ORDER BY scope_id"
        ).fetchall() == sorted((scope, position + 1, f"{scope}:{position + 1}") for scope, position in heads)
        for table in ("pc_source_journal_heads", "pc_artifacts"):
            columns = connection.execute(f"PRAGMA table_xinfo({table})").fetchall()
            assert next(column[6] for column in columns if column[1] == "extension_value") == (
                3 if storage == "STORED" else 2
            )
    after = dump(runner.database)
    assert not apply(runner).changed
    assert dump(runner.database) == after


def test_generated_column_copy_includes_columns_added_by_the_same_revision(tmp_path):
    runner = source(tmp_path, "evolved-atomic")
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute(
            "ALTER TABLE pc_artifact_processing_intents ADD COLUMN extension_value TEXT "
            "GENERATED ALWAYS AS ('task:' || scope_id) VIRTUAL"
        )
        before = connection.execute(
            "SELECT scope_id,extension_value FROM pc_artifact_processing_intents ORDER BY scope_id"
        ).fetchall()
        assert before
    apply(runner)
    assert runner.verify().state == "ready"
    with closing(sqlite3.connect(runner.database)) as connection:
        assert connection.execute(
            "SELECT scope_id,extension_value,consecutive_dream_attempts "
            "FROM pc_artifact_processing_intents ORDER BY scope_id"
        ).fetchall() == [(scope, value, 0) for scope, value in before]
    assert not apply(runner).changed


@pytest.mark.parametrize(
    ("definition", "matches"),
    [
        ("TEXT COLLATE NOCASE DEFAULT 'MiXeD'", 1),
        ("TEXT COLLATE NOCASE GENERATED ALWAYS AS ('MiXeD') VIRTUAL", 1),
        ("TEXT GENERATED ALWAYS AS ('MiXeD' COLLATE NOCASE) VIRTUAL", 0),
    ],
)
def test_extension_column_collation_survives_rebuild(tmp_path, definition, matches):
    runner = source(tmp_path, "evolved-atomic")
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("ALTER TABLE pc_source_journal_heads ADD COLUMN extension_value " + definition)
        before = connection.execute("SELECT extension_value = 'mixed' FROM pc_source_journal_heads").fetchall()
        assert before and all(row == (matches,) for row in before)
    apply(runner)
    assert runner.verify().state == "ready"
    with closing(sqlite3.connect(runner.database)) as connection:
        assert connection.execute("SELECT extension_value = 'mixed' FROM pc_source_journal_heads").fetchall() == before
    assert not apply(runner).changed


@pytest.mark.parametrize("unmapped", [None, "extension", "merge"])
def test_memory_lineage_is_archived_without_accepting_unmapped_data(tmp_path, unmapped):
    runner = source(tmp_path, "memory-release")
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        scope, family, artifact, revision, upstream = connection.execute(
            "SELECT a.scope_id,a.family,a.artifact_id,a.revision,b.revision FROM pc_artifacts a "
            "JOIN pc_artifacts b ON a.scope_id=b.scope_id AND a.family=b.family AND a.artifact_id=b.artifact_id "
            "AND a.revision>b.revision WHERE a.family='memory' ORDER BY a.revision,b.revision LIMIT 1"
        ).fetchone()
        connection.execute(
            "INSERT INTO pc_sources(scope_id,source_type,source_id,payload,journal_position) VALUES (?, 'message', 'evidence', ?, 1)",
            (scope, b'{"text":"Preserved source evidence."}'),
        )
        connection.execute("UPDATE pc_source_journal_heads SET position=1 WHERE scope_id=?", (scope,))
        connection.execute(
            "INSERT INTO pc_artifact_lineage_sources(scope_id,family,artifact_id,revision,ordinal,source_type,source_id) "
            "VALUES (?,?,?,?,0,'message','evidence')",
            (scope, family, artifact, revision),
        )
        connection.execute(
            "ALTER TABLE pc_artifact_lineage_artifacts ADD COLUMN is_merge_input BOOLEAN DEFAULT '0' NOT NULL"
        )
        connection.execute(
            "INSERT INTO pc_artifact_lineage_artifacts(scope_id,family,artifact_id,revision,ordinal,"
            "upstream_family,upstream_artifact_id,upstream_revision) VALUES (?,?,?,?,0,?,?,?)",
            (scope, family, artifact, revision, family, artifact, upstream),
        )
        if unmapped == "extension":
            connection.execute("ALTER TABLE pc_artifact_lineage_sources ADD COLUMN extension_note TEXT DEFAULT 'keep'")
        elif unmapped == "merge":
            connection.execute("UPDATE pc_artifact_lineage_artifacts SET is_merge_input=1")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    before = dump(runner.database)
    if unmapped:
        with pytest.raises(MigrationError, match="needs a mapping"):
            apply(runner)
        assert dump(runner.database) == before
        return
    apply(runner)
    assert runner.verify().state == "ready"
    with closing(sqlite3.connect(runner.database)) as connection:
        metadata = json.loads(
            connection.execute(
                "SELECT metadata FROM pc_memory_artifact_archive WHERE scope_id=? AND artifact_id=? AND revision=?",
                (scope, artifact, revision),
            ).fetchone()[0]
        )
        assert metadata["lineage"] == {
            "sources": [{"ordinal": 0, "source_type": "message", "source_id": "evidence"}],
            "artifacts": [
                {
                    "ordinal": 0,
                    "upstream_family": family,
                    "upstream_artifact_id": artifact,
                    "upstream_revision": upstream,
                }
            ],
        }
        assert connection.execute("SELECT count(*) FROM pc_atomic_memory_current").fetchone() == (4,)
        assert connection.execute("SELECT payload FROM pc_sources WHERE source_id='evidence'").fetchone() == (
            b'{"text":"Preserved source evidence."}',
        )
    assert not apply(runner).changed


@pytest.mark.parametrize(
    ("name", "interruption"),
    [
        ("first-release", None),
        ("evolved-atomic", None),
        ("evolved-atomic", "pc_historical_pc_artifact_lineage_sources"),
        ("evolved-atomic", "pc_full_upgrade_pc_artifacts"),
    ],
)
def test_dependent_views_survive_rebuild_and_retry(tmp_path, name, interruption):
    runner = source(tmp_path, name)
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        connection.execute("ALTER TABLE pc_artifact_lineage_sources ADD COLUMN extension_note TEXT")
        connection.execute("CREATE VIEW custom_artifacts AS SELECT scope_id,artifact_id FROM pc_artifacts")
        connection.execute("CREATE VIEW custom_lineage AS SELECT scope_id,artifact_id FROM pc_artifact_lineage_sources")
        connection.execute("CREATE VIEW custom_counts AS SELECT count(*) AS total FROM custom_artifacts")
        connection.execute("CREATE VIEW custom_candidates AS SELECT candidate_id FROM pc_artifact_candidate_heads")
        connection.execute("CREATE TABLE extension_audit (total INTEGER)")
        connection.execute(
            "CREATE TRIGGER custom_count_write INSTEAD OF UPDATE ON custom_counts "
            "BEGIN INSERT INTO extension_audit VALUES (NEW.total); END"
        )
        definitions = connection.execute(
            "SELECT name,sql FROM sqlite_schema WHERE name LIKE 'custom_%' AND name!='custom_candidates' ORDER BY name"
        ).fetchall()
    before = dump(runner.database)
    runner.plan()
    assert dump(runner.database) == before
    if interruption:

        def interrupt(connection, _cursor, statement, *_args):
            if statement.startswith("ALTER TABLE") and interruption in statement and "RENAME TO" in statement:
                # Reference layouts also execute frozen DDL; interrupt only the real database.
                files = connection.exec_driver_sql("PRAGMA database_list").all()
                if any(Path(filename) == runner.database for _, _, filename in files if filename):
                    raise MigrationError("interrupted", "Injected after table replacement")

        event.listen(Engine, "after_cursor_execute", interrupt)
        try:
            with pytest.raises(MigrationError, match="interrupted"):
                apply(runner)
        finally:
            event.remove(Engine, "after_cursor_execute", interrupt)
        if interruption.startswith("pc_historical_"):
            assert dump(runner.database) == before
        backup = json.loads(runner.evidence_path.read_text())["backup"]["location"]
        assert apply(runner).backup_ref == backup
    else:
        apply(runner)
    assert runner.verify().state == "ready"
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        assert (
            connection.execute(
                "SELECT name,sql FROM sqlite_schema WHERE name LIKE 'custom_%' AND name!='custom_candidates' ORDER BY name"
            ).fetchall()
            == definitions
        )
        assert (
            connection.execute("SELECT * FROM custom_artifacts").fetchall()
            == connection.execute("SELECT scope_id,artifact_id FROM pc_artifacts").fetchall()
        )
        assert (
            connection.execute("SELECT * FROM custom_lineage").fetchall()
            == connection.execute("SELECT scope_id,artifact_id FROM pc_artifact_lineage_sources").fetchall()
        )
        assert (
            connection.execute("SELECT * FROM custom_candidates").fetchall()
            == connection.execute("SELECT candidate_id FROM pc_candidate_heads").fetchall()
        )
        count = connection.execute("SELECT total FROM custom_counts").fetchone()[0]
        connection.execute("UPDATE custom_counts SET total=total")
        assert connection.execute("SELECT total FROM extension_audit").fetchall() == [(count,)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert not apply(runner).changed
