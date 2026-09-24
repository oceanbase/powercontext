# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Observable repository analysis and worktree consistency regressions."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from powercontext.builtin.code import (
    CodeConfig,
    CodeError,
    CodeQueryRequest,
    CodeQueryResult,
    CodeRepositoryConfig,
    CodeService,
)

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


def test_index_closes_its_database_before_returning(repository):
    import gc
    import warnings

    _, service = repository
    gc.collect()
    with warnings.catch_warnings(record=True) as observed:
        warnings.simplefilter("always", ResourceWarning)
        service.index("scope", full=True)
        gc.collect()
    assert not any(
        issubclass(warning.category, ResourceWarning) and "unclosed database" in str(warning.message)
        for warning in observed
    )


def test_cycles_diamonds_and_nested_tests_keep_finite_source_evidence(repository):
    root, service = repository
    (root / "flow.py").write_text(
        "def leaf():\n    return 1\n\n"
        "def left():\n    return leaf()\n\n"
        "def right():\n    return leaf()\n\n"
        "def top():\n    return left() + right()\n\n"
        "def cycle_a():\n    return cycle_b()\n\n"
        "def cycle_b():\n    return cycle_a() + leaf()\n"
    )
    (root / "tests/test_flow.py").write_text(
        "from flow import top\n\ndef test_nested():\n    def inner():\n        return top()\n    assert inner() == 2\n"
    )
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, symbol = definition(service, "leaf", "flow.py")
    callers = query(service, "callers", expected=fingerprint, symbol_id=symbol["id"])
    assert {item["name"] for item in callers.items} == {"left", "right", "cycle_b"}
    impact = query(service, "impact", expected=fingerprint, symbol_id=symbol["id"], depth=5)
    assert {"left", "right", "top", "cycle_a", "cycle_b"} <= {item["name"] for item in impact.items}
    assert len({item["id"] for item in impact.items}) == len(impact.items)
    assert sum(item["name"] == "top" for item in impact.items) == 1
    tests = query(service, "affected_tests", expected=fingerprint, paths=["flow.py"])
    nested = next(item for item in tests.items if item["qualified_name"] == "test_nested.inner")
    assert nested["resolution"] == "resolved_static"
    assert nested["witness_path"]
    assert nested["path"] == "tests/test_flow.py"


def test_git_worktrees_have_independent_freshness_and_cached_evidence(repository, tmp_path):
    root, original = repository
    sibling = tmp_path / "sibling"
    git(root, "worktree", "add", "--detach", str(sibling), "HEAD")
    service = CodeService(
        original.config.model_copy(
            update={
                "repositories": {
                    "scope": CodeRepositoryConfig(root=root),
                    "sibling": CodeRepositoryConfig(root=sibling),
                }
            }
        )
    )
    service.index("sibling")
    request = CodeQueryRequest.model_validate({"operation": {"kind": "explore", "query": "prepare"}})
    before = service.query("sibling", request)
    assert isinstance(before, CodeQueryResult)
    (root / "src/sample/api.py").write_text("def prepare(value):\n    return 'changed-original'\n")
    with pytest.raises(CodeError, match="code_changed"):
        service.query("scope", request)
    after = CodeService(service.config).query("sibling", request)
    assert isinstance(after, CodeQueryResult)
    assert after.fingerprint == before.fingerprint
    assert after.items == before.items
    service.sync("scope")
    original_result = service.query("scope", request)
    assert isinstance(original_result, CodeQueryResult)
    assert original_result.fingerprint != after.fingerprint
    assert any("changed-original" in str(item.get("content", "")) for item in original_result.items)
    assert all("changed-original" not in str(item.get("content", "")) for item in after.items)


def test_unavailable_fts_storage_never_advertises_a_ready_index(repository, tmp_path, monkeypatch):
    import sqlite3

    _, original = repository
    service = CodeService(original.config.model_copy(update={"cache_dir": tmp_path / "fts-unavailable"}))
    connect = sqlite3.connect

    def restricted_connection(*args, **kwargs):
        connection = connect(*args, **kwargs)
        # Exercise an actual SQLite capability denial rather than fabricating an
        # indexer exception. A build without FTS5 reaches the same storage boundary.
        connection.set_authorizer(
            lambda action, table, module, database, trigger: (
                sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_CREATE_VTABLE and module == "fts5"
                else sqlite3.SQLITE_OK
            )
        )
        return connection

    monkeypatch.setattr(sqlite3, "connect", restricted_connection)
    with pytest.raises(CodeError, match="code_storage_unavailable") as failure:
        service.index("scope")
    assert failure.value.status == 503
    status = service.status("scope")
    assert status.status == "missing"
    assert status.last_build is not None
    assert status.last_build["status"] == "failed"
    assert status.last_build["reason"] == "code_storage_unavailable"
    with pytest.raises(CodeError) as unavailable:
        query(service, "symbols", query="prepare")
    assert unavailable.value.status == 503


