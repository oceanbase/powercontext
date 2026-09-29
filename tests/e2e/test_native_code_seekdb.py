# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Real embedded seekdb acceptance. Install powercontext[seekdb,code,server,cli]."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from powercontext.builtin.code import (
    CodeConfig,
    CodeError,
    CodeLimits,
    CodeQueryRequest,
    CodeRepositoryConfig,
    CodeService,
)
from powercontext.builtin.code.cache import GenerationCache
from powercontext.builtin.code.configuration import open_code_service
from powercontext.builtin.code.seekdb import SeekDBGraphStore
from powercontext.builtin.persistence.seekdb import SeekDBConfig, SeekDBProfile
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
from powercontext.builtin.scope import ScopeDraft
from powercontext.server.factory import create_server_app
from powercontext.server.settings import ServerSettings

pytest.importorskip("pylibseekdb")
for grammar in ("tree_sitter_python", "tree_sitter_javascript", "tree_sitter_typescript", "tree_sitter_go"):
    pytest.importorskip(grammar)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)

FILES = {
    "budget.py": 'def fit(value):\n    """Calculate billing allowance."""\n    return value\n',
    "api.py": "from budget import fit\ndef prepare(value):\n    return fit(value)\n",
    "tests/test_api.py": "from api import prepare\ndef test_prepare():\n    assert prepare(1) == 1\n",
    "web/budget.ts": "export const fit = (value: string): string => value;\n",
    "web/api.tsx": 'import {fit} from "./budget.js";\nexport function renderBudget() { return <div>{fit("预算")}</div>; }\n',
    "web/api.test.ts": 'import {renderBudget} from "./api";\nexport function testBudget() { return renderBudget(); }\n',
    "web/client.js": "export function clientBudget(value) { return value; }\n",
    "web/start.js": 'import {clientBudget} from "./client.js";\nexport function startBudget() { return clientBudget(1); }\n',
    "go.mod": "module example.com/budget\n\ngo 1.23\n",
    "server/budget.go": "package server\nfunc FitBudget(value int) int { return value }\n",
    "server/api.go": "package server\nfunc ServeBudget() int { return FitBudget(1) }\n",
    "server/api_test.go": 'package server\nimport "testing"\nfunc TestBudget(t *testing.T) { ServeBudget() }\n',
}


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("POWERCONTEXT_HOME", str(tmp_path / "home"))


def git(root: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)


