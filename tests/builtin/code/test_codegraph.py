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

"""Behavior checks against an explicitly deployed, real CodeGraph engine."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from time import monotonic

import pytest

from powercontext.builtin.code.adapter import CodeGraphAdapter
from powercontext.builtin.code.config import CodeGraphConfig
from powercontext.builtin.code.errors import UnsupportedCodeCapabilityError
from powercontext.builtin.code.models import CodeAffectedTestsOperation, CodeSymbolsOperation, CodeTargetOperation

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def adapter() -> CodeGraphAdapter:
    executable = os.environ.get("POWERCONTEXT_TEST_CODEGRAPH_EXECUTABLE")
    if not executable:
        pytest.skip("Set POWERCONTEXT_TEST_CODEGRAPH_EXECUTABLE to run the real CodeGraph acceptance checks")
    return CodeGraphAdapter(CodeGraphConfig(executable=executable))


def write_sources(root: Path, sources: dict[str, str]) -> None:
    for name, content in sources.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content)


def index_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted((root / ".codegraph").rglob("*")):
        if path.is_file():
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


async def test_same_name_definitions_remain_distinct_and_unsafe_relationships_fail(
    adapter: CodeGraphAdapter, tmp_path: Path
) -> None:
    write_sources(
        tmp_path,
        {
            "alpha.py": "def shared(value):\n    return value + 1\n",
            "beta.py": "def shared(value):\n    return value * 2\n",
            "test_alpha.py": "from alpha import shared\n\ndef test_alpha():\n    assert shared(1) == 2\n",
            "tests/nested/beta_test.py": "from beta import shared\n\ndef test_beta():\n    assert shared(2) == 4\n",
        },
    )
    identity = await adapter.identity(deadline=monotonic() + 10)
    await adapter.build(tmp_path, identity=identity, deadline=monotonic() + 60)
    before = index_digest(tmp_path)
    result = await adapter.query(
        tmp_path,
        CodeSymbolsOperation(kind="symbols", query="shared"),
        identity=identity,
        deadline=monotonic() + 5,
        max_cache_bytes=10 * 1024 * 1024,
    )
    assert {item["path"] for item in result["items"]} == {"alpha.py", "beta.py"}
    for path in ("alpha.py", "beta.py"):
        with pytest.raises(UnsupportedCodeCapabilityError, match="unsupported_capability"):
            await adapter.query(
                tmp_path,
                CodeTargetOperation(kind="callers", path=path, qualified_name="shared", start_line=1),
                identity=identity,
                deadline=monotonic() + 5,
                max_cache_bytes=10 * 1024 * 1024,
            )
    assert index_digest(tmp_path) == before


async def test_symbol_file_filter_applies_before_result_limit(adapter: CodeGraphAdapter, tmp_path: Path) -> None:
    write_sources(
        tmp_path,
        {
            "alpha.py": "def shared(value):\n    return value + 1\n",
            "beta.py": "def shared(value):\n    return value * 2\n",
        },
    )
    identity = await adapter.identity(deadline=monotonic() + 10)
    await adapter.build(tmp_path, identity=identity, deadline=monotonic() + 60)
    for path in ("alpha.py", "beta.py"):
        result = await adapter.query(
            tmp_path,
            CodeSymbolsOperation(kind="symbols", query="shared", path=path, limit=1),
            identity=identity,
            deadline=monotonic() + 5,
            max_cache_bytes=10 * 1024 * 1024,
        )
        assert [item["path"] for item in result["items"]] == [path]


async def test_affected_tests_retain_changed_test_starting_nodes(adapter: CodeGraphAdapter, tmp_path: Path) -> None:
    write_sources(
        tmp_path,
        {
            "budget.py": "def allocate_budget(total):\n    return total // 2\n",
            "test_budget.py": "from budget import allocate_budget\n\ndef test_budget():\n    assert allocate_budget(8) == 4\n",
        },
    )
    identity = await adapter.identity(deadline=monotonic() + 10)
    await adapter.build(tmp_path, identity=identity, deadline=monotonic() + 60)
    for changed in (("budget.py",), ("budget.py", "test_budget.py"), ("test_budget.py", "budget.py")):
        result = await adapter.query(
            tmp_path,
            CodeAffectedTestsOperation(kind="affected_tests", changed_paths=changed),
            identity=identity,
            deadline=monotonic() + 5,
            max_cache_bytes=10 * 1024 * 1024,
        )
        assert [item["path"] for item in result["items"]] == ["test_budget.py"]
        assert result["items"][0]["relationships"][0]["target"]["path"] == "budget.py"
        assert not result["truncated"]


async def test_real_callers_include_root_and_nested_pytest_files_without_import_edges(
    adapter: CodeGraphAdapter, tmp_path: Path
) -> None:
    write_sources(
        tmp_path,
        {
            "helpers.py": "def choose_target(value):\n    return value + 1\n",
            "test_helpers.py": "from helpers import choose_target\n\ndef test_root():\n    assert choose_target(1) == 2\n",
            "tests/nested/helpers_test.py": "from helpers import choose_target\n\ndef test_nested():\n    assert choose_target(2) == 3\n",
        },
    )
    identity = await adapter.identity(deadline=monotonic() + 10)
    build = await adapter.build(tmp_path, identity=identity, deadline=monotonic() + 60)
    assert build["indexed_files"] == 3
    before = index_digest(tmp_path)
    result = await adapter.query(
        tmp_path,
        CodeTargetOperation(kind="callers", path="helpers.py", qualified_name="choose_target", start_line=1),
        identity=identity,
        deadline=monotonic() + 5,
        max_cache_bytes=10 * 1024 * 1024,
    )
    assert {item["path"] for item in result["items"]} == {"test_helpers.py", "tests/nested/helpers_test.py"}
    assert {item["location"]["qualified_name"] for item in result["items"]} == {"test_root", "test_nested"}
    assert all(item["relationships"][0]["kind"] == "calls" for item in result["items"])
    assert all(item["relationships"][0]["call_line"] == 4 for item in result["items"])
    assert index_digest(tmp_path) == before
