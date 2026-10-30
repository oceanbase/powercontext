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

"""Nonempty released storage upgrades before description metadata exists."""

import json
import sqlite3
import struct
from contextlib import closing
from hashlib import sha256

import pytest
import sqlite_vec

from powercontext.builtin.artifacts.atomic_memory.models import AtomicMemoryContent
from powercontext.builtin.artifacts.memory.canonical import canonical_json
from powercontext.builtin.artifacts.search import analyze_text
from powercontext.builtin.persistence.atomic_memory_index import (
    atomic_memory_embedding_input,
    atomic_memory_embedding_input_hash,
    atomic_memory_profile_fingerprint,
)
from powercontext.builtin.persistence.migrations.models import MigrationError
from tests.builtin.persistence.test_release140_server_migrations import _apply, _source


@pytest.mark.parametrize("release", ["1.3.0", "1.4.0"])
@pytest.mark.parametrize("vectors", [False, True])
def test_nonempty_released_memory_upgrades_without_rewriting_history(tmp_path, release, vectors):
    _, profile, runner = _source(tmp_path, release=release, vectors=vectors)
    expected = {}
    with closing(sqlite3.connect(runner.database)) as connection, connection:
        if vectors:
            connection.enable_load_extension(True)
            try:
                sqlite_vec.load(connection)
            finally:
                connection.enable_load_extension(False)
        assert "description" not in {
            row[1] for row in connection.execute("PRAGMA table_info(pc_atomic_memory_current)")
        }
        for number in range(2):
            identifier = f"historical-{number}"
            content = AtomicMemoryContent(kind="fact", text=f"Preserve historical nebula fact {number}.")
            payload = content.model_dump_json(by_alias=True).encode()
            assert "description" not in json.loads(payload)
            expected[identifier] = payload
            connection.execute(
                "INSERT INTO pc_artifacts (scope_id,family,artifact_id,revision,content) "
                "VALUES ('scope','atomic-memory',?,1,?)",
                (identifier, payload),
            )
            connection.execute(
                "INSERT INTO pc_artifact_heads (scope_id,family,artifact_id,revision) "
                "VALUES ('scope','atomic-memory',?,1)",
                (identifier,),
            )
            connection.execute(
                "INSERT INTO pc_atomic_memory_current "
                "(scope_id,artifact_id,revision,state_version,content_hash,projection_format,kind,text,"
                "searchable_text,tag_keys,embedding,profile_fingerprint,embedding_input_hash) "
                "VALUES ('scope',?,1,0,?,'powercontext.atomic-memory.current.v1',?,?,?,?,?,?,?)",
                (
                    identifier,
                    sha256(canonical_json(json.loads(payload))).hexdigest(),
                    content.kind,
                    content.text,
                    analyze_text(atomic_memory_embedding_input(content.kind, content.text)),
                    "[]",
                    None if profile is None else struct.pack("<3f", 1.0, 0.0, 0.0),
                    None if profile is None else atomic_memory_profile_fingerprint(profile),
                    None if profile is None else atomic_memory_embedding_input_hash(content.kind, content.text),
                ),
            )

    result = _apply(runner)
    assert result.state == "ready" and result.readiness_scope == "server" and result.revision == "v1_4_1_006"
    verified = runner.verify()
    assert verified.state == "ready" and verified.readiness_scope == "server"
    assert not _apply(runner).changed
    with closing(sqlite3.connect(runner.database)) as connection:
        assert (
            dict(connection.execute("SELECT artifact_id,content FROM pc_artifacts WHERE family='atomic-memory'"))
            == expected
        )
        assert connection.execute(
            "SELECT artifact_id,revision,description FROM pc_atomic_memory_current ORDER BY artifact_id"
        ).fetchall() == [(identifier, 1, None) for identifier in sorted(expected)]
        connection.execute("UPDATE pc_atomic_memory_current SET description='Invented summary'")
        connection.commit()
    before = runner.database.read_bytes()
    with pytest.raises(MigrationError, match="projection_verification_failed"):
        runner.verify()
    assert runner.database.read_bytes() == before
