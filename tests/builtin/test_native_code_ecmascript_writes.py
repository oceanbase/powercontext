# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Assignment patterns must not turn mutated declarations into certain callees."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from powercontext.builtin.code import CodeConfig, CodeQueryRequest, CodeRepositoryConfig, CodeService

pytest.importorskip("tree_sitter_javascript")
pytest.importorskip("tree_sitter_typescript")
pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


@pytest.fixture
def indexed_repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), "init"], check=True, capture_output=True)

    def build(files):
        for path, content in files.items():
            (root / path).write_text(content, encoding="utf-8")
        subprocess.run([executable, "-C", str(root), "add", "."], check=True, capture_output=True)
        service = CodeService(
            CodeConfig(
                enabled=True, cache_dir=tmp_path / "cache", repositories={"scope": CodeRepositoryConfig(root=root)}
            )
        )
        service.index("scope")
        return service

    return build


def query(service, kind, **arguments):
    return service.query(
        "scope",
        CodeQueryRequest.model_validate({
            "operation": {"kind": kind, **arguments},
            "expected_fingerprint": service.status("scope").fingerprint,
        }),
    )


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_destructured_assignments_downgrade_outer_identifiers_and_members(indexed_repository, extension):
    patterns = {
        "object": "({value: TARGET} = values);",
        "array": "[TARGET] = values;",
        "nested_default": "({value: [{nested: TARGET = replacement}]} = values);",
        "array_rest": "[...TARGET] = values;",
        "object_rest": "({...TARGET} = values);",
        "for_of": "for ({value: TARGET} of values) {}",
        "for_in": "for (TARGET in values) {}",
    }
    files = {}
    for receiver, declaration in (("run", "function run() {}"), ("C.run", "class C { static run() {} }")):
        for name, pattern in patterns.items():
            files[f"{receiver}-{name}.{extension}"] = (
                f"{declaration}\n"
                f"function install(values) {{ {pattern.replace('TARGET', receiver)} {receiver}(); }}\n"
                f"function caller() {{ {receiver}(); }}\n"
            )
    for name, pattern in (("shorthand", "{run}"), ("shorthand_default", "{run = replacement}")):
        files[f"{name}.{extension}"] = (
            f"function run() {{}}\n"
            f"function install(values) {{ ({pattern} = values); run(); }}\n"
            f"function caller() {{ run(); }}\n"
        )
    service = indexed_repository(files)
    for path in files:
        symbols = query(service, "symbols", query="run", path_prefix=path)
        assert symbols.coverage["partial_files"] == 0
        assert symbols.coverage["failed_files"] == 0
        target = next(item for item in symbols.items if item["name"] == "run")
        callers = query(service, "callers", symbol_id=target["id"])
        assert {(item["name"], item["resolution"]) for item in callers.items} == {
            ("install", "candidate"),
            ("caller", "candidate"),
        }, path
        for caller in callers.items:
            callees = query(service, "callees", symbol_id=caller["id"])
            assert [(item["id"], item["resolution"]) for item in callees.items] == [(target["id"], "candidate")]


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_destructuring_declarations_and_default_values_preserve_outer_bindings(indexed_repository, extension):
    service = indexed_repository({
        f"declarations.{extension}": """function run() {}
function replacement() {}
function caller() { run(); replacement(); }
function declared(values) { const {value: [run = replacement]} = values; run(); }
function parameter({run = replacement}) { run(); }
function loop(values) { for (const {run = replacement} of values) { run(); } }
function assign(values) { let local; ({value: local = replacement} = values); }
function localMutation(values) { let run; ({value: run} = values); run(); }
""",
    })
    for name in ("run", "replacement"):
        symbols = query(service, "symbols", query=name)
        target = next(item for item in symbols.items if item["name"] == name)
        callers = query(service, "callers", symbol_id=target["id"])
        assert [(item["name"], item["resolution"]) for item in callers.items] == [("caller", "resolved_static")]


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_class_field_initializers_resolve_bare_calls_in_lexical_scope(indexed_repository, extension):
    service = indexed_repository({
        f"fields.{extension}": """function helper() {}
class C {
    static helper() {}
    static value = helper();
    arrow = () => helper();
    static invoke() { helper(); }
}
function explicit() { C.helper(); }
""",
    })
    symbols = query(service, "symbols", query="helper")
    outer = next(item for item in symbols.items if item["kind"] == "function")
    member = next(item for item in symbols.items if item["kind"] == "method")
    callers = query(service, "callers", symbol_id=outer["id"])
    assert len(callers.items) == 3
    assert {item["kind"] for item in callers.items} == {"class", "function", "method"}
    assert all(item["resolution"] == "resolved_static" for item in callers.items)
    for caller in callers.items:
        callees = query(service, "callees", symbol_id=caller["id"])
        assert [(item["id"], item["resolution"]) for item in callees.items] == [(outer["id"], "resolved_static")]
    member_callers = query(service, "callers", symbol_id=member["id"])
    assert [(item["name"], item["resolution"]) for item in member_callers.items] == [("explicit", "resolved_static")]


@pytest.mark.parametrize("extension", ["js", "ts"])
def test_named_class_expression_remains_visible_to_initializers_and_methods(indexed_repository, extension):
    service = indexed_repository({
        f"named.{extension}": """const Box = class Inner {
    static helper() {}
    static value = Inner.helper();
    arrow = () => Inner.helper();
    static invoke() { Inner.helper(); }
};
""",
    })
    symbols = query(service, "symbols", query="helper")
    target = next(item for item in symbols.items if item["name"] == "helper")
    callers = query(service, "callers", symbol_id=target["id"])
    assert len(callers.items) == 3
    assert {item["kind"] for item in callers.items} == {"class", "function", "method"}
    assert all(item["resolution"] == "resolved_static" for item in callers.items)
