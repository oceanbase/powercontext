# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Mixed repository evidence through the same public index/query workflow."""

from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from powercontext.builtin.code import CodeConfig, CodeError, CodeQueryRequest, CodeRepositoryConfig, CodeService

for grammar in ("tree_sitter_python", "tree_sitter_javascript", "tree_sitter_typescript", "tree_sitter_go"):
    pytest.importorskip(grammar)

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


def git(root, *arguments):
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init")
    service = CodeService(
        CodeConfig(enabled=True, cache_dir=tmp_path / "cache", repositories={"scope": CodeRepositoryConfig(root=root)})
    )

    def build(files):
        for path, content in files.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content.encode("utf-8"))
        git(root, "add", ".")
        git(root, "-c", "user.name=Code Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
        service.index("scope")
        return root, service

    return build


def query(service, kind, *, expected=None, before=None, **arguments):
    return service.query(
        "scope",
        CodeQueryRequest.model_validate({
            "operation": {"kind": kind, **arguments},
            "expected_fingerprint": expected,
            "before_fingerprint": before,
        }),
    )


def symbol(service, name, path):
    result = query(service, "symbols", query=name, path_prefix=path)
    return result.fingerprint, next(item for item in result.items if item["name"] == name and item["kind"] != "file")


def callers(service, name, path):
    fingerprint, item = symbol(service, name, path)
    return query(service, "callers", expected=fingerprint, symbol_id=item["id"])


def test_mixed_languages_share_index_citations_and_test_discovery(repository):
    _, service = repository({
        "prepare.py": "def prepare():\n    return '预算'\n",
        "web/budget.ts": "export const fit = (value: string): string => value;\r\n",
        "web/api.tsx": 'import {fit as trim} from "./budget.js";\r\nexport function prepare() { return <div>{trim("预算")}</div>; }\r\n',
        "web/api.test.ts": 'import {prepare} from "./api";\nexport function testPrepare() { return prepare(); }\n',
        "web/view.jsx": "export const render = () => <div>预算</div>;\n",
        "web/client.mjs": 'import {prepare} from "./api";\nexport function boot() { return prepare(); }\n',
        "web/legacy.cjs": "function legacy() { return 1; }\n",
        "web/types.mts": "export interface Request { value: string }\n",
        "web/types.cts": "type Response = string;\n",
        "go.mod": "module example.com/project\n\ngo 1.23\n",
        "budget/fit.go": "package budget\nfunc Fit(value string) string { return value }\n",
        "api/api.go": 'package api\nimport trim "example.com/project/budget"\nfunc Prepare() string { return trim.Fit("预算") }\n',
        "api/api_test.go": "package api\nfunc TestPrepare() { Prepare() }\n",
        "notes.txt": "prepare reference\n",
    })
    assert set(service.status("scope").languages) == {"python", "javascript", "typescript", "go"}
    result = query(service, "symbols", query="prepare")
    assert {item["language"] for item in result.items} >= {"python", "typescript", "go"}
    assert result.coverage["failed_files"] == 0
    assert result.coverage["partial_files"] == 0
    assert result.coverage["languages"]["javascript"]["ok"] == 3
    assert result.coverage["languages"]["typescript"]["ok"] == 5
    assert result.coverage["languages"]["go"]["ok"] == 3
    for name, path in (("fit", "web/budget.ts"), ("Fit", "budget/fit.go")):
        result = callers(service, name, path)
        assert {item["name"] for item in result.items} in ({"prepare"}, {"Prepare"})
        assert all(item["resolution"] == "resolved_static" for item in result.items)
        tests = query(service, "affected_tests", expected=result.fingerprint, paths=[path])
        assert any(
            item["path"].endswith(("api.test.ts", "api_test.go")) and item["witness_path"] for item in tests.items
        )
    fingerprint, item = symbol(service, "prepare", "web/api.tsx")
    evidence = query(
        service,
        "read",
        expected=fingerprint,
        path=item["path"],
        file_sha256=item["file_sha256"],
        start_line=item["start_line"],
        end_line=item["end_line"],
    )
    assert 'trim("预算")' in evidence.items[0]["content"]
    assert evidence.items[0]["content"].endswith("\r\n")
    assert callers(service, "prepare", "prepare.py").items == []


def test_esm_exports_scopes_and_mutations_do_not_invent_static_calls(repository):
    _, service = repository({
        "leaf.ts": "export function leaf() {}\nfunction privateFn() {}\nexport default () => leaf();\n",
        "barrel.ts": 'export {leaf as exposed} from "./leaf";\n',
        "api.js": """import fallback, {leaf as direct, privateFn} from "./leaf";
import {exposed} from "./barrel";
import * as ns from "./leaf";
export function real() { direct(); exposed(); ns.leaf(); fallback(); privateFn(); }
export function shadow(direct) { direct(); }
export function destructured({leaf: direct}) { direct(); }
export function blocked() { let direct; direct(); }
export function caught() { try {} catch(direct) { direct(); } }
export function named() { const fn = function direct() {}; direct(); }
const object = { direct() {} };
export function outside() { direct(); }
""",
        "mutation.ts": "export function changed() {}\nchanged = replacement;\nexport function caller() { changed(); }\n",
        "types.ts": 'import type {leaf} from "./leaf";\nexport function invalid() { leaf(); }\n',
    })
    result = callers(service, "leaf", "leaf.ts")
    names = {item["name"] for item in result.items}
    assert {"real", "named", "outside"} <= names
    assert not names & {"shadow", "destructured", "blocked", "caught", "invalid"}
    assert callers(service, "privateFn", "leaf.ts").items == []
    changed = callers(service, "changed", "mutation.ts")
    assert [(item["name"], item["resolution"]) for item in changed.items] == [("caller", "candidate")]
    fingerprint, fn = symbol(service, "real", "api.js")
    callees = query(service, "callees", expected=fingerprint, symbol_id=fn["id"])
    assert any(
        item["name"].startswith("<anonymous") and item["resolution"] == "resolved_static" for item in callees.items
    )


def test_go_packages_aliases_build_variants_and_shadowing(repository):
    _, service = repository({
        "go.mod": "module example.org/project\n",
        "helpers/util.go": "package utilities\nfunc Work() {}\nfunc hidden() {}\n",
        "api/a.go": """package api
import "example.org/project/helpers"
func Prepare() { utilities.Work(); Helper() }
func Shadow(utilities interface{ Work() }) { utilities.Work() }
func Local() { Helper := func() {}; Helper() }
func Unexported() { utilities.hidden() }
type Client struct {}
func (c *Client) Run() { Helper() }
""",
        "api/b.go": "package api\nfunc Helper() {}\n",
        "api/platform_linux.go": "package api\nfunc Platform() {}\n",
        "api/platform_windows.go": "package api\nfunc Platform() {}\n",
        "api/use.go": "package api\nfunc Use() { Platform() }\n",
    })
    result = callers(service, "Work", "helpers/util.go")
    assert [(item["name"], item["resolution"]) for item in result.items] == [("Prepare", "resolved_static")]
    assert callers(service, "hidden", "helpers/util.go").items == []
    assert {item["name"] for item in callers(service, "Helper", "api/b.go").items} == {"Prepare", "Run"}
    for path in ("api/platform_linux.go", "api/platform_windows.go"):
        result = callers(service, "Platform", path)
        assert [(item["name"], item["resolution"]) for item in result.items] == [("Use", "candidate")]


def test_incremental_mixed_index_matches_rebuild_and_rejects_stale_evidence(repository):
    root, service = repository({
        "leaf.ts": "export function leaf() {}\n",
        "api.js": 'import {leaf} from "./leaf";\nexport function run() { leaf(); }\n',
        "prepare.py": "def prepare(): pass\n",
        "go.mod": "module example.org/project\n",
        "go.go": "package sample\nfunc Prepare() {}\n",
    })
    before, item = symbol(service, "leaf", "leaf.ts")
    (root / "leaf.ts").write_text("export function leaf() { return 2; }\n")
    with pytest.raises(CodeError, match="code_changed"):
        query(service, "callees", expected=before, symbol_id=item["id"])
    service.sync("scope")
    changed = callers(service, "leaf", "leaf.ts")
    assert service.status("scope").last_build["extracted_files"] == 1
    assert changed.fingerprint != before
    service.index("scope", full=True)
    rebuilt = callers(service, "leaf", "leaf.ts")
    assert rebuilt.items == changed.items
    current = rebuilt.fingerprint
    (root / "leaf.ts").unlink()
    service.sync("scope")
    result = query(
        service, "impact_changes", expected=service.status("scope").fingerprint, before=current, paths=["leaf.ts"]
    )
    assert any(item["path"] == "api.js" for item in result.items)
    assert query(service, "symbols", query="leaf", path_prefix="leaf.ts").items == []


@pytest.mark.parametrize("path", ["thing.test.ts", "thing.spec.jsx", "__tests__/thing.js", "thing_test.go"])
def test_test_filename_hints_remain_candidates(repository, path):
    source = "thing.go" if path.endswith(".go") else "thing.ts"
    content = "package example\n" if source.endswith(".go") else "// declarations only\n"
    _, service = repository({source: content, path: content})
    result = query(service, "affected_tests", expected=service.status("scope").fingerprint, paths=[source])
    assert [(item["path"], item["resolution"], item["witness_path"]) for item in result.items] == [
        (path, "candidate", [])
    ]


def test_ambiguous_modules_type_exports_and_unsupported_syntax_are_conservative(repository):
    _, service = repository({
        "choice.ts": "export function choose() {}\n",
        "choice.js": "export function choose() {}\n",
        "use.js": 'import {choose} from "./choice";\nexport function run() { choose(); }\n',
        "type-barrel.ts": 'export type {choose} from "./choice.ts";\n',
        "type-use.ts": 'import {choose} from "./type-barrel";\nexport function invalid() { choose(); }\n',
        "bad.ts": "export function useful() {}\nexport function broken( {\n",
        "bad-use.ts": 'import {useful} from "./bad";\nexport function notCertain() { useful(); }\n',
        "cpp.cpp": "void choose() {}\n",
    })
    for path in ("choice.ts", "choice.js"):
        result = callers(service, "choose", path)
        assert [(item["name"], item["resolution"]) for item in result.items] == [("run", "candidate")]
    result = query(service, "symbols", query="useful", path_prefix="bad.ts")
    assert any(item["name"] == "useful" for item in result.items)
    assert result.coverage["languages"]["typescript"]["partial"] == 1
    assert not any(item["resolution"] == "resolved_static" for item in callers(service, "useful", "bad.ts").items)
    assert result.coverage["unsupported_files"] == 1
    with pytest.raises(CodeError, match="unsupported_capability"):
        query(service, "affected_tests", expected=result.fingerprint, paths=["cpp.cpp"])


def test_go_nested_module_and_test_packages_do_not_leak_into_imports(repository):
    _, service = repository({
        "go.mod": "module example.org/root\n",
        "root.go": 'package root\nimport "example.org/child"\nfunc Run() { child.Work() }\n',
        "child/go.mod": "module example.org/child\n",
        "child/work.go": "package child\nfunc Work() {}\n",
        "child/work_test.go": "package child\nfunc TestWork() { Work() }\nfunc OnlyTest() {}\n",
        "child/use.go": "package child\nfunc Invalid() { OnlyTest() }\n",
    })
    assert {item["name"] for item in callers(service, "Work", "child/work.go").items} == {"TestWork"}
    assert callers(service, "OnlyTest", "child/work_test.go").items == []


@pytest.mark.parametrize("extension", ["js", "ts"])
@pytest.mark.parametrize(
    "member",
    [
        "static run = replacement;",
        "static { this.run = replacement; }",
        "static get run() { return replacement; }",
        "static [unknown]() {}",
    ],
)
def test_class_member_overrides_cannot_prove_a_method_call(repository, extension, member):
    path = f"class.{extension}"
    _, service = repository({
        path: f"class Client {{ static run() {{}} {member} }}\nfunction caller() {{ Client.run(); }}\n"
    })
    result = callers(service, "run", path)
    assert not any(item["resolution"] == "resolved_static" for item in result.items)


@pytest.mark.parametrize("extension", ["js", "ts", "tsx", "go"])
def test_long_source_positions_keep_worker_alive_and_round_trip_citations(repository, extension):
    prefix = "package sample\n" if extension == "go" else ""
    declarations = [
        f"func Step{number}() int {{ return {number} }}"
        if extension == "go"
        else f"export const Step{number} = () => {number};"
        for number in range(20)
    ]
    content = prefix + "\n" * 300 + "".join(" " * 300 + declaration + "\r\n" for declaration in declarations)
    path = f"long.{extension}"
    _, service = repository({path: content})
    result = query(service, "symbols", query="Step19")
    assert result.coverage["failed_files"] == 0
    item = next(item for item in result.items if item["name"] == "Step19")
    assert item["start_line"] == 320 + bool(prefix)
    read = query(
        service,
        "read",
        expected=result.fingerprint,
        path=path,
        file_sha256=item["file_sha256"],
        start_line=item["start_line"],
        end_line=item["end_line"],
    )
    assert declarations[-1] in read.items[0]["content"]
    assert read.items[0]["content"].endswith("\r\n")
