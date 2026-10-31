# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Publication records: the commit that outlives the directory which named it."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from powercontext.builtin.code import CodeConfig, CodeRepositoryConfig, CodeService
from powercontext.builtin.code.cache import GenerationCache
from powercontext.builtin.code.store import SQLiteGraphStore

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


class RecordingStore(SQLiteGraphStore):
    """A store that remembers which identifiers were committed and which were recalled.

    Both lists matter: recalling a name only reclaims the rows if they were
    committed under that same name. A test that checks `reclaimed` alone passes
    even when the store ignores the name it was given.
    """

    def __init__(self) -> None:
        self.created: list[str | None] = []
        self.reclaimed: list[str] = []

    def create(
        self,
        directory: Path,
        nodes: list[dict[str, object]],
        edges: list[dict[str, object]],
        deadline: float,
        *,
        generation_id: str | None = None,
    ) -> dict[str, object]:
        self.created.append(generation_id)
        return super().create(directory, nodes, edges, deadline, generation_id=generation_id)

    def remove_generation(self, generation_id: str, binding: str, deadline: float) -> None:
        self.reclaimed.append(generation_id)


class NamedStore(RecordingStore):
    """Behaves like the SeekDB backend where it matters: the manifest carries a name.

    SQLite rows live inside the generation directory, so losing the directory loses
    the rows and there is nothing to protect. The invariant this file is about only
    has consequences when the rows outlive the directory, which is the shape here.
    """

    def create(
        self,
        directory: Path,
        nodes: list[dict[str, object]],
        edges: list[dict[str, object]],
        deadline: float,
        *,
        generation_id: str | None = None,
    ) -> dict[str, object]:
        reference = super().create(directory, nodes, edges, deadline, generation_id=generation_id)
        return {
            "graph": {
                "backend": "named",
                "binding": directory.parent.name,
                "id": generation_id,
                **{key: value for key, value in reference.items() if key == "database_sha256"},
            }
        }


def git(root: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)


def cache_of(service: CodeService) -> GenerationCache:
    directories = [path for path in (service.config.cache_dir).iterdir() if path.is_dir()]
    assert len(directories) == 1
    return GenerationCache(directories[0], service.store)