def make_repository(directory: Path) -> Path:
    root = directory / "repository"
    root.mkdir()
    for path, content in FILES.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    git(root, "init")
    git(root, "add", ".")
    git(root, "-c", "user.name=Code Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    return root


@pytest.fixture(scope="module")
def backend(tmp_path_factory):
    database = SeekDBConfig(path=tmp_path_factory.mktemp("embedded-code") / "database")
    with asyncio.Runner() as runner:
        context = SeekDBProfile.open(database, tables=())
        profile = runner.run(context.__aenter__())
        store = SeekDBGraphStore(database.path, profile.connection_options)
        try:
            store.initialize(time.monotonic() + 120)
            yield database, store
        finally:
            store.close()
            runner.run(context.__aexit__(None, None, None))


@pytest.fixture
def repository(tmp_path, backend):
    _, store = backend
    root = make_repository(tmp_path)
    service = CodeService(
        CodeConfig(enabled=True, cache_dir=tmp_path / "cache", repositories={"scope": CodeRepositoryConfig(root=root)}),
        store=store,
    )
    service.index("scope")
    yield root, service
    service.clear("scope")


def query(service, kind, *, expected=None, before=None, budget=16000, **arguments):
    return service.query(
        "scope",
        CodeQueryRequest.model_validate({
            "operation": {"kind": kind, **arguments},
            "expected_fingerprint": expected,
            "before_fingerprint": before,
            "max_bytes": budget,
        }),
    )


def symbol(service, name, path):
    result = query(service, "symbols", query=name, path_prefix=path)
    return result.fingerprint, next(item for item in result.items if item["name"] == name and item["kind"] != "file")


@pytest.mark.parametrize(
    ("path", "name", "caller", "language"),
    (
        ("budget.py", "fit", "prepare", "python"),
        ("web/budget.ts", "fit", "renderBudget", "typescript"),
        ("web/client.js", "clientBudget", "startBudget", "javascript"),
        ("server/budget.go", "FitBudget", "ServeBudget", "go"),
    ),
)
def test_languages_round_trip_relations_and_source(repository, path, name, caller, language):
    root, service = repository
    fingerprint, definition = symbol(service, name, path)
    assert definition["language"] == language
    callers = query(service, "callers", expected=fingerprint, symbol_id=definition["id"])
    assert caller in {item["name"] for item in callers.items}
    target = next(item for item in callers.items if item["name"] == caller)
    callees = query(service, "callees", expected=fingerprint, symbol_id=target["id"])
    assert definition["id"] in {item["id"] for item in callees.items}
    impact = query(service, "impact", expected=fingerprint, symbol_id=definition["id"], depth=4)
    assert caller in {item["name"] for item in impact.items}
    read = query(
        service,
        "read",
        expected=fingerprint,
        path=path,
        file_sha256=definition["file_sha256"],
        start_line=1,
        end_line=2,
    )
    item = read.items[0]
    assert item["file_sha256"] == hashlib.sha256((root / path).read_bytes()).hexdigest()
    assert item["snippet_sha256"] == hashlib.sha256(item["content"].encode()).hexdigest()
    assert name in item["content"]
    mapping = query(service, "map", path_prefix=path)
    assert definition["id"] in {item["id"] for item in mapping.items}


def test_fts_path_boundaries_and_scope_isolation(repository):
    root, service = repository
    assert any(item["path"] == "budget.py" for item in query(service, "explore", query="billing allowance").items)
    for path in ("A%_/value.py", "a%_/value.py", "A%_ /value.py"):
        destination = root / path
        destination.parent.mkdir(exist_ok=True)
        destination.write_text("def ExactCase(): return 1\n")
    git(root, "add", ".")
    service.sync("scope")
    for path in ("A%_/value.py", "a%_/value.py", "A%_ /value.py"):
        assert {item["path"] for item in query(service, "map", path_prefix=path).items} == {path}
    fingerprint, definition = symbol(service, "fit", "budget.py")
    scoped = query(
        service, "impact", expected=fingerprint, symbol_id=definition["id"], path_prefix="budget.py", depth=3
    )
    assert scoped.coverage["path_boundary_edges"] > 0
    assert all(item["path"] == "budget.py" for item in scoped.items)
    other = CodeService(
        service.config.model_copy(update={"repositories": {"other": CodeRepositoryConfig(root=root)}}),
        store=service.store,
    )
    other.index("other")
    service.clear("scope")
    assert other.status("other").status == "ready"
    other.clear("other")


def test_incremental_changes_deleted_symbol_and_stale_rejection(repository):
    root, service = repository
    before, definition = symbol(service, "fit", "budget.py")
    (root / "budget.py").write_text("# deleted definition\n")
    with pytest.raises(CodeError, match="code_changed"):
        query(service, "callers", expected=before, symbol_id=definition["id"])
    refreshed = service.sync("scope")
    assert refreshed["reused_files"] > 0 and refreshed["extracted_files"] == 1
    changes = query(service, "changes")
    assert changes.items == [
        {
            "path": "budget.py",
            "change": "modified",
            "before_sha256": definition["file_sha256"],
            "after_sha256": hashlib.sha256((root / "budget.py").read_bytes()).hexdigest(),
        }
    ]
    impact = query(service, "impact_changes", expected=refreshed["fingerprint"], before=before, paths=["budget.py"])
    assert any(item["generation"] == "before" and item["name"] == "prepare" for item in impact.items)
    incremental = query(service, "symbols", query="prepare")
    service.index("scope", full=True)
    rebuilt = query(service, "symbols", query="prepare")
    assert rebuilt.fingerprint == incremental.fingerprint
    assert rebuilt.items == incremental.items
    unchanged = service.sync("scope")
    assert unchanged["extracted_files"] == 0


def test_tests_budgets_and_reader_safe_clear(repository):
    _, service = repository
    fingerprint, _ = symbol(service, "fit", "budget.py")
    affected = query(service, "affected_tests", expected=fingerprint, paths=["budget.py"])
    assert any(item["path"] == "tests/test_api.py" and item["witness_path"] for item in affected.items)
    bounded = query(service, "explore", query="budget", budget=1800)
    assert len(bounded.model_dump_json(by_alias=True).encode()) <= 1800
    deadline = time.monotonic() + 30
    directory = next(service.config.cache_dir.iterdir())
    cache = GenerationCache(directory, service.store)
    with cache.pin(deadline) as (old, _):
        assert service.clear("scope")["retained_reader_generations"] == 1
        assert service.status("scope").status == "missing"
        with old.graph(deadline) as graph:
            assert any(item["name"] == "fit" for item in graph.search("fit", "budget.py", 10))
    assert service.clear("scope")["retained_reader_generations"] == 0


def test_corrupt_rows_are_rejected_and_rebuilt(repository, backend):
    _, service = repository
    _, store = backend
    with store.connection(time.monotonic() + 10) as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE pc_code_nodes SET name = 'corrupted' WHERE name = 'fit'")
        connection.commit()
    with pytest.raises(CodeError, match="index_integrity_failed"):
        query(service, "symbols", query="fit")
    service.sync("scope")
    assert symbol(service, "fit", "budget.py")[1]["name"] == "fit"


@pytest.mark.parametrize("checkpoint", ("descriptor", "committed"))
@pytest.mark.parametrize("recovery", ("sync", "index", "clear"))
def test_interrupted_build_recovers_without_orphan_rows(repository, backend, tmp_path, checkpoint, recovery):
    _, service = repository
    database, store = backend
    before = query(service, "symbols", query="fit")
    marker = tmp_path / "builder-paused"
    script = """
import json
import os
import signal
import sys
from pathlib import Path

from powercontext.builtin.code import CodeConfig, CodeService
from powercontext.builtin.code import seekdb

config, database, options, marker, checkpoint = json.loads(sys.argv[1])
marker = Path(marker)

def pause():
    marker.touch()
    signal.pause()

if checkpoint == "descriptor":
    write_private = seekdb.write_private
    def interrupted_write(path, content):
        if path.name.startswith("graph"):
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.fsync(descriptor)
            os.close(descriptor)
            pause()
        write_private(path, content)
    seekdb.write_private = interrupted_write
else:
    create = seekdb.SeekDBGraphStore.create
    def interrupted_create(self, *args):
        result = create(self, *args)
        pause()
        return result
    seekdb.SeekDBGraphStore.create = interrupted_create

store = seekdb.SeekDBGraphStore(Path(database), options)
CodeService(CodeConfig.model_validate(config), store=store).index("scope", full=True)
"""
    arguments = json.dumps([
        service.config.model_dump(mode="json"),
        str(database.path),
        store.options,
        str(marker),
        checkpoint,
    ])
    with (tmp_path / "builder.log").open("w+") as log:
        builder = subprocess.Popen([sys.executable, "-c", script, arguments], stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 45
            while not marker.exists() and builder.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            log.seek(0)
            assert marker.exists(), log.read()
        finally:
            builder.kill()
            builder.wait(timeout=10)
    after = query(service, "symbols", query="fit")
    assert after.fingerprint == before.fingerprint and after.items == before.items
    result = getattr(service, recovery)("scope")
    assert result["status"] == ("cleared" if recovery == "clear" else "ready")
    directory = next(service.config.cache_dir.iterdir())
    assert not list(directory.glob("staging-*"))
    references = [json.loads(path.read_text())["id"] for path in directory.glob("generation-*/graph.json")]
    with store.connection(time.monotonic() + 10) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT id FROM pc_code_generations WHERE binding = %s", (directory.name,))
        assert {row[0] for row in cursor.fetchall()} == set(references)
        for table in ("pc_code_nodes", "pc_code_edges"):
            cursor.execute(
                f"SELECT COUNT(*) FROM {table} rows_ LEFT JOIN pc_code_generations generations "  # noqa: S608 - constant tables.
                "ON generations.id = rows_.generation_id WHERE generations.id IS NULL"
            )
            assert cursor.fetchone()[0] == 0


@pytest.mark.parametrize("content", (b"", b'{"backend":'))
@pytest.mark.parametrize("recovery", ("sync", "index", "clear"))
def test_legacy_incomplete_staging_descriptor_recovers(repository, content, recovery):
    _, service = repository
    directory = next(service.config.cache_dir.iterdir())
    staging = GenerationCache(directory, service.store).staging()
    (staging / "graph.json").write_bytes(content)
    result = getattr(service, recovery)("scope")
    assert result["status"] == ("cleared" if recovery == "clear" else "ready")
    assert not staging.exists()
    if recovery != "clear":
        assert symbol(service, "fit", "budget.py")[1]["name"] == "fit"


def test_unknown_committed_staging_generation_preserves_published_index(repository):
    _, service = repository
    before = query(service, "symbols", query="fit")
    directory = next(service.config.cache_dir.iterdir())
    staging = GenerationCache(directory, service.store).staging()
    service.store.create(staging, [], [], time.monotonic() + 30)
    descriptor = staging / "graph.json"
    original = descriptor.read_bytes()
    descriptor.write_bytes(b"")
    try:
        with pytest.raises(CodeError, match="index_integrity_failed"):
            service.clear("scope")
        after = query(service, "symbols", query="fit")
        assert after.fingerprint == before.fingerprint and after.items == before.items
        assert staging.exists()
    finally:
        descriptor.write_bytes(original)


def test_malformed_published_descriptor_is_rejected(repository):
    _, service = repository
    directory = next(service.config.cache_dir.iterdir())
    pointer = json.loads((directory / "current.json").read_text())
    published = directory / pointer["current"]["directory"]
    descriptor = published / "graph.json"
    original = descriptor.read_bytes()
    descriptor.write_bytes(b"")
    try:
        with pytest.raises(CodeError, match="index_integrity_failed"):
            query(service, "symbols", query="fit")
        with pytest.raises(CodeError, match="index_integrity_failed"):
            service.store.remove(published, time.monotonic() + 10)
    finally:
        descriptor.write_bytes(original)


def test_failed_publication_keeps_old_index_and_retry_cleans_up(repository, monkeypatch):
    root, service = repository
    before = service.status("scope").fingerprint
    original = (root / "budget.py").read_text()
    (root / "budget.py").write_text(original.replace("return value", "return value + 1"))
    replace = os.replace

    def fail_pointer(source, destination):
        if Path(destination).name == "current.json":
            raise OSError("publication fault")  # noqa: TRY003 - deliberate publication failure.
        return replace(source, destination)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_pointer)
        with pytest.raises(CodeError, match="code_build_failed"):
            service.sync("scope")
    (root / "budget.py").write_text(original)
    assert service.status("scope").fingerprint == before
    assert query(service, "symbols", query="fit").items
    (root / "budget.py").write_text(original.replace("return value", "return value + 2"))
    service.sync("scope")
    directory = next(service.config.cache_dir.iterdir())
    assert len(list(directory.glob("generation-*"))) == 2
    with service.store.connection(time.monotonic() + 10) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM pc_code_generations WHERE binding = %s", (directory.name,))
        assert cursor.fetchone()[0] == 2


def test_missing_fulltext_index_is_unavailable_until_sync_repairs_it(repository, backend):
    _, service = repository
    _, store = backend
    with store.connection(time.monotonic() + 30) as connection, connection.cursor() as cursor:
        cursor.execute("DROP INDEX ix_code_search ON pc_code_nodes")
        connection.commit()
    try:
        assert service.status("scope").status == "failed"
        with pytest.raises(CodeError, match="code_storage_unavailable"):
            query(service, "symbols", query="fit")
        service.sync("scope")
        assert query(service, "explore", query="billing allowance").items
    finally:
        store.initialize(time.monotonic() + 60)


def test_database_query_timeout_is_bounded_and_next_query_recovers(repository, monkeypatch):
    from pymysql.cursors import Cursor

    _, service = repository
    constrained = CodeService(
        service.config.model_copy(update={"limits": CodeLimits(query_seconds=0.2)}), store=service.store
    )
    execute = Cursor.execute

    def slow_database(cursor, statement, arguments=None):
        if statement.startswith("SELECT content_hash"):
            return execute(cursor, "SELECT SLEEP(2)")
        return execute(cursor, statement, arguments)

    start = time.monotonic()
    with monkeypatch.context() as patch:
        patch.setattr(Cursor, "execute", slow_database)
        with pytest.raises(CodeError, match="code_timeout"):
            query(constrained, "symbols", query="fit")
    assert time.monotonic() - start < 1.5
    assert service.status("scope").status == "ready"


def test_cache_quota_rolls_back_publication_and_reclaims_graph(repository):
    _, service = repository
    before = service.status("scope").fingerprint
    constrained = CodeService(
        service.config.model_copy(update={"limits": CodeLimits(max_cache_bytes=1)}), store=service.store
    )
    with pytest.raises(CodeError, match="code_cache_limit_exceeded"):
        constrained.index("scope", full=True)
    assert service.status("scope").fingerprint == before
    directory = next(service.config.cache_dir.iterdir())
    with service.store.connection(time.monotonic() + 10) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM pc_code_generations WHERE binding = %s", (directory.name,))
        assert cursor.fetchone()[0] == 1


def test_process_exit_before_publication_recovers_without_partial_results(repository, backend, tmp_path):
    root, service = repository
    database, _ = backend
    before = service.status("scope").fingerprint
    original = (root / "budget.py").read_text()
    (root / "budget.py").write_text(original.replace("return value", "return value + 1"))
    script = tmp_path / "interrupted_builder.py"
    script.write_text(
        "import asyncio, os, sys\n"
        "from powercontext.builtin.code import CodeConfig\n"
        "from powercontext.builtin.code.cache import GenerationCache\n"
        "from powercontext.builtin.code.configuration import open_code_service\n"
        "from powercontext.builtin.persistence.seekdb import SeekDBConfig\n"
        "replace = GenerationCache.replace_json\n"
        "def interrupted(self, name, value):\n"
        "    if name == 'current.json': os._exit(72)\n"
        "    return replace(self, name, value)\n"
        "GenerationCache.replace_json = interrupted\n"
        "async def main():\n"
        "    async with open_code_service(CodeConfig.model_validate_json(sys.argv[1]), SeekDBConfig.model_validate_json(sys.argv[2])) as service:\n"
        "        await asyncio.to_thread(service.sync, 'scope')\n"
        "asyncio.run(main())\n"
    )
    result = subprocess.run(
        [sys.executable, str(script), service.config.model_dump_json(), database.model_dump_json()],
        capture_output=True,
        timeout=120,
    )
    assert result.returncode == 72, result.stderr.decode()
    (root / "budget.py").write_text(original)
    assert service.status("scope").fingerprint == before
    assert query(service, "symbols", query="fit").items
    service.index("scope", full=True)
    directory = next(service.config.cache_dir.iterdir())
    assert len(list(directory.glob("generation-*"))) == 2
    with service.store.connection(time.monotonic() + 10) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM pc_code_generations WHERE binding = %s", (directory.name,))
        assert cursor.fetchone()[0] == 2


def test_http_prepare_online_cli_sync_and_restart(tmp_path, backend):
    database, _ = backend
    root = make_repository(tmp_path)

    async def exercise():
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Embedded code", summary="Code evidence", idempotency_key="seekdb-code")
            )
        code = CodeConfig(
            enabled=True, cache_dir=tmp_path / "cache", repositories={scope.scope_id: CodeRepositoryConfig(root=root)}
        )
        env_file = tmp_path / "server.env"
        env_file.write_text(
            "POWERCONTEXT_SERVER_DATABASE='"
            + database.model_dump_json()
            + "'\n"
            + "POWERCONTEXT_SERVER_CODE='"
            + code.model_dump_json()
            + "'\n"
        )

        async def cli(operation):
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "from powercontext.cli.app import main; main()",
                "code",
                operation,
                "--scope",
                scope.scope_id,
                "--env-file",
                str(env_file),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(process.communicate(), 180)
            assert process.returncode == 0, stderr.decode()
            return json.loads(stdout)

        for restart in range(2):
            app = create_server_app(settings=ServerSettings(database=database, code=code))
            async with (
                app.router.lifespan_context(app),
                httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
            ):
                if not restart:
                    assert (await cli("index"))["status"] == "ready"
                assert (await cli("status"))["status"] == "ready"
                for name, path in (
                    ("prepare", "api.py"),
                    ("renderBudget", "web/api.tsx"),
                    ("clientBudget", "web/client.js"),
                    ("ServeBudget", "server/api.go"),
                ):
                    response = await http.post(
                        "/v1/context/prepare",
                        json={
                            "scope_id": scope.scope_id,
                            "query": name,
                            "assembly": {"sections": []},
                            "include_code": True,
                        },
                    )
                    response.raise_for_status()
                    assert response.json()["status"] == "ready", response.json()
                    assert path in response.json()["content"]
                if not restart:
                    (root / "api.py").write_text(FILES["api.py"] + "# changed\n")
                    stale = await http.post(
                        f"/v1/scopes/{scope.scope_id}/code/query",
                        json={"operation": {"kind": "symbols", "query": "prepare"}},
                    )
                    assert stale.status_code == 409
                    response = await http.post(
                        "/v1/context/prepare",
                        json={
                            "scope_id": scope.scope_id,
                            "query": "prepare",
                            "assembly": {"sections": []},
                            "include_code": True,
                        },
                    )
                    assert response.json()["status"] == "empty"
                    await cli("sync")
                else:
                    assert (await cli("clear"))["status"] == "cleared"
                    status = await http.post(
                        f"/v1/scopes/{scope.scope_id}/code/query", json={"operation": {"kind": "status"}}
                    )
                    assert status.json()["status"] == "missing"

    asyncio.run(exercise())