def test_clear_preserves_active_evidence_and_other_bindings(repository):
    import time

    from powercontext.builtin.code.cache import GenerationCache

    _, service = repository
    other = CodeService(
        service.config.model_copy(update={"repositories": {"other": service.config.repositories["scope"]}})
    )
    other.index("other")
    directories = list(service.config.cache_dir.iterdir())
    caches = [GenerationCache(path) for path in directories]
    deadline = time.monotonic() + 10
    with caches[0].pin(deadline) as (first, _), caches[1].pin(deadline) as (second, _):
        cleared = service.clear("scope")
        assert cleared == {"status": "cleared", "retained_reader_generations": 1}
        assert service.status("scope").status == "missing"
        assert other.status("other").status == "ready"
        assert first.source("src/sample/budget.py", deadline) == second.source("src/sample/budget.py", deadline)
    assert service.clear("scope")["retained_reader_generations"] == 0
    service.index("scope")
    assert service.status("scope").status == "ready"


def test_code_diagnostics_are_aggregate_and_do_not_change_results(repository, caplog):
    from contextlib import contextmanager

    from powercontext.builtin.code.telemetry import tracing_context

    _, service = repository
    spans = []

    class Span:
        def __init__(self, name):
            self.name, self.attributes = name, {}

        def set_attributes(self, attributes):
            self.attributes.update(attributes)

        def set_outcome(self, outcome):
            self.outcome = outcome

    class Trace:
        @contextmanager
        def stage(self, name, *, attributes):
            span = Span(name)
            spans.append(span)
            yield span

        def background(self, *args, **kwargs):
            return self.stage(*args, **kwargs)

    caplog.set_level("DEBUG", logger="powercontext.builtin.code.telemetry")
    with tracing_context(Trace()):
        result = query(service, "explore", query="prepare")
    assert result.items
    assert {span.name for span in spans} >= {
        "code.query",
        "code.verify",
        "code.search",
        "code.traverse",
        "code.render_result",
    }
    assert all(span.attributes["powercontext.code.seconds"] >= 0 for span in spans)
    attributes = repr([span.attributes for span in spans])
    assert str(service.config.repositories["scope"].root) not in attributes
    assert "prepare" not in attributes
    assert "预算" not in attributes

    class BrokenTrace:
        def stage(self, *args, **kwargs):
            raise RuntimeError

        background = stage

    with tracing_context(BrokenTrace()):
        observed = query(service, "explore", query="prepare")
    assert observed.items == result.items
    assert observed.fingerprint == result.fingerprint


def test_path_scoped_graph_counts_omitted_edges_without_exposing_targets(repository):
    _, service = repository
    fingerprint, symbol = definition(service, "fit", "src/sample/budget.py")
    result = query(
        service,
        "callers",
        symbol_id=symbol["id"],
        expected=fingerprint,
        path_prefix="src/sample/budget.py",
    )
    assert result.items == []
    assert result.coverage["path_boundary_edges"] == 1
    assert "edges_outside_path_scope" in result.limitations
    assert "src/sample/api.py" not in result.model_dump_json()


def test_code_tracing_exports_stale_query_as_error_without_source_text(repository):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.trace import StatusCode

    from powercontext.builtin.code.telemetry import tracing_context
    from powercontext.server.tracing import ServerTracing

    root, service = repository
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    try:
        with tracing_context(ServerTracing(provider)):
            query(service, "symbols", query="prepare")
            (root / "src/sample/api.py").write_text("# private changed source\n")
            with pytest.raises(CodeError, match="code_changed"):
                query(service, "symbols", query="private query text")
        spans = exporter.get_finished_spans()
        failed = [span for span in spans if span.name == "code.query" and span.status.status_code == StatusCode.ERROR]
        assert len(failed) == 1
        assert failed[0].attributes is not None
        assert failed[0].attributes["powercontext.code.reason"] == "code_changed"
        attributes = repr([dict(span.attributes or {}) for span in spans])
        assert "private" not in attributes
        assert str(root) not in attributes
    finally:
        provider.shutdown()


def test_literal_and_glob_exclusions_preserve_public_visibility(repository):
    _, service = repository
    repository_config = service.config.repositories["scope"].model_copy(
        update={
            "exclude": (
                "src/sample/budget.py",
                "src/sample/oth[ae]r.py",
                "tests/test_*.py",
                *(f"unrelated/file_{number}.py" for number in range(2000)),
            )
        }
    )
    filtered = CodeService(service.config.model_copy(update={"repositories": {"scope": repository_config}}))
    filtered.index("scope")
    assert query(filtered, "symbols", query="fit").items == []
    assert all(not item["path"].startswith("tests/") for item in query(filtered, "symbols", query="test_prepare").items)
    assert query(filtered, "symbols", query="prepare").items