@pytest.fixture
def repository(tmp_path, request):
    root = tmp_path / "repository"
    (root / "src").mkdir(parents=True)
    (root / "src/app.py").write_text("def run():\n    return 1\n")
    git(root, "init")
    git(root, "add", ".")
    git(root, "-c", "user.name=Code Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    store = getattr(request, "param", RecordingStore)()
    service = CodeService(
        CodeConfig(
            enabled=True,
            repositories={"scope": CodeRepositoryConfig(root=root)},
            cache_dir=tmp_path / "cache",
        ),
        store=store,
    )
    service.index("scope", full=True)
    return service, root


def test_a_published_generation_is_recorded_under_its_final_directory(repository):
    service, _ = repository
    cache = cache_of(service)

    records = cache.publications()
    pointer = cache.pointer()
    assert pointer is not None
    assert len(records) == 1
    _, record = records.popitem()
    assert record["state"] == "published"
    assert record["directory"] == pointer["current"]["directory"]


def test_reindexing_keeps_a_live_generation_out_of_the_sweep(repository):
    service, root = repository
    (root / "src/app.py").write_text("def run():\n    return 2\n")
    git(root, "add", ".")

    service.index("scope")

    assert service.store.reclaimed == []
    assert [record["state"] for record in cache_of(service).publications().values()] == ["published", "published"]


def test_a_generation_directory_removed_out_of_band_is_reclaimed_by_name(repository):
    service, root = repository
    cache = cache_of(service)
    attempt = next(iter(cache.publications()))
    pointer = cache.pointer()
    assert pointer is not None
    shutil.rmtree(cache.directory / pointer["current"]["directory"])
    (root / "src/app.py").write_text("def run():\n    return 3\n")
    git(root, "add", ".")

    service.index("scope")

    assert service.store.reclaimed == [attempt]
    assert attempt not in cache.publications()


def test_a_pending_attempt_without_its_staging_directory_is_reclaimed(repository):
    service, _ = repository
    cache = cache_of(service)
    attempt = "0" * 32
    journal = json.loads((cache.directory / "publication.json").read_text())
    journal["attempts"][attempt] = {"directory": "staging-nowhere", "state": "pending", "started_at": 0}
    (cache.directory / "publication.json").write_text(json.dumps(journal))

    reclaimed = cache.recover(time.monotonic() + 30)

    assert reclaimed == 1
    assert service.store.reclaimed == [attempt]
    assert attempt not in cache.publications()


@pytest.mark.parametrize("repository", [NamedStore], indirect=True)
def test_the_recorded_name_is_the_identifier_the_store_committed(repository):
    """The name in the record has to be the name the rows were written under.

    Recalling a name the store never used deletes nothing and raises nothing, so
    the leak this change exists to fix would survive every other assertion here.
    """

    service, _ = repository
    cache = cache_of(service)
    attempt = next(iter(cache.publications()))
    pointer = cache.pointer()
    assert pointer is not None
    manifest = json.loads(
        (cache.directory / pointer["current"]["directory"] / "manifest.json").read_text(encoding="utf-8")
    )

    assert service.store.created == [attempt]
    assert manifest["graph"]["id"] == attempt


@pytest.mark.parametrize("repository", [NamedStore], indirect=True)
def test_recovery_cannot_reclaim_rows_a_reachable_generation_still_owns(repository):
    """A record naming a moved-away staging directory must not decide a live commit.

    This is the state a crash between the rename and the journal update used to
    leave behind: `current.json` reaches a generation whose manifest owns the rows,
    while the record still names the staging directory that no longer exists.
    """

    service, _ = repository
    cache = cache_of(service)
    attempt = next(iter(cache.publications()))
    pointer = cache.pointer()
    assert pointer is not None
    journal = json.loads((cache.directory / "publication.json").read_text())
    journal["attempts"][attempt] = {"directory": "staging-renamed-away", "state": "pending", "started_at": 0}
    (cache.directory / "publication.json").write_text(json.dumps(journal))

    reclaimed = cache.recover(time.monotonic() + 30)

    assert reclaimed == 0
    assert service.store.reclaimed == []
    assert attempt in cache.publications()


def test_clear_settles_a_record_whose_directory_went_missing(repository):
    """Clearing used to forget the handle and leave the rows charged to the binding.

    Directory absence is not evidence that collection reclaimed the commit: the
    record is the only thing that still names the rows, so clearing has to settle
    it rather than drop it.
    """

    service, _ = repository
    cache = cache_of(service)
    attempt = next(iter(cache.publications()))
    pointer = cache.pointer()
    assert pointer is not None
    shutil.rmtree(cache.directory / pointer["current"]["directory"])

    service.clear("scope")

    assert service.store.reclaimed == [attempt]
    assert cache.publications() == {}


def test_the_record_window_never_drops_a_generation_that_still_exists(repository):
    """The bound may not discard the only handle to rows that can still be recalled.

    A generation pinned by a reader keeps its directory, so `collect()` cannot
    retire it. If the window evicted its record anyway, the rows would be
    unreachable the moment the directory went away.
    """

    service, _ = repository
    cache = cache_of(service)
    kept = [f"{index:032x}" for index in range(70)]
    abandoned = [f"{index:032x}" for index in range(1000, 1006)]
    for name in kept:
        (cache.directory / ("generation-" + name)).mkdir()
    attempts = {
        **{name: {"directory": "generation-" + name, "state": "published", "started_at": 1} for name in kept},
        **{name: {"directory": "generation-" + name, "state": "published", "started_at": 0} for name in abandoned},
    }
    (cache.directory / "publication.json").write_text(json.dumps({"attempts": attempts}))

    cache.begin_publication(cache.staging(), time.monotonic() + 30)

    records = cache.publications()
    assert sorted(service.store.reclaimed) == sorted(abandoned)
    assert all(name in records for name in kept)


def test_a_commit_can_be_recalled_before_its_tables_exist(tmp_path, monkeypatch):
    """The first build can name its commit and die before initialize() creates tables.

    ``connection()`` converts every MySQL error into ``CodeError`` on exit, so the
    outer handler never saw 1146 and every retry failed during recovery — before
    ``create()`` could create the tables it was waiting for. A missing table means
    there is nothing to recall, not that the recall failed.
    """

    import pymysql

    from powercontext.builtin.code.seekdb import SeekDBGraphStore

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *arguments: object) -> bool:
            return False

        def execute(self, *arguments: object) -> None:
            raise pymysql.err.ProgrammingError(1146, "Table 'test.pc_code_edges' doesn't exist")

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *arguments: object) -> bool:
            return False

        def begin(self) -> None: ...
        def rollback(self) -> None: ...
        def commit(self) -> None: ...
        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(pymysql, "connect", lambda **options: Connection())
    store = SeekDBGraphStore(tmp_path / "seekdb", {})

    store.remove_generation("a" * 32, "binding", time.monotonic() + 30)


