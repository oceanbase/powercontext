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

"""Public code service journeys using Git, the deployed engine, and real caches."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from powercontext.builtin.code.config import CodeConfig, CodeGraphConfig, CodeLimits
from powercontext.builtin.code.errors import CodeChangedError, CodeUnavailableError, InvalidCodeRequestError
from powercontext.builtin.code.models import (
    CodeAffectedTestsOperation,
    CodeImpactOperation,
    CodeQueryRequest,
    CodeQueryResponse,
    CodeReadOperation,
    CodeSymbolsOperation,
    CodeTargetOperation,
    CodeTreeOperation,
)
from powercontext.builtin.code.service import CodeService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def service(tmp_path: Path) -> CodeService:
    executable = os.environ.get("POWERCONTEXT_TEST_CODEGRAPH_EXECUTABLE")
    if not executable:
        pytest.skip("Set POWERCONTEXT_TEST_CODEGRAPH_EXECUTABLE for real engine acceptance")
    root = tmp_path / "repository"
    root.mkdir()
    sources = {
        "helpers.py": b"def choose_target(value):\r\n    return value + 1  # " + "中文".encode() + b"\r\n",
        "test_helpers.py": b"from helpers import choose_target\n\ndef test_root():\n    assert choose_target(1) == 2\n",
        "tests/nested/helpers_test.py": b"from helpers import choose_target\n\ndef test_nested():\n    assert choose_target(2) == 3\n",
    }
    for path, content in sources.items():
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    git = shutil.which("git")
    assert git
    for arguments in (
        ("init", "--quiet"),
        ("config", "user.name", "Acceptance"),
        ("config", "user.email", "acceptance@example.invalid"),
        ("add", "."),
        ("commit", "--quiet", "-m", "test fixture"),
    ):
        subprocess.run((git, "-C", str(root), *arguments), check=True, capture_output=True)
    return CodeService(
        CodeConfig(
            enabled=True,
            repositories={"alpha": root, "beta": root},
            provider=CodeGraphConfig(executable=executable),
            cache_dir=tmp_path / "cache",
        )
    )


async def test_real_index_query_read_and_current_content_invalidation(service: CodeService) -> None:
    assert (await service.status("alpha")).status == "missing"
    built = await service.index("alpha")
    assert built.status == "ready"
    assert (await service.status("beta")).status == "missing"
    request = CodeQueryRequest(operation=CodeSymbolsOperation(kind="symbols", query="choose_target"))
    symbols = await service.query("alpha", request)
    assert isinstance(symbols, CodeQueryResponse)
    assert symbols.fingerprint == built.fingerprint
    assert len(symbols.items) == 1
    symbol = symbols.items[0]
    assert symbol.path == "helpers.py"
    assert symbol.location is not None and symbol.location.start_line == 1
    assert symbol.file_sha256 is not None
    raw = (service.config.repositories["alpha"] / "helpers.py").read_bytes()
    snippet = await service.query(
        "alpha",
        CodeQueryRequest(
            expected_fingerprint=symbols.fingerprint,
            operation=CodeReadOperation(
                kind="read", path=symbol.path, file_sha256=symbol.file_sha256, start_line=1, end_line=2
            ),
        ),
    )
    assert isinstance(snippet, CodeQueryResponse)
    assert snippet.items[0].content == raw.decode()
    assert snippet.items[0].content_sha256 == hashlib.sha256(raw).hexdigest()
    for kind in ("callers", "impact"):
        related = await service.query(
            "alpha",
            CodeQueryRequest(
                expected_fingerprint=symbols.fingerprint,
                operation=(
                    CodeTargetOperation(kind="callers", path=symbol.path, qualified_name="choose_target", start_line=1)
                    if kind == "callers"
                    else CodeImpactOperation(
                        kind="impact", path=symbol.path, qualified_name="choose_target", start_line=1
                    )
                ),
            ),
        )
        assert isinstance(related, CodeQueryResponse)
        assert {item.path for item in related.items} == {"test_helpers.py", "tests/nested/helpers_test.py"}
        assert all(item.relationships[0].call_line == 4 for item in related.items)
    affected = await service.query(
        "alpha",
        CodeQueryRequest(
            expected_fingerprint=symbols.fingerprint,
            operation=CodeAffectedTestsOperation(kind="affected_tests", changed_paths=("helpers.py",)),
        ),
    )
    assert isinstance(affected, CodeQueryResponse)
    assert {item.path for item in affected.items} == {"test_helpers.py", "tests/nested/helpers_test.py"}
    prepared = await service.prepare("alpha", "choose_target")
    assert prepared.items[0].content == raw.decode()
    tree = await service.query("alpha", CodeQueryRequest(operation=CodeTreeOperation(kind="tree")))
    assert isinstance(tree, CodeQueryResponse)
    assert {item.path for item in tree.items} == {"helpers.py", "test_helpers.py", "tests", "tests/nested"}
    assert (await service.index("alpha")).fingerprint == built.fingerprint
    code = service.config.repositories["alpha"] / "helpers.py"
    code.write_bytes(raw.replace(b"value + 1", b"value + 2"))
    with pytest.raises(CodeChangedError, match="code_changed"):
        await service.query("alpha", request)
    assert (await service.status("alpha")).status == "stale"
    refreshed = await service.index("alpha")
    assert refreshed.fingerprint != built.fingerprint
    with pytest.raises(CodeChangedError):
        await service.query(
            "alpha", CodeQueryRequest(operation=request.operation, expected_fingerprint=symbols.fingerprint)
        )


async def test_scope_binding_and_revocation_survive_existing_cache(service: CodeService) -> None:
    alpha = await service.index("alpha")
    beta = await service.index("beta")
    assert alpha.fingerprint != beta.fingerprint
    revoked = CodeService(service.config.model_copy(update={"repositories": {}}))
    assert (await revoked.status("alpha")).status == "disabled"
    with pytest.raises(CodeUnavailableError, match="code_not_configured"):
        await revoked.prepare("alpha", "choose_target")
    with pytest.raises(InvalidCodeRequestError, match="budget_too_small"):
        await service.query(
            "alpha",
            CodeQueryRequest(operation=CodeSymbolsOperation(kind="symbols", query="choose_target"), max_bytes=512),
        )


async def test_interrupted_refresh_keeps_only_the_previous_complete_generation(
    service: CodeService, monkeypatch
) -> None:
    previous = await service.index("alpha")
    source = service.config.repositories["alpha"] / "helpers.py"
    original = source.read_bytes()
    source.write_bytes(original + b"\ndef added_after_refresh():\n    return 42\n")
    built = asyncio.Event()
    unpublished = asyncio.Event()
    real_build = service.adapter.build

    async def pause_before_publish(*args, **kwargs):
        result = await real_build(*args, **kwargs)
        built.set()
        await unpublished.wait()
        return result

    monkeypatch.setattr(service.adapter, "build", pause_before_publish)
    build = asyncio.create_task(service.index("alpha"))
    await asyncio.wait_for(built.wait(), timeout=20)
    assert (await service.status("alpha")).status == "building"
    build.cancel()
    with pytest.raises(asyncio.CancelledError):
        await build
    restarted = CodeService(service.config)
    assert (await restarted.status("alpha")).status == "stale"
    with pytest.raises(CodeChangedError):
        await restarted.prepare("alpha", "added_after_refresh")
    source.write_bytes(original)
    assert (await restarted.status("alpha")).fingerprint == previous.fingerprint
    source.write_bytes(original + b"\ndef added_after_refresh():\n    return 42\n")
    assert (await restarted.index("alpha")).fingerprint != previous.fingerprint
    result = await restarted.prepare("alpha", "added_after_refresh")
    assert result.items[0].content == "def added_after_refresh():\n    return 42\n"


async def test_concurrent_refresh_and_queries_observe_complete_results(service: CodeService) -> None:
    service = CodeService(service.config.model_copy(update={"limits": CodeLimits(query_timeout_seconds=20)}))
    first, second = await asyncio.gather(service.index("alpha"), service.index("alpha"))
    assert first.fingerprint == second.fingerprint
    request = CodeQueryRequest(operation=CodeSymbolsOperation(kind="symbols", query="choose_target"))
    results = await asyncio.gather(
        service.query("alpha", request), service.index("alpha"), service.query("alpha", request)
    )
    assert {result.fingerprint for result in results} == {first.fingerprint}
    for result in (results[0], results[2]):
        assert isinstance(result, CodeQueryResponse)
        assert result.items[0].path == "helpers.py"


async def test_change_after_real_engine_query_discards_all_code(service: CodeService, monkeypatch) -> None:
    await service.index("alpha")
    query = service.adapter.query

    async def edit_after_query(*args, **kwargs):
        result = await query(*args, **kwargs)
        (service.config.repositories["alpha"] / "helpers.py").write_text("def replacement():\n    return 99\n")
        return result

    monkeypatch.setattr(service.adapter, "query", edit_after_query)
    with pytest.raises(CodeChangedError):
        await service.prepare("alpha", "choose_target")


async def test_timeout_and_deleted_target_never_mean_no_impact(service: CodeService) -> None:
    indexed = await service.index("alpha")
    with pytest.raises(InvalidCodeRequestError, match="invalid_code_target"):
        await service.query(
            "alpha",
            CodeQueryRequest(
                operation=CodeAffectedTestsOperation(kind="affected_tests", changed_paths=("deleted.py",)),
                expected_fingerprint=indexed.fingerprint,
            ),
        )
    limited = CodeService(service.config.model_copy(update={"limits": CodeLimits(query_timeout_seconds=0.001)}))
    with pytest.raises(CodeUnavailableError, match="code_timeout"):
        await limited.prepare("alpha", "choose_target")
    assert (await service.status("alpha")).fingerprint == indexed.fingerprint