def git(root: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    files = {
        "src/sample/__init__.py": "from .budget import fit\n",
        "src/sample/budget.py": 'def fit(value):\n    """预算按字节计算。"""\n    return value\n',
        "src/sample/api.py": "from . import fit as trim\n\ndef prepare(value):\n    return trim(value)\n",
        "src/sample/other.py": "def fit(value):\n    return None\n",
        "tests/test_api.py": "from sample.api import prepare\n\ndef test_prepare():\n    assert prepare('ok') == 'ok'\n",
    }
    for path, content in files.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    git(root, "init")
    git(root, "add", ".")
    git(root, "-c", "user.name=Code Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    service = CodeService(
        CodeConfig(
            enabled=True,
            repositories={"scope": CodeRepositoryConfig(root=root)},
            cache_dir=tmp_path / "cache",
        )
    )
    service.index("scope", full=True)
    return root, service


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


def definition(service, name, path=""):
    result = query(service, "symbols", query=name, path_prefix=path)
    return result.fingerprint, next(item for item in result.items if item["name"] == name and item["kind"] != "file")


def test_alias_reexport_callers_and_test_evidence(repository):
    _, service = repository
    fingerprint, symbol = definition(service, "fit", "src/sample/budget.py")
    callers = query(service, "callers", expected=fingerprint, symbol_id=symbol["id"])
    assert {item["name"] for item in callers.items} == {"prepare"}
    assert callers.items[0]["witness_path"][0]["rule_id"] == "explicit_import"
    tests = query(service, "affected_tests", expected=fingerprint, paths=["src/sample/budget.py"])
    assert any(item["path"] == "tests/test_api.py" and item["witness_path"] for item in tests.items)
    read = query(
        service,
        "read",
        expected=fingerprint,
        path=symbol["path"],
        file_sha256=symbol["file_sha256"],
        start_line=1,
        end_line=3,
    )
    assert "预算按字节计算" in read.items[0]["content"]
    assert read.items[0]["start_line"] == 1


def test_same_size_and_mtime_edit_is_stale_then_syncs(repository):
    root, service = repository
    fingerprint, symbol = definition(service, "prepare")
    path = root / "src/sample/api.py"
    before = path.stat()
    path.write_bytes(path.read_bytes().replace(b"trim(value)", b"trim(None )"))
    os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
    status = service.status("scope")
    assert status.status == "stale"
    assert status.freshness == "stale"
    assert status.fingerprint == fingerprint
    with pytest.raises(CodeError, match="code_changed"):
        query(service, "callees", expected=fingerprint, symbol_id=symbol["id"])
    update = service.sync("scope")
    assert update["extracted_files"] == 1
    assert update["fingerprint"] != fingerprint
    assert service.status("scope").status == "ready"
    with pytest.raises(CodeError, match="code_changed"):
        query(service, "callees", expected=fingerprint, symbol_id=symbol["id"])


def test_unchanged_caller_resolves_a_new_module(repository):
    root, service = repository
    path = root / "src/sample/api.py"
    path.write_text("from .later import action\n\ndef prepare(value):\n    return action(value)\n")
    service.sync("scope")
    fingerprint, symbol = definition(service, "prepare")
    assert query(service, "callees", expected=fingerprint, symbol_id=symbol["id"]).items == []
    (root / "src/sample/later.py").write_text("def action(value):\n    return value\n")
    git(root, "add", "src/sample/later.py")
    update = service.sync("scope")
    assert update["extracted_files"] == 1
    fingerprint, symbol = definition(service, "prepare")
    result = query(service, "callees", expected=fingerprint, symbol_id=symbol["id"])
    assert [item["name"] for item in result.items] == ["action"]


def test_deleted_definition_retains_before_impact(repository):
    root, service = repository
    original = service.status("scope").fingerprint
    (root / "src/sample/budget.py").unlink()
    service.sync("scope")
    changes = query(service, "changes")
    assert {"path": "src/sample/budget.py", "change": "deleted"}.items() <= changes.items[0].items()
    assert changes.before_fingerprint == original
    impact = query(
        service, "impact_changes", expected=changes.fingerprint, before=original, paths=["src/sample/budget.py"]
    )
    assert any(item["name"] == "prepare" and item["generation"] == "before" for item in impact.items)
    with pytest.raises(CodeError, match="code_target_missing"):
        query(service, "affected_tests", expected=changes.fingerprint, paths=["src/sample/budget.py"])


def test_path_restriction_precedes_result_limit(repository):
    root, service = repository
    for index in range(60):
        (root / f"src/sample/duplicate{index}.py").write_text("def fit(value):\n    return value\n")
    git(root, "add", "src")
    service.sync("scope")
    result = query(service, "symbols", query="fit", path_prefix="src/sample/budget.py", limit=1)
    assert len(result.items) == 1
    assert result.items[0]["path"] == "src/sample/budget.py"


def test_credentials_and_symlinks_are_not_read(repository, tmp_path):
    root, service = repository
    sentinel = "NEVER_RETURN_THIS_SENTINEL"
    (root / ".env").write_text(sentinel)
    outside = tmp_path / "outside.py"
    outside.write_text(f"def {sentinel}(): pass\n")
    (root / "src/sample/link.py").symlink_to(outside)
    git(root, "add", ".env", "src/sample/link.py")
    service.sync("scope")
    result = query(service, "explore", query=sentinel)
    assert result.items == []
    assert result.coverage["skipped_files"] >= 2


def test_code_disabled_and_unbound_scopes_do_not_open_repositories(tmp_path):
    service = CodeService(CodeConfig(cache_dir=tmp_path / "cache"))
    assert service.status("scope").status == "disabled"
    with pytest.raises(CodeError, match="code_disabled"):
        query(service, "symbols", query="fit")
    assert not (tmp_path / "cache").exists()


def test_response_budget_includes_envelope(repository):
    _, service = repository
    result = query(service, "explore", query="fit prepare", budget=1300)
    assert len(result.model_dump_json(by_alias=True).encode()) <= 1300
    assert result.status == "partial"
    assert "output_budget" in result.limitations


def test_response_rendering_uses_the_shared_query_deadline(repository, monkeypatch):
    from powercontext.builtin.code import service as service_module

    _, service = repository
    monotonic = time.monotonic
    serialize = service_module.json_bytes
    elapsed = 0

    def delayed_serialization(value):
        nonlocal elapsed
        content = serialize(value)
        if isinstance(value, dict) and value.get("schema") == "powercontext.code-query.v1":
            # Model time spent rendering without a slow or timing-sensitive sleep.
            elapsed += service.config.limits.query_seconds
        return content

    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + elapsed)
    monkeypatch.setattr(service_module, "json_bytes", delayed_serialization)
    with pytest.raises(CodeError, match="code_timeout"):
        query(service, "symbols", query="prepare")