def test_the_window_never_recalls_the_publication_it_is_writing_for(repository):
    """Publishing must not read its own not-yet-renamed directory as stale.

    `publish()` names the final directory in the record before `os.replace` lands,
    so between those two steps the record is the one entry in the journal whose
    directory is missing. When the window has one slot open and every older record
    still names an existing directory, the in-flight attempt is the only stale
    entry — and recalling it deletes the rows this very publication just committed,
    leaving a `ready` index whose identifier owns nothing.
    """

    service, _ = repository
    cache = cache_of(service)
    attempt = next(iter(cache.publications()))
    kept = [f"{index:032x}" for index in range(cache.PUBLICATION_WINDOW)]
    for name in kept:
        (cache.directory / ("generation-" + name)).mkdir()
    records = {
        **cache.publications(),
        **{name: {"directory": "generation-" + name, "state": "published", "started_at": 1.0} for name in kept},
    }
    records[attempt] = {"directory": "generation-" + "e" * 32, "state": "published", "started_at": 0.0}
    (cache.directory / "publication.json").write_text(json.dumps({"attempts": records}))

    cache._write_publications(cache.publications(), time.monotonic() + 30, protected=frozenset({attempt}))

    assert service.store.reclaimed == []
    assert attempt in cache.publications()


@pytest.mark.parametrize("repository", [NamedStore], indirect=True)
def test_recovery_cannot_reclaim_rows_a_reader_pinned_generation_still_owns(repository):
    """A lease must outlive the pointer's interest in a generation.

    A reader that pinned a generation keeps it alive long after three later
    publications pushed it out of current/previous. Recovery used to decide from
    the pointer alone: once the identifier was no longer reachable there, the rows
    were deleted while the reader's directory and lock still existed, and the
    pinned query answered `ok` with the same fingerprint and no items.
    """

    service, _ = repository
    cache = cache_of(service)
    attempt = next(iter(cache.publications()))
    pointer = cache.pointer()
    assert pointer is not None
    reference = pointer["current"]
    generation_dir = cache.directory / reference["directory"]
    journal = json.loads((cache.directory / "publication.json").read_text())
    journal["attempts"][attempt] = {"directory": "staging-renamed-away", "state": "pending", "started_at": 0}
    (cache.directory / "publication.json").write_text(json.dumps(journal))
    reader = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys, time\n"
            "fd = os.open(sys.argv[1], os.O_CREAT | os.O_RDWR, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)\n"
            "print('locked', flush=True)\n"
            "time.sleep(120)\n",
            str(generation_dir / "reader.lock"),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert reader.stdout is not None and reader.stdout.readline().strip() == "locked"
        for _ in range(3):
            service.index("scope", full=True)
        manifest = json.loads((generation_dir / "manifest.json").read_text(encoding="utf-8"))
        with cache.store.open(generation_dir, manifest, time.monotonic() + 30) as graph:
            names = [node["name"] for node in graph.nodes()]
    finally:
        reader.kill()
        reader.wait()

    assert service.store.reclaimed == []
    assert attempt in cache.publications()
    assert "run" in names


def test_a_malformed_record_names_nothing_and_is_reclaimed(repository):
    """A record this class did not write is corruption, not an address.

    Coercing a missing or malformed `directory` to the empty name made every
    existence check against the cache root succeed, so the attempt could never be
    reclaimed and the rows stayed charged to the binding forever. An unnamed
    record is now handled exactly like a vanished directory: reclaimable, with the
    identifier as the only handle left.
    """

    service, _ = repository
    cache = cache_of(service)
    unnamed = "1" * 32
    empty = "2" * 32
    foreign = "3" * 32
    journal = json.loads((cache.directory / "publication.json").read_text())
    journal["attempts"][unnamed] = {"state": "pending", "started_at": 0}
    journal["attempts"][empty] = {"directory": "", "state": "pending", "started_at": 0}
    journal["attempts"][foreign] = {"directory": "../elsewhere", "state": "pending", "started_at": 0}
    (cache.directory / "publication.json").write_text(json.dumps(journal))

    cache.recover(time.monotonic() + 30)

    assert sorted(service.store.reclaimed) == sorted([unnamed, empty, foreign])
    assert unnamed not in cache.publications()
    assert empty not in cache.publications()
    assert foreign not in cache.publications()


