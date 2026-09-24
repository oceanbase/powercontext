# Copyright (c) 2026 OceanBase.
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
# Unless required by applicable law or agreed to in writing, software distributed
# under the License is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR
# CONDITIONS OF ANY KIND, either express or implied. See the License for the
# specific language governing permissions and limitations under the License.

"""Python binding uncertainty through public repository queries."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

import pytest

from powercontext.builtin.code import CodeConfig, CodeQueryRequest, CodeRepositoryConfig, CodeService

pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_python")

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="native code indexing requires POSIX file locks and resource limits"
)


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repository"
    root.mkdir()
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), "init"], check=True, capture_output=True)
    service = CodeService(
        CodeConfig(enabled=True, cache_dir=tmp_path / "cache", repositories={"scope": CodeRepositoryConfig(root=root)})
    )

    def build(files):
        for path, content in files.items():
            (root / path).write_text(content, encoding="utf-8")
        subprocess.run([executable, "-C", str(root), "add", "."], check=True, capture_output=True)
        service.index("scope")
        return root, service

    return build


def query(service, kind, *, expected=None, **arguments):
    return service.query(
        "scope",
        CodeQueryRequest.model_validate({
            "operation": {"kind": kind, **arguments},
            "expected_fingerprint": expected,
        }),
    )


def callees(service, name, path):
    symbols = query(service, "symbols", query=name, path_prefix=path)
    caller = next(item for item in symbols.items if item["name"] == name and item["kind"] != "file")
    return query(service, "callees", expected=symbols.fingerprint, symbol_id=caller["id"])


@pytest.mark.parametrize("binding", ["def target(): return 'original'", "from original import target"])
def test_wildcard_import_downgrades_overwritten_bindings_and_reexports(repository, binding):
    root, service = repository({
        "lib.py": "def target(): return 'replacement'\n",
        "original.py": "def target(): return 'original'\n",
        "shadow.py": f"{binding}\nfrom lib import *\ndef caller(): return target()\n",
        "consumer.py": (
            "import shadow\n"
            "from shadow import target as copied\n"
            "def through_module(): return shadow.target()\n"
            "def through_reexport(): return copied()\n"
        ),
    })
    actual = subprocess.run(
        [sys.executable, "-c", "from shadow import caller; print(caller())"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    )
    assert actual.stdout.strip() == "replacement"
    for name, path in (
        ("caller", "shadow.py"),
        ("through_module", "consumer.py"),
        ("through_reexport", "consumer.py"),
    ):
        result = callees(service, name, path)
        assert result.items
        assert all(item["resolution"] == "candidate" for item in result.items)


def test_wildcard_import_does_not_weaken_function_local_bindings(repository):
    _, service = repository({
        "lib.py": "def target(): return 'replacement'\n",
        "local.py": ("from lib import *\ndef caller():\n    def target(): return 'local'\n    return target()\n"),
    })
    result = callees(service, "caller", "local.py")
    assert [(item["qualified_name"], item["resolution"]) for item in result.items] == [
        ("caller.target", "resolved_static")
    ]
