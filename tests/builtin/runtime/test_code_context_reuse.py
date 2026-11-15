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

"""Exact prepared reuse of code evidence against a real repository and code index."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import pytest

from powercontext.builtin.code import CodeConfig, CodeRepositoryConfig, CodeService
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import (
    BuiltinConfig,
    ContextAssembly,
    PrepareContextRequest,
    PreparedContext,
    PreparedContextWithReuse,
    open_builtin_runtime,
)
from powercontext.builtin.runtime.context_bindings import ContextBindingError
from powercontext.builtin.runtime.context_reuse import (
    MAX_REUSE_DEPENDENCIES,
    ContextReuseChange,
    ContextReuseError,
    ContextReuseInvalidation,
    prepare_reuse,
)
from powercontext.builtin.runtime.prepared_code import CodeEvidenceRef
from powercontext.builtin.runtime.prepared_context import PreparedContextBuild
from powercontext.builtin.scope import ScopeDraft

FILES = {
    "src/sample/__init__.py": "from .budget import fit\n",
    "src/sample/budget.py": 'def fit(value):\n    """Budget bytes."""\n    return value\n',
    "src/sample/api.py": "from . import fit as trim\n\ndef prepare(value):\n    return trim(value)\n",
    "README.md": "Sample repository.\n",
}


def _git(root: Path, *arguments: str) -> None:
    executable = shutil.which("git")
    assert executable
    subprocess.run([executable, "-C", str(root), *arguments], check=True, capture_output=True)


def _repository(root: Path) -> Path:
    for path, content in FILES.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content)
    _git(root, "init")
    _git(root, "add", ".")
    _git(root, "-c", "user.name=Code Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture")
    return root


def _request() -> PrepareContextRequest:
    return PrepareContextRequest(
        query="fit prepare",
        include_reuse=True,
        include_code=True,
        assembly=ContextAssembly(sections=()),
    )


def _cited_paths(prepared: PreparedContextWithReuse) -> set[str]:
    paths = set()
    for line in (prepared.context.content or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("{"):
            citation = json.loads(stripped)
            if isinstance(citation, dict) and "file_sha256" in citation:
                paths.add(citation["path"])
    return paths


def _code_runtime(tmp_path: Path):
    """Open a runtime whose Scope is bound to an indexed repository; yields (runtime, scoped context, root)."""

    class Fixture:
        async def __aenter__(self):
            root = _repository(tmp_path / "repository")
            database = SQLiteConfig(url=f"sqlite+aiosqlite:///{tmp_path / 'runtime.db'}")
            async with open_builtin_runtime(BuiltinConfig(database=database)) as runtime:
                assert runtime.scopes is not None
                scope = await runtime.scopes.create(
                    ScopeDraft(title="Code reuse", summary="Exact code evidence", idempotency_key="code-reuse")
                )
            self.scope_id = scope.scope_id
            code = CodeConfig(
                enabled=True,
                repositories={self.scope_id: CodeRepositoryConfig(root=root)},
                cache_dir=tmp_path / "cache",
            )
            self.service = CodeService(code)
            await asyncio.to_thread(self.service.index, self.scope_id)
            self._runtime = open_builtin_runtime(BuiltinConfig(database=database, code=code))
            runtime = await self._runtime.__aenter__()
            return runtime.context.for_scope(self.scope_id), root, self

        async def __aexit__(self, *error):
            await self._runtime.__aexit__(*error)

    return Fixture()


def _code_change(change: Literal["changed", "removed"]) -> ContextReuseInvalidation:
    return ContextReuseInvalidation("complete", (ContextReuseChange("code", change),), truncated=False)


def test_code_reuse_revalidates_cited_files_only(tmp_path):
    async def scenario():
        async with _code_runtime(tmp_path) as (scoped, root, fixture):
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            cited = _cited_paths(prepared)
            assert "src/sample/budget.py" in cited
            assert "README.md" not in cited

            checked = await scoped.validate_reuse(prepared.reuse.binding)
            assert checked.content_bytes == prepared.context.content_bytes

            # An edit outside the cited files and a rebuilt index keep the proof valid.
            (root / "README.md").write_text("Sample repository, edited.\n")
            await asyncio.to_thread(fixture.service.index, fixture.scope_id, full=True)
            await scoped.validate_reuse(prepared.reuse.binding)

            # Editing a cited file invalidates it; restoring the bytes makes it valid again.
            budget = root / "src/sample/budget.py"
            budget.write_text(FILES["src/sample/budget.py"] + "# edited\n")
            with pytest.raises(ContextReuseError, match="source-changed") as edited:
                await scoped.validate_reuse(prepared.reuse.binding)
            # The explanation counts changed cited files without naming any path.
            explanation = edited.value.invalidation
            assert explanation == _code_change("changed")
            assert explanation is not None
            assert "budget" not in explanation.to_json()
            budget.write_text(FILES["src/sample/budget.py"])
            await scoped.validate_reuse(prepared.reuse.binding)

            # A removed cited file and an unavailable repository both fail closed.
            budget.unlink()
            with pytest.raises(ContextReuseError, match="source-changed") as removed:
                await scoped.validate_reuse(prepared.reuse.binding)
            assert removed.value.invalidation == _code_change("removed")
            budget.write_text(FILES["src/sample/budget.py"])
            root.rename(root.with_name("moved"))
            with pytest.raises(ContextReuseError, match="code-unavailable") as unavailable:
                await scoped.validate_reuse(prepared.reuse.binding)
            assert unavailable.value.invalidation is None
            root.with_name("moved").rename(root)

    asyncio.run(scenario())


def test_a_cited_file_that_leaves_the_git_inventory_stops_reuse(tmp_path):
    async def scenario():
        async with _code_runtime(tmp_path) as (scoped, root, fixture):
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            assert "src/sample/budget.py" in _cited_paths(prepared)
            # The bytes are unchanged, but the file is no longer tracked and is ignored, so it is not indexable.
            _git(root, "rm", "--cached", "-q", "src/sample/budget.py")
            (root / ".gitignore").write_text("src/sample/budget.py\n")
            await asyncio.to_thread(fixture.service.index, fixture.scope_id, full=True)
            with pytest.raises(ContextReuseError, match="source-changed") as removed:
                await scoped.validate_reuse(prepared.reuse.binding)
            assert removed.value.invalidation == _code_change("removed")

    asyncio.run(scenario())


def test_code_reuse_is_bound_to_its_scope(tmp_path):
    async def scenario():
        async with _code_runtime(tmp_path) as (scoped, _root, fixture):
            prepared = await scoped.prepare(_request())
            assert isinstance(prepared, PreparedContextWithReuse)
            assert _cited_paths(prepared)
            runtime = scoped._runtime
            other = await runtime.scopes.create(
                ScopeDraft(title="Other", summary="Another Scope", idempotency_key="code-reuse-other")
            )
            with pytest.raises(ContextBindingError):
                await runtime.context.for_scope(other.scope_id).validate_reuse(prepared.reuse.binding)
            assert fixture.scope_id != other.scope_id

    asyncio.run(scenario())


def _origin(scope_id: str, path: str) -> CodeEvidenceRef:
    return CodeEvidenceRef(
        scope_id=scope_id,
        fingerprint="fingerprint",
        path=path,
        file_sha256="0" * 64,
        start_line=1,
        end_line=1,
        snippet_sha256="1" * 64,
    )


@pytest.mark.parametrize(
    ("origins", "reason"),
    [
        ((_origin("other-scope", "a.py"),), "cross-scope"),
        (tuple(_origin("scope", f"f{index}.py") for index in range(MAX_REUSE_DEPENDENCIES + 1)), "dependency-limit"),
    ],
)
def test_code_dependencies_stay_in_scope_and_within_the_bound(origins, reason):
    build = replace(
        PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=()),
        code_origins=origins,
    )

    async def scenario():
        with pytest.raises(ContextReuseError, match=reason):
            await prepare_reuse(
                object(),  # ty: ignore[invalid-argument-type] the limit is checked before any read.
                "scope",
                build,
                build.context,
                codec=None,  # ty: ignore[invalid-argument-type] nothing is sealed when the limit fails.
                binding_identity="local",
                now=datetime.now(UTC),
                context=None,
            )

    asyncio.run(scenario())


class _Snapshot:
    """A Memory dependency snapshot with ancestors and Sources beyond its single selected root."""

    def __init__(self, records: int) -> None:
        self.records = records

    async def read_dependency_snapshot(self, roots, *, context=None, selected=None):
        return [{"kind": "artifact", "index": index} for index in range(self.records)]


def test_the_full_memory_snapshot_and_code_share_the_dependency_bound():
    build = replace(
        PreparedContextBuild(context=PreparedContext(status="empty", content=None, content_bytes=0), origins=()),
        code_origins=(_origin("scope", "a.py"), _origin("scope", "b.py")),
    )

    async def scenario():
        with pytest.raises(ContextReuseError, match="dependency-limit"):
            await prepare_reuse(
                _Snapshot(MAX_REUSE_DEPENDENCIES - 1),  # ty: ignore[invalid-argument-type] only the snapshot is read.
                "scope",
                build,
                build.context,
                codec=None,  # ty: ignore[invalid-argument-type] nothing is sealed when the limit fails.
                binding_identity="local",
                now=datetime.now(UTC),
                context=None,
            )

    asyncio.run(scenario())