def test_budgeted_source_keeps_complete_lines_and_matching_citations(repository):
    import hashlib

    root, service = repository
    content = "".join(f'第 {index} 行包含引号 " 和反斜杠 \\\r\n' for index in range(100)).encode()
    (root / "budgeted.md").write_bytes(content)
    git(root, "add", ".")
    fingerprint = service.sync("scope")["fingerprint"]
    for budget in (1500, 1800):
        result = query(
            service,
            "read",
            expected=fingerprint,
            path="budgeted.md",
            file_sha256=hashlib.sha256(content).hexdigest(),
            start_line=1,
            end_line=100,
            budget=budget,
        )
        assert result.status == "partial"
        assert "output_budget" in result.limitations
        assert len(result.model_dump_json(by_alias=True).encode()) <= budget
        snippet = result.items[0]
        assert 1 <= snippet["end_line"] < 100
        expected = b"\n".join(content.split(b"\n")[: snippet["end_line"]]) + b"\n"
        assert snippet["content"].encode() == expected
        assert snippet["snippet_sha256"] == hashlib.sha256(expected).hexdigest()


def test_query_rejects_ambiguous_paths_and_missing_fingerprint():
    from pydantic import ValidationError

    for operation in (
        {"kind": "symbols", "query": "fit", "path_prefix": "../outside"},
        {"kind": "callers", "symbol_id": "a"},
        {"kind": "map", "depth": True},
    ):
        with pytest.raises(ValidationError):
            CodeQueryRequest.model_validate({"operation": operation})


def test_conditional_definition_and_receiver_are_candidates(repository):
    root, service = repository
    (root / "src/sample/dynamic.py").write_text(
        "if flag:\n    def selected(): pass\ndef invoke():\n    selected()\n"
        "class Example:\n    def run(self): self.help()\n    def help(self): pass\n"
    )
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, symbol = definition(service, "selected")
    callers = query(service, "callers", expected=fingerprint, symbol_id=symbol["id"])
    assert callers.items[0]["resolution"] == "candidate"
    assert callers.items[0]["witness_path"][0]["rule_id"] == "conditional_binding"
    _, helper = definition(service, "help")
    callers = query(service, "callers", expected=fingerprint, symbol_id=helper["id"])
    assert callers.items[0]["resolution"] == "candidate"


def test_loop_binding_does_not_invent_global_call(repository):
    root, service = repository
    (root / "src/sample/shadow.py").write_text(
        "def fit(): pass\ndef run(items):\n    for fit in items:\n        fit()\n"
    )
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, symbol = definition(service, "fit", "src/sample/shadow.py")
    assert query(service, "callers", expected=fingerprint, symbol_id=symbol["id"]).items == []