def test_a_record_with_an_unreadable_timestamp_still_sorts(repository):
    """The window sorts records read back from JSON, so any value has to sort.

    `started_at` is whatever the journal holds — a corrupted write, an older
    format, a hand-edited file. Sorting a mix of strings, `None` and numbers used
    to raise, which broke the publication that was trying to trim the window.
    """

    service, _ = repository
    cache = cache_of(service)
    corrupt = "4" * 32
    older = "5" * 32
    filler = [f"{index:032x}" for index in range(cache.PUBLICATION_WINDOW)]
    attempts = {
        **cache.publications(),
        **{name: {"directory": "staging-gone", "state": "pending", "started_at": 1.0} for name in filler},
        corrupt: {"directory": "staging-gone", "state": "pending", "started_at": "yesterday"},
        older: {"directory": "staging-gone", "state": "pending", "started_at": None},
    }
    (cache.directory / "publication.json").write_text(json.dumps({"attempts": attempts}))

    cache.begin_publication(cache.staging(), time.monotonic() + 30)

    records = cache.publications()
    assert len(records) == cache.PUBLICATION_WINDOW


def test_a_recorded_identifier_is_validated_before_any_table_is_touched(tmp_path, monkeypatch):
    """The identifier arrives from a local record, so it is checked like a committed one.

    `remove_generation` used to accept any string and address `pc_code_nodes` and
    `pc_code_edges` with it. The identifier comes from a local `publication.json`,
    so validating it the way `create()` does before any row is touched keeps a
    corrupted record from becoming a delete statement.
    """

    import pymysql

    from powercontext.builtin.code.errors import CodeError
    from powercontext.builtin.code.seekdb import SeekDBGraphStore

    executed: list[object] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *arguments: object) -> bool:
            return False

        def execute(self, *arguments: object) -> None:
            executed.append(arguments)

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *arguments: object) -> bool:
            return False

        def begin(self) -> None: ...
        def rollback(self) -> None: ...
        def commit(self) -> None: ...
        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(pymysql, "connect", lambda **options: Connection())
    store = SeekDBGraphStore(tmp_path / "seekdb", {})

    with pytest.raises(CodeError):
        store.remove_generation("not-hex", "binding", time.monotonic() + 30)
    with pytest.raises(CodeError):
        store.remove_generation("a" * 31, "binding", time.monotonic() + 30)
    assert executed == []


def test_the_node_and_edge_deletes_are_scoped_to_the_binding(tmp_path, monkeypatch):
    """A corrupted record must not delete another binding's rows by quoting its identifier.

    Node and edge rows are addressed by identifier alone, and identifiers are only
    unique per binding. The deletes therefore run through `pc_code_generations`
    scoped to the binding, so an identifier this binding does not own selects
    nothing.
    """

    import pymysql

    from powercontext.builtin.code.seekdb import SeekDBGraphStore

    statements: list[tuple[object, ...]] = []

    class Cursor:
        def __enter__(self) -> Cursor:
            return self

        def __exit__(self, *arguments: object) -> bool:
            return False

        def execute(self, *arguments: object) -> None:
            statements.append(arguments)

    class Connection:
        def __enter__(self) -> Connection:
            return self

        def __exit__(self, *arguments: object) -> bool:
            return False

        def begin(self) -> None: ...
        def rollback(self) -> None: ...
        def commit(self) -> None: ...
        def cursor(self) -> Cursor:
            return Cursor()

    monkeypatch.setattr(pymysql, "connect", lambda **options: Connection())
    store = SeekDBGraphStore(tmp_path / "seekdb", {})

    store.remove_generation("b" * 32, "binding", time.monotonic() + 30)

    deletes = [statement for statement, _ in statements]
    assert len(deletes) == 3
    assert all("pc_code_generations" in str(delete) for delete in deletes[:2])
    assert all("binding = %s" in str(delete) for delete in deletes)
    assert [parameters for _, parameters in statements] == [("b" * 32, "binding")] * 3