def test_embedded_profile_restart_preserves_index(tmp_path):
    root = make_repository(tmp_path)
    database = SeekDBConfig(path=tmp_path / "database")
    config = CodeConfig(
        enabled=True, cache_dir=tmp_path / "cache", repositories={"scope": CodeRepositoryConfig(root=root)}
    )

    async def exercise():
        async with open_code_service(config, database) as service:
            await asyncio.to_thread(service.index, "scope")
            before = await asyncio.to_thread(query, service, "explore", query="billing")
        async with open_code_service(config, database) as service:
            after = await asyncio.to_thread(query, service, "explore", query="billing")
            assert after.fingerprint == before.fingerprint
            assert after.items == before.items and after.items
            await asyncio.to_thread(service.clear, "scope")

    asyncio.run(exercise())


def test_cancelled_shutdown_drains_an_active_code_query(tmp_path, monkeypatch):
    from pymysql.cursors import Cursor

    root = make_repository(tmp_path)
    database = SeekDBConfig(path=tmp_path / "database")
    config = CodeConfig(
        enabled=True, cache_dir=tmp_path / "cache", repositories={"scope": CodeRepositoryConfig(root=root)}
    )
    started, release = threading.Event(), threading.Event()
    execute = Cursor.execute

    def slow_query(cursor, statement, arguments=None):
        if statement.startswith("SELECT payload FROM pc_code_nodes"):
            started.set()
            assert release.wait(10)
        return execute(cursor, statement, arguments)

    async def exercise():
        context = open_code_service(config, database)
        service = await context.__aenter__()
        await asyncio.to_thread(service.index, "scope")
        with ThreadPoolExecutor(max_workers=1) as workers, monkeypatch.context() as patch:
            patch.setattr(Cursor, "execute", slow_query)
            result = workers.submit(query, service, "symbols", query="fit")
            try:
                assert await asyncio.to_thread(started.wait, 10)
                shutdown = asyncio.create_task(context.__aexit__(None, None, None))
                await asyncio.sleep(0.05)
                shutdown.cancel("first")
                await asyncio.sleep(0.05)
                shutdown.cancel("second")
                assert not shutdown.done()
            finally:
                release.set()
            with pytest.raises(asyncio.CancelledError):
                await shutdown
            assert result.result(timeout=10).items
        async with open_code_service(config, database) as reopened:
            assert (await asyncio.to_thread(reopened.status, "scope")).status == "ready"

    asyncio.run(exercise())