@pytest.mark.parametrize(
    "pattern",
    [
        "{'callback': target}",
        "{'callback': _, **target}",
        "[target, *rest]",
        "[first, *target]",
        "Thing(callback=target)",
        "Thing(target)",
        "[first] as target",
        "(target, 1) | (target, 2)",
        "target",
    ],
)
def test_match_captures_do_not_resolve_to_global_functions(repository, pattern):
    root, service = repository
    (root / "patterns.py").write_text(
        "def target(): return 'global'\n"
        "def run(value):\n"
        "    match value:\n"
        f"        case {pattern} if target:\n"
        "            return target()\n"
    )
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, caller = definition(service, "run", "patterns.py")
    assert query(service, "callees", expected=fingerprint, symbol_id=caller["id"]).items == []
    _, target = definition(service, "target", "patterns.py")
    assert query(service, "callers", expected=fingerprint, symbol_id=target["id"]).items == []


def test_match_value_and_class_patterns_do_not_bind_names(repository):
    root, service = repository
    (root / "patterns.py").write_text(
        "class Thing: pass\n"
        "def callback(): pass\n"
        "def run(value):\n"
        "    match value:\n"
        "        case Thing(callback=captured):\n"
        "            return Thing(), callback()\n"
        "        case Thing.CONSTANT:\n"
        "            return Thing(), callback()\n"
    )
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, caller = definition(service, "run", "patterns.py")
    callees = query(service, "callees", expected=fingerprint, symbol_id=caller["id"])
    assert {item["name"] for item in callees.items} == {"Thing", "callback"}
    assert all(item["resolution"] == "resolved_static" for item in callees.items)


@pytest.mark.parametrize(
    "assignment",
    [
        "lib.original = replacement",
        "lib.original, other = replacement, None",
        "lib.original += replacement",
        "del lib.original",
        "def patch():\n    lib.original = replacement",
        "lib.Box.original = replacement",
    ],
)
def test_attribute_rebinding_downgrades_calls_through_import_aliases(repository, assignment):
    root, service = repository
    (root / "lib.py").write_text(
        "def original(): return 'original'\n"
        "def untouched(): return 'untouched'\n"
        "class Box:\n    def original(): return 'original'\n"
    )
    member = "Box.original" if "Box" in assignment else "original"
    (root / "patches.py").write_text(
        f"import lib\ndef replacement(): return 'replacement'\n{assignment}\n"
        f"def run(): return lib.{member}(), lib.untouched()\n"
    )
    (root / "consumer.py").write_text(
        f"import lib as alias\ndef consume(): return alias.{member}()\n"
        + ("from lib import original as saved\ndef copied(): return saved()\n" if member == "original" else "")
    )
    git(root, "add", ".")
    service.sync("scope")
    for path, name in [("patches.py", "run"), ("consumer.py", "consume")]:
        fingerprint, caller = definition(service, name, path)
        callees = query(service, "callees", expected=fingerprint, symbol_id=caller["id"])
        originals = [item for item in callees.items if item["name"] == "original"]
        assert originals
        assert all(item["resolution"] == "candidate" for item in originals)
        assert all(item["resolution"] == "resolved_static" for item in callees.items if item["name"] == "untouched")
    if member == "original":
        _, caller = definition(service, "copied", "consumer.py")
        assert all(
            item["resolution"] == "candidate"
            for item in query(service, "callees", expected=fingerprint, symbol_id=caller["id"]).items
        )


def test_depth_limited_test_search_reports_partial_only_with_remaining_work(repository):
    root, service = repository
    (root / "step0.py").write_text("def step0(): return 1\n")
    for index in range(1, 8):
        (root / f"step{index}.py").write_text(
            f"from step{index - 1} import step{index - 1}\ndef step{index}(): return step{index - 1}()\n"
        )
    (root / "tests/test_chain.py").write_text("from step7 import step7\ndef test_chain(): assert step7() == 1\n")
    (root / "cycle.py").write_text("def first(): return second()\ndef second(): return first()\n")
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, leaf = definition(service, "step0", "step0.py")
    tests = query(service, "affected_tests", expected=fingerprint, paths=["step0.py"])
    assert tests.items == []
    assert tests.status == "partial"
    assert "depth_limit" in tests.limitations
    impact = query(service, "impact", expected=fingerprint, symbol_id=leaf["id"], depth=1)
    assert impact.status == "partial"
    assert "depth_limit" in impact.limitations
    nearby = query(service, "affected_tests", expected=fingerprint, paths=["step6.py"])
    assert any(item["path"] == "tests/test_chain.py" for item in nearby.items)
    assert nearby.status == "ok"
    assert "depth_limit" not in nearby.limitations
    _, cycle = definition(service, "first", "cycle.py")
    complete_cycle = query(service, "impact", expected=fingerprint, symbol_id=cycle["id"], depth=1)
    assert complete_cycle.status == "ok"
    assert "depth_limit" not in complete_cycle.limitations
    scoped = query(service, "impact", expected=fingerprint, symbol_id=leaf["id"], depth=1, path_prefix="step0.py")
    assert scoped.status == "ok"
    assert "depth_limit" not in scoped.limitations


def test_large_changes_response_obeys_elapsed_and_byte_budgets(repository):
    root, service = repository
    for index in range(5000):
        (root / f"note{index:04}.md").write_text("# Small change\n")
    git(root, "add", ".")
    service.sync("scope")
    started = time.monotonic()
    result = query(service, "changes")
    elapsed = time.monotonic() - started
    assert elapsed < service.config.limits.query_seconds + 1
    assert len(result.model_dump_json(by_alias=True).encode()) <= 16000
    assert result.items
    assert result.status == "partial"
    assert "output_budget" in result.limitations


def test_corrupt_database_fails_closed_and_sync_repairs(repository):
    _, service = repository
    databases = list(service.config.cache_dir.glob("*/generation-*/graph.sqlite"))
    assert len(databases) == 1
    databases[0].write_bytes(b"corrupt")
    with pytest.raises(CodeError, match="index_integrity_failed"):
        query(service, "symbols", query="fit")
    service.sync("scope")
    assert definition(service, "fit")[1]["name"] == "fit"


def test_corrupt_facts_are_reextracted_on_unchanged_sync(repository):
    _, service = repository
    facts = list(service.config.cache_dir.glob("*/generation-*/facts/*.json"))
    facts[0].write_bytes(b"corrupt")
    result = service.sync("scope")
    assert result["extracted_files"] == 1
    assert service.status("scope").freshness == "fresh"


def test_branch_switch_invalidates_changes_baseline(repository):
    root, service = repository
    git(root, "switch", "-c", "another-branch")
    service.sync("scope")
    with pytest.raises(CodeError, match="baseline_unavailable"):
        query(service, "changes")


def test_partial_syntax_never_claims_complete_static_resolution(repository):
    root, service = repository
    (root / "src/sample/broken.py").write_text("from .budget import fit\ndef broken():\n    fit(1)\n    invalid = (\n")
    git(root, "add", ".")
    service.sync("scope")
    result = query(service, "symbols", query="broken")
    assert result.coverage["partial_files"] == 1
    fingerprint, symbol = definition(service, "fit", "src/sample/budget.py")
    callers = query(service, "callers", expected=fingerprint, symbol_id=symbol["id"])
    assert all(item["resolution"] == "candidate" for item in callers.items if item["path"].endswith("broken.py"))


def test_http_client_mcp_and_prepared_code_share_freshness(repository, tmp_path):
    import asyncio

    import httpx
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport

    from powercontext.builtin.persistence.sqlite import SQLiteConfig
    from powercontext.builtin.runtime import BuiltinConfig, open_builtin_runtime
    from powercontext.builtin.scope import ScopeDraft
    from powercontext.client import PowerContextClient
    from powercontext.http import CodeQueryRequest as TransportQuery
    from powercontext.server.factory import create_server_app
    from powercontext.server.settings import ServerSettings

    root, _ = repository
    code = CodeConfig(enabled=True, cache_dir=tmp_path / "http-cache")
    app = create_server_app(
        settings=ServerSettings(
            database=SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'http.db'}"),
            code=code,
        )
    )

    def http_client(headers=None, timeout=None, auth=None, **_):
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://127.0.0.1",
            headers=headers,
            timeout=timeout,
            auth=auth,
            follow_redirects=True,
        )

    async def exercise():
        nonlocal app
        database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'http.db'}")
        async with open_builtin_runtime(
            BuiltinConfig(database=database), scheduler_path=tmp_path / "scheduler.db"
        ) as runtime:
            assert runtime.scopes is not None
            scope = await runtime.scopes.create(
                ScopeDraft(title="Code fixture", summary="Native acceptance", idempotency_key="code-test")
            )
            scope_id = scope.scope_id
        configured = code.model_copy(update={"repositories": {scope_id: CodeRepositoryConfig(root=root)}})
        await asyncio.to_thread(CodeService(configured).index, scope_id)
        app = create_server_app(settings=ServerSettings(database=database, code=configured))
        transport = StreamableHttpTransport("http://127.0.0.1/mcp/", httpx_client_factory=http_client)
        async with app.router.lifespan_context(app), http_client() as http, Client(transport) as mcp:
            request = {"operation": {"kind": "symbols", "query": "prepare"}}
            async with PowerContextClient("http://127.0.0.1", http_client=http) as sdk:
                result = await sdk.query_code(scope_id, TransportQuery.model_validate(request))
            from powercontext.http import CodeQueryResult as TransportResult

            assert isinstance(result.root, TransportResult) and result.root.items is not None
            assert any(item["name"] == "prepare" for item in result.root.items)
            tool = await mcp.call_tool("query_code", {"scope_id": scope_id, **request})
            assert not tool.is_error
            assert tool.structured_content["fingerprint"] == result.root.fingerprint
            prepared = await http.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope_id,
                    "query": "prepare",
                    "include_code": True,
                    "assembly": {"sections": []},
                },
            )
            prepared.raise_for_status()
            assert prepared.json()["status"] == "ready"
            assert "BEGIN_POWERCONTEXT_CODE_V1" in prepared.json()["content"]
            invalid = await http.post(
                f"/v1/scopes/{scope_id}/code/query",
                json={
                    "operation": {
                        "kind": "read",
                        "path": "../outside",
                        "file_sha256": "a" * 64,
                        "start_line": 1,
                        "end_line": 1,
                    },
                },
            )
            assert invalid.status_code == 422
            (root / "src/sample/api.py").write_text("def changed(): pass\n")
            stale = await http.post(f"/v1/scopes/{scope_id}/code/query", json=request)
            assert stale.status_code == 409
            assert stale.json()["error"]["code"] == "code_changed"
            fallback = await http.post(
                "/v1/context/prepare",
                json={
                    "scope_id": scope_id,
                    "query": "prepare",
                    "include_code": True,
                    "assembly": {"sections": []},
                },
            )
            assert fallback.json()["status"] == "empty"

    asyncio.run(exercise())


def test_large_file_line_positions_do_not_crash_native_parser(repository):
    root, service = repository
    content = "# padding\r\n" * 300 + "def deep():\r\n    return '精确行号'\r\n"
    (root / "src/sample/large.py").write_bytes(content.encode())
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, symbol = definition(service, "deep")
    assert symbol["start_line"] == 301
    assert symbol["end_line"] == 302
    result = query(
        service,
        "read",
        expected=fingerprint,
        path=symbol["path"],
        file_sha256=symbol["file_sha256"],
        start_line=301,
        end_line=302,
    )
    assert result.items[0]["content"] == "def deep():\r\n    return '精确行号'\r\n"


def test_index_does_not_execute_git_clean_filters(repository, tmp_path):
    import shlex

    root, service = repository
    (root / ".gitattributes").write_text("*.py filter=sentinel\n")
    git(root, "add", ".gitattributes")
    marker = tmp_path / "filter-ran"
    git(root, "config", "filter.sentinel.clean", f"touch {shlex.quote(str(marker))}; cat")
    (root / "src/sample/api.py").write_text("def changed(): pass\n")
    service.sync("scope")
    assert service.status("scope").freshness == "fresh"
    assert not marker.exists()


def test_cache_reader_keeps_generation_until_release(repository):
    import time

    from powercontext.builtin.code.cache import GenerationCache

    root, service = repository
    directory = next(service.config.cache_dir.iterdir())
    cache = GenerationCache(directory)
    deadline = time.monotonic() + 30
    with cache.pin(deadline) as (old, _):
        for number in range(3):
            (root / "src/sample/api.py").write_text(f"def changed(): return {number}\n")
            service.sync("scope")
        assert old.source("src/sample/api.py", deadline).startswith(b"from . import")
        assert old.directory.exists()
    cache.collect(cache.pointer(), deadline)
    assert not old.directory.exists()


def test_parser_timeout_is_visible_and_never_publishes_false_edges(repository):
    from powercontext.builtin.code import CodeLimits

    _, service = repository
    constrained = CodeService(service.config.model_copy(update={"limits": CodeLimits(parse_seconds=0.000001)}))
    constrained.index("scope", full=True)
    result = query(constrained, "symbols", query="fit")
    assert result.coverage["failed_files"] > 0
    assert not any(item["kind"] == "function" for item in result.items)


def test_corrupt_cache_pointer_and_build_status_recover_on_sync(repository):
    _, service = repository
    directory = next(service.config.cache_dir.iterdir())
    (directory / "current.json").write_text('{"current": []}')
    assert service.status("scope").reason == "index_integrity_failed"
    with pytest.raises(CodeError, match="index_integrity_failed"):
        query(service, "symbols", query="fit")
    (directory / "last-build.json").write_text("invalid json")
    assert service.status("scope").status == "failed"
    service.sync("scope")
    assert service.status("scope").freshness == "fresh"


def test_unicode_separators_do_not_change_source_ranges(repository):
    root, service = repository
    content = 'def text():\r\n    return "before\u2028after"\r\n'
    (root / "src/sample/lines.py").write_bytes(content.encode())
    git(root, "add", ".")
    service.sync("scope")
    fingerprint, symbol = definition(service, "text")
    result = query(
        service,
        "read",
        expected=fingerprint,
        path=symbol["path"],
        file_sha256=symbol["file_sha256"],
        start_line=1,
        end_line=2,
    )
    assert result.items[0]["content"] == content
    assert result.items[0]["end_line"] == 2


@pytest.mark.parametrize("backend", ["builtin", "casbin"])
def test_scope_code_authorization_and_revocation(repository, tmp_path, backend):
    import asyncio

    from powercontext.builtin.code.application import CodeApplication
    from tests.e2e.test_access_control_regressions import _grant, _scope, _server

    root, service = repository

    async def scenario():
        async with _server(tmp_path, backend) as (app, client, _):
            scope_id = await _scope(client)
            binding = await _grant(client, scope_id, "reader", "scope.viewer")
            native = CodeService(
                service.config.model_copy(update={"repositories": {scope_id: CodeRepositoryConfig(root=root)}})
            )
            await asyncio.to_thread(native.index, scope_id)
            app.state.application.code = CodeApplication(app.state.application, native)
            path = f"/v1/scopes/{scope_id}/code/query"
            payload = {"operation": {"kind": "symbols", "query": "prepare"}}
            reader = {"Authorization": "Bearer reader"}
            accepted = await client.post(path, headers=reader, json=payload)
            assert accepted.status_code == 200, accepted.text
            assert any(item["name"] == "prepare" for item in accepted.json()["items"])
            stranger = {"Authorization": "Bearer stranger"}
            denied = await client.post(path, headers=stranger, json=payload)
            absent = await client.post("/v1/scopes/absent-scope/code/query", headers=stranger, json=payload)
            assert denied.status_code == absent.status_code == 403
            assert denied.json()["error"] == absent.json()["error"]
            revoked = await client.post(
                "/v1/access/bindings/revoke",
                json={
                    "binding_id": binding["binding_id"],
                    "expected_version": binding["version"],
                    "idempotency_key": "revoke-native-reader",
                },
            )
            assert revoked.status_code == 200, revoked.text
            blocked = await client.post(path, headers=reader, json=payload)
            assert blocked.status_code == 403
            assert "src/sample" not in blocked.text

    asyncio.run(scenario())


def test_incremental_and_full_build_deliver_equivalent_relationships(repository):
    root, service = repository
    (root / "src/sample/other.py").unlink()
    (root / "src/sample/api.py").write_text(
        "from .budget import fit as changed\n\ndef prepare(value):\n    return changed(value)\n"
    )
    service.sync("scope")

    def observable():
        fingerprint, target = definition(service, "fit", "src/sample/budget.py")
        callers = query(service, "callers", expected=fingerprint, symbol_id=target["id"])
        tests = query(service, "affected_tests", expected=fingerprint, paths=["src/sample/budget.py"])
        return [
            (
                result.operation,
                [(item["path"], item["name"], item.get("resolution"), item.get("content")) for item in result.items],
            )
            for result in (callers, tests)
        ]

    incremental = observable()
    service.index("scope", full=True)
    assert observable() == incremental


def test_failed_rebuild_preserves_published_generation(repository):
    from powercontext.builtin.code import CodeLimits

    _, service = repository
    before = query(service, "symbols", query="prepare")
    constrained = CodeService(service.config.model_copy(update={"limits": CodeLimits(build_seconds=0.000001)}))
    with pytest.raises(CodeError):
        constrained.index("scope", full=True)
    after = query(service, "symbols", query="prepare")
    assert after.fingerprint == before.fingerprint
    assert after.items == before.items
    status = service.status("scope")
    assert status.freshness == "fresh"
    assert status.last_build is not None
    assert status.last_build["status"] == "failed"


@pytest.mark.parametrize("progress", ["unexpected output", "begin:not-an-index"])
def test_malformed_parser_progress_reports_parser_failure(repository, monkeypatch, progress):
    import sys

    from powercontext.builtin.code import process

    _, service = repository
    before = service.status("scope").fingerprint
    popen = subprocess.Popen

    def malformed_worker(arguments, **kwargs):
        # Keep Git processes intact and replace only the parser's stdout producer.
        if arguments[1:3] == ["-m", "powercontext.builtin.code.worker"]:
            arguments = [sys.executable, "-c", f"print({progress!r}, flush=True)"]
        return popen(arguments, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(process.subprocess, "Popen", malformed_worker)
        with pytest.raises(CodeError, match="code_parser_failed"):
            service.index("scope", full=True)
    status = service.status("scope")
    assert status.status == "ready"
    assert status.fingerprint == before
    assert status.last_build["reason"] == "code_parser_failed"


def test_fifo_cache_corruption_fails_without_blocking(repository):
    _, service = repository
    directory = next(service.config.cache_dir.iterdir())
    pointer = directory / "current.json"
    pointer.unlink()
    os.mkfifo(pointer)
    assert service.status("scope").reason == "index_integrity_failed"


def test_explore_reports_truncation_when_combined_neighbors_exceed_limit(repository):
    root, service = repository
    helpers = "\n".join(f"def helper_{index}():\n    return {index}\n" for index in range(9))
    central = "def central():\n" + "".join(f"    helper_{index}()\n" for index in range(9))
    callers = "\n".join(f"def caller_{index}():\n    central()\n" for index in range(9))
    (root / "src/sample/fan.py").write_text(helpers + central + callers)
    git(root, "add", "src/sample/fan.py")
    service.sync("scope")

    result = query(service, "explore", query="central", budget=32768)

    assert len(result.items) == 16
    assert result.status == "partial"
    assert "item_limit" in result.limitations
